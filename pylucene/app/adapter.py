import enum
import json
import os
import shutil
import concurrent.futures

import requests
import issue_db_api
import psycopg2
import numpy as np

from java.nio.file import Paths
from org.apache.lucene.analysis.standard import StandardAnalyzer
from org.apache.lucene.document import Document, TextField, Field, StoredField
from org.apache.lucene.index import IndexWriter, IndexWriterConfig, DirectoryReader
from org.apache.lucene.queryparser.classic import QueryParser
from org.apache.lucene.search import IndexSearcher
from org.apache.lucene.store import SimpleFSDirectory

IP_ADDRESS = "131.234.28.135"

# Database connection parameters
DB_NAME = 'issues'
DB_USER = 'postgres'
DB_PASSWORD = 'pass'
DB_HOST = IP_ADDRESS
DB_PORT = '5432'

# Function to get attachments by issue ID from Jira API (Parallelized)
def get_attachments_for_issues(issue_ids):
    def get_attachments(issue_id):
        try:
            url = f"https://issues.apache.org/jira/rest/api/2/issue/{issue_id}"
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            return issue_id, data.get("fields", {}).get("attachment", [])
        except requests.exceptions.RequestException as e:
            print(f"Error fetching attachments for {issue_id}: {e}")
            return issue_id, []

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_issue = {executor.submit(get_attachments, issue_id): issue_id for issue_id in issue_ids}
        return {future.result()[0]: future.result()[1] for future in concurrent.futures.as_completed(future_to_issue)}

class MissingPrediction(Exception):
    def __init__(self, ident, key):
        super().__init__(f'Missing prediction for issue {ident} ({key})')
        self.ident = ident
        self.key = key

class PredictionSelection(enum.Enum):
    TRUE = enum.auto()
    FALSE = enum.auto()
    EITHER = enum.auto()

class IssueIndex:
    def __init__(self, loc: str):
        self._base_dir = loc
        self._metadata_file = os.path.join(self._base_dir, 'index_data.json')
        self._index_dir = os.path.join(self._base_dir, 'index')
        self._metadata = self._load_metadata()
        
        self.w_s = 0.5
        self.w_issues = 0.3
        self.w_comments = 0.2
        self.w_exe = 0
        self.w_ext = 0
        self.w_prop = 0
        self.comments_per_issue= 5;

    def _load_metadata(self):
        if not os.path.exists(self._metadata_file):
            with open(self._metadata_file, 'w') as file:
                json.dump({'indexes': {}}, file)
        with open(self._metadata_file) as file:
            return json.load(file)

    def _store_metadata(self):
        with open(self._metadata_file, 'w') as file:
            json.dump(self._metadata, file)

    @property
    def indexes(self) -> list[str]:
        return list(self._metadata['indexes'])

    @staticmethod
    def _get_index_key(database_url: str, projects_by_repo: dict[str, list[str]], model_id: str | None, version_id: str | None) -> str:
        key = (
            database_url,
            model_id,
            version_id,
            tuple((key, tuple(value)) for key, value in projects_by_repo.items())
        )
        return str(hash(key))


    
    def index_issues(self,
                     database_url,  
                     projects_by_repo: dict[str, list[str]], # projects name as repo
                     model_id: str | None = None, # machine learning model id that should be used for predictions
                     version_id: str | None = None): # version of that model
        # Retrieve data from API
        database_url = f"https://{IP_ADDRESS}:4269/issues-db-api"
        
        repo = issue_db_api.IssueRepository(
            database_url,
            allow_self_signed_certificates=os.environ['SE_ALLOW_UNSAFE_SSL'].lower() == 'true'
        )
        query = issue_db_api.Query().lor(
            *(
                issue_db_api.Query().tag(f'{jira_repo}-{project}')
                for jira_repo, projects in projects_by_repo.items()
                for project in projects
            )
        )
        issues = repo.search(
            query,
            attributes=['key', 'summary', 'description']
        )
        
        # Fetch predictions if model_id is provided
        predictions = {}
        if model_id is not None:
            
            # predictions = requests.get(f"http://100.65.2.177:8000/models/{model_id}/versions/{version_id}/predictions",
            predictions = requests.get(f"http://{IP_ADDRESS}:8000/models/{model_id}/versions/{version_id}/predictions",
            # predictions = requests.get(f"https://{IP_ADDRESS}:4269/issues-db-api/models/{model_id}/versions/{version_id}/predictions",
            
            # predictions = requests.get(f"http://172.30.0.1:8000/models/{model_id}/versions/{version_id}/predictions",
                json={
                    'issue_ids': [i.identifier for i in issues]
                },
                verify=False)
            predictions = predictions.json()["predictions"]
        
        # Connect to the database
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        
        cursor = conn.cursor()
        issue_ids = [i.key for i in issues]
        
        with conn:
            with conn.cursor() as cursor:
                allComments = self.get_comments(issue_ids, cursor)
        
        
        # Setup Lucene index
        key = self._get_index_key(database_url, projects_by_repo, model_id, version_id)
        path = os.path.join(self._index_dir, key)
        if key in self._metadata['indexes']:
            shutil.rmtree(path)
        else:
            self._metadata['indexes'][key] = {
                'database-url': database_url,
                'included-projects': projects_by_repo,
                'model': {
                    'id': model_id,
                    'version': version_id
                }
            }
        os.makedirs(path, exist_ok=True)
        index_directory = SimpleFSDirectory(Paths.get(path))
        writer_config = IndexWriterConfig(StandardAnalyzer())
        writer = IndexWriter(index_directory, writer_config)
        
        # Store issues
        for issue in issues:
            if predictions.get(issue.identifier) == None:
                print("no prediction available")
                continue
            
            comments = "".join(str(comment[4]) for comment in allComments.get(issue.key,[]))

            doc = Document()
            #doc.add(SortedDocValuesField('id', BytesRef(issue.identifier)))
            doc.add(Field('id', issue.identifier, TextField.TYPE_STORED))
            doc.add(Field('project', issue.key.split('-')[0], TextField.TYPE_STORED))
            doc.add(Field('key', issue.key, StoredField.TYPE))
            doc.add(Field('summary', issue.summary, StoredField.TYPE))
            doc.add(Field('description', issue.description, StoredField.TYPE))
            
            doc.add(Field('text', f'{issue.summary}. {issue.description}.{comments}', TextField.TYPE_STORED))
            doc.add(Field('comments',f'{comments}', TextField.TYPE_STORED))
            if model_id is not None:
                try:
                    classes = predictions[issue.identifier]
                except KeyError:
                    print(f"missingPredictions, {issue.identifier}, {issue.key}")
                for cls in ['existence', 'property', 'executive']:
                    # print(str(classes[cls]['prediction']).lower())
                    doc.add(Field(cls, str(classes[cls]['prediction']).lower(), TextField.TYPE_STORED))
                    # print(classes[cls]["confidence"])
                    doc.add(StoredField(cls+ "_confidence",classes[cls]["confidence"]))

            writer.addDocument(doc)

        writer.close()
        self._store_metadata()

    def check_have_index(self,
                         projects_by_repo: dict[str, list[str]],
                         model_id: str,
                         version_id: str) -> tuple[bool, None | str]:
        selected_index = None
        for index, data in self._metadata['indexes'].items():
            if data['model']['id'] != model_id or data['model']['version'] != version_id:
                if model_id is not None and version_id is not None:
                    continue
            for jira_repo, projects in projects_by_repo.items():
                for project in projects:
                    if jira_repo not in data['included-projects']:
                        break 
                    if project not in data['included-projects'][jira_repo]:
                        break
                else:
                    continue
                break
            else:
                selected_index = index
                break
        else:
            return False, None
        print(selected_index)
        return selected_index is not None, selected_index

    def get_comments(self, issue_ids, cursor):
        if not issue_ids:
            return {}

        try:
            query = (
                "SELECT ic.id AS id, ic.issue_id as issue_id, ic.author_name as author_name, ic.author_display_name as author_display_name, ic.body, cr.classification_result "
                "FROM issues_comments ic "
                "LEFT JOIN classification_results cr ON ic.id = cr.issue_comment_id "
                "WHERE LENGTH(ic.body) > 200 AND ic.is_bot = false AND ic.issue_id = ANY(%s) "
                "ORDER BY ic.id;"
            )
            cursor.execute(query, (issue_ids,))
            comments = cursor.fetchall()
        except Exception as e:
            print(e)
            return {}

        comments_dict = {}
        for comment in comments:
            comments_dict.setdefault(comment[1], []).append(comment)
        return comments_dict

    def search(self,
               text_query,
               projects_by_repo: dict[str, list[str]],
               model_id: str,
               version_id: str,
               predictions: dict[str, PredictionSelection],
               num_items: int):
        # Find a suitable index
        have_index, index = self.check_have_index(projects_by_repo, model_id, version_id)
        if not have_index:
            return False, 'No suitable index was found'

        # Lucene setup
        path = Paths.get(os.path.join(self._index_dir, index))
        index_directory = SimpleFSDirectory(path)
        reader = DirectoryReader.open(index_directory)
        searcher = IndexSearcher(reader)

        # Build query
        parts = [f'text: {text_query}']
        
        query = QueryParser('text', StandardAnalyzer()).parse(
            ' AND '.join(parts)
        )

        hits = searcher.search(query, num_items +100)
        
        
        print("has been hits",len(hits.scoreDocs))
        if len(hits.scoreDocs) == 0:
            return True,[]
                
        # Connect to the database
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host="131.234.28.135",
            port=DB_PORT
        )
        issue_ids = [searcher.doc(hit.doc).get("key") for hit in hits.scoreDocs]
        
        with conn:
            with conn.cursor() as cursor:
                comments = self.get_comments(issue_ids, cursor)

        # Fetch attachments in parallel
        attachments = get_attachments_for_issues(issue_ids)

        # Prepare response
        response = []
        for hit in hits.scoreDocs:
            doc = searcher.doc(hit.doc)
            issue_id = doc.get("key")
            response.append(
                {
                    "hit_score": hit.score,
                    "issue_id": doc.get("id"),
                    "issue_key": doc.get("key"),
                    "summary": doc.get("summary"),
                    "description": doc.get("description"),
                    "comments": comments.get(issue_id, []),
                    "existence": doc.get("existence"),
                    "existence_confidence": doc.get("existence_confidence"),
                    "property": doc.get("property"),
                    "property_confidence": doc.get("property_confidence"),
                    "executive": doc.get("executive"),
                    "executive_confidence": doc.get("executive_confidence"),
                    "attachments": attachments.get(issue_id, [])
                }
            )
        
        # Close connection
        conn.close()
        
        if(predictions["existence"]!= PredictionSelection.EITHER or predictions["executive"]!= PredictionSelection.EITHER  or predictions["property"]!= PredictionSelection.EITHER):
            self.w_ext = 1 if predictions["existence"] == PredictionSelection.TRUE else 0
            self.w_exe = 1 if predictions["executive"] == PredictionSelection.TRUE else 0
            self.w_prop = 1 if predictions["property"] == PredictionSelection.TRUE else 0
            response = self.rerank_issues(response)

        return True, response[:num_items]

    def calculate_new_score(self, issue, max_hit_score):
        # Normalize hit score
        s = issue['hit_score'] / max_hit_score if max_hit_score != 0 else 0

        ext = float(issue.get('existence_confidence', 0))
        exe = float(issue.get('executive_confidence', 0))
        prop = float(issue.get('property_confidence', 0))

        # Extract comment confidences
        ext_C_values = [
            comment.get('existence', {}).get('confidence', 0) 
            for comment in issue['comments'] 
            if isinstance(comment, dict) and 'existence' in comment
        ]
        exe_C_values = [
            comment.get('executive', {}).get('confidence', 0) 
            for comment in issue['comments'] 
            if isinstance(comment, dict) and 'executive' in comment
        ]
        prop_C_values = [
            comment.get('property', {}).get('confidence', 0) 
            for comment in issue['comments'] 
            if isinstance(comment, dict) and 'property' in comment
        ]

        # Calculate average confidences for comments
        ext_C = np.mean(ext_C_values) if ext_C_values else 0
        exe_C = np.mean(exe_C_values) if exe_C_values else 0
        prop_C = np.mean(prop_C_values) if prop_C_values else 0

        # Select the weights for the score calculation
        key = str(self.w_exe) + str(self.w_ext) + str(self.w_prop)
        weightsDict = {
            "110": [0.5, 0.34, 0.16],
            "111": [0.333, 0.333, 0.333],
            "100": [0.66, 0.33, 0],
            "101": [0.33, 0.66, 0],
            "011": [0.01, 0.85, 0.14],
            "010": [0.03, 0.85, 0.12],
            "000": [0.04, 0.71, 0.25],
            "001": [0, 0.79, 0.21]
        }
        w_exec_c, w_ext_c, w_prop_c = weightsDict.get(key, [0.33, 0.33, 0.33])

        # Normalize issue weights
        total_issue_weight = self.w_exe + self.w_ext + self.w_prop
        w_exe_normalized = self.w_exe / total_issue_weight if total_issue_weight != 0 else 0
        w_ext_normalized = self.w_ext / total_issue_weight if total_issue_weight != 0 else 0
        w_prop_normalized = self.w_prop / total_issue_weight if total_issue_weight != 0 else 0

        n = len(issue['comments'])

        # Calculate the new score
        new_score = (
            self.w_s * s +
            (1 - self.w_s) * (
                (np.log(self.comments_per_issue) / (np.log(self.comments_per_issue) + np.log(n + 1))) * ((w_exe_normalized * exe + w_ext_normalized * ext + w_prop_normalized * prop)) +
                (np.log(n + 1) / (np.log(self.comments_per_issue) + np.log(n + 1))) * ((w_exec_c * exe_C + w_ext_c * ext_C + w_prop_c * prop_C))
            )
        )

        return new_score

    def rerank_issues(self, issues):
        max_hit_score = max(issue['hit_score'] for issue in issues)
        for issue in issues:
            issue['hit_score'] = self.calculate_new_score(issue, max_hit_score)
        return sorted(issues, key=lambda x: x['hit_score'], reverse=True)