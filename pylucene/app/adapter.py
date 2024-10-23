import enum
import json
import os
import shutil

import requests
import issue_db_api
import psycopg2
import numpy as np

from pymongo import MongoClient
from bson.objectid import ObjectId

import lucene
from java.nio.file import Paths
from org.apache.lucene.analysis.standard import StandardAnalyzer
from org.apache.lucene.document import Document, TextField, Field, StoredField, FloatPoint
from org.apache.lucene.index import (
    IndexWriter,
    IndexWriterConfig,
    DirectoryReader,
    MultiReader,
)
from org.apache.lucene.queryparser.classic import QueryParser
from org.apache.lucene.search import IndexSearcher
from org.apache.lucene.store import SimpleFSDirectory

IP_ADDRESS = "100.65.2.177"

# Function to get attachments by issue ID from Jira API
def get_attachments_by_id(issue_id: str):
    try:
        url = f"https://issues.apache.org/jira/rest/api/2/issue/{issue_id}"
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for HTTP errors
        data = response.json()
        return data.get("fields", {}).get("attachment", [])
    except requests.exceptions.RequestException as e:
        print(f"Error fetching attachments: {e}")
        return []

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
        self.w_prop = 1


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
    def _get_index_key(database_url: str,
                       projects_by_repo: dict[str, list[str]],
                       model_id: str | None,
                       version_id: str | None) -> str:
        key = (
            database_url,
            model_id,
            version_id,
            tuple((key, tuple(value)) for key, value in projects_by_repo.items())
        )
        return str(hash(key))
    
    

    def get_attachments_by_ids(self, db_name, collection_name, id_list):
        # Initialize MongoDB client and access the database and collection
        client = MongoClient(f'mongodb://{IP_ADDRESS}:27017/')  # Adjust the URI if necessary
        db = client[db_name]
        collection = db[collection_name]
        
        print(id_list)

        # Convert list of ids to ObjectId if needed
        # object_ids = [ObjectId(id_str) for id_str in id_list]

        # MongoDB query to fetch attachments for the given ids
        result = collection.find(
            { "id": { "$in": id_list } },
            { "fields.attachment": 1,"id":1 }
        )

        # Create a mapping of id -> attachments
        id_attachment_map = {}
        for doc in result:
            doc_id = str(doc['id'])  # Convert ObjectId to string for easier handling
            attachments = doc.get('fields', {}).get('attachment', [])
            id_attachment_map[doc_id] = attachments

        return id_attachment_map

    

    
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
        
        print("worked till here 3")
        
        
        predictions = {}
        if model_id is not None:
            
            # predictions = requests.get(f"http://100.65.2.177:8000/models/{model_id}/versions/{version_id}/predictions",
            predictions = requests.get(f"http://{IP_ADDRESS}:8000/models/{model_id}/versions/{version_id}/predictions",
            # predictions = requests.get(f"http://172.30.0.1:8000/models/{model_id}/versions/{version_id}/predictions",
                json={
                    'issue_ids': [i.identifier for i in issues]
                })
            predictions = predictions.json()["predictions"]
            
        # Example usage:
        db_name = 'JiraRepos'
        collection_name = 'Apache'
        id_list = [str(i.identifier).replace("Apache-","") for i in issues]

        attachments = self.get_attachments_by_ids(db_name, collection_name, id_list)
        print("attachments:" + attachments)
        # for issue in issues:
        #     try:
        #         attributes = dir(issue)
        #         print(attributes)
        #         if issue.attachment:
        #             print(issue.attachment)
        #             break
        #         else:
        #             print("no attatment")
        #     except:
        #         continue
        #             comments = issue.comments
        #             issues_with_comments_count += 1
            # print("worked till here 2",predictions)
            
        # for i in predictions:
        #     print(predictions[i])
        #     break
        # # return predictions
        # # Setup Lucene stuff
        # key = self._get_index_key(database_url, projects_by_repo, model_id, version_id)
        # path = os.path.join(self._index_dir, key)
        # if key in self._metadata['indexes']:
        #     shutil.rmtree(path)
        # else:
        #     self._metadata['indexes'][key] = {
        #         'database-url': database_url,
        #         'included-projects': projects_by_repo,
        #         'model': {
        #             'id': model_id,
        #             'version': version_id
        #         }
        #     }
        # os.makedirs(path, exist_ok=True)
        # index_directory = SimpleFSDirectory(Paths.get(path))
        # writer_config = IndexWriterConfig(StandardAnalyzer())
        # writer = IndexWriter(index_directory, writer_config)
        
        # issues_with_comments_count = 0
        # # Store issues
        # for issue in issues:
        #     if predictions.get(issue.identifier) == None:
        #         print("no prediction available")
        #         continue
        #     comments = ""
        #     try:
        #         if issue.comments:
        #             comments = issue.comments
        #             issues_with_comments_count += 1
        #     except:
        #         print("Catched PanicException:")
                
        #     doc = Document()
        #     #doc.add(SortedDocValuesField('id', BytesRef(issue.identifier)))
        #     doc.add(Field('id', issue.identifier, TextField.TYPE_STORED))
        #     doc.add(Field('project', issue.key.split('-')[0], TextField.TYPE_STORED))
        #     doc.add(Field('key', issue.key, StoredField.TYPE))
        #     doc.add(Field('summary', issue.summary, StoredField.TYPE))
        #     doc.add(Field('description', issue.description, StoredField.TYPE))
        #     doc.add(Field('text', f'{issue.summary}. {issue.description}.{comments}', TextField.TYPE_STORED))
        #     doc.add(Field('comments',f'{comments}', TextField.TYPE_STORED))
        #     if model_id is not None:
        #         try:
        #             classes = predictions[issue.identifier]
        #         except KeyError:
        #             print(f"missingPredictions, {issue.identifier}, {issue.key}")
        #         for cls in ['existence', 'property', 'executive']:
        #             # print(str(classes[cls]['prediction']).lower())
        #             doc.add(Field(cls, str(classes[cls]['prediction']).lower(), TextField.TYPE_STORED))
        #             # print(classes[cls]["confidence"])
        #             doc.add(StoredField(cls+ "_confidence",classes[cls]["confidence"]))

        #     writer.addDocument(doc)

        # writer.close()
        # self._store_metadata()

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

    

    def get_comments(self,issue_ids,cursor):
        if not issue_ids:
            return {}

        try:
            query = (
                "SELECT id, issue_id, author_name, author_display_name, body "
                "FROM issues_comments WHERE issue_id = ANY(%s) ORDER BY id"
            )
            
            query = (
                "SELECT ic.id AS id, ic.issue_id as issue_id, ic.author_name as author_name, ic.author_display_name as author_display_name, ic.body, cr.classification_result "
                "FROM issues_comments ic "
                "LEFT JOIN classification_results cr ON ic.id = cr.issue_comment_id "
                "WHERE " 
                "LENGTH(ic.body) > 200 " 
                "AND ic.issue_id = ANY(%s) "
                "ORDER BY ic.id;"
            )
            cursor.execute(query, (issue_ids,))
            comments = cursor.fetchall()
            # print(comments[0])
            
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
        for cls, selector in predictions.items():
            match selector:
                case PredictionSelection.TRUE:
                    parts.append(f'{cls}: true')
                case PredictionSelection.FALSE:
                    parts.append(f'{cls}: false')
                case _:
                    pass

        query = QueryParser('text', StandardAnalyzer()).parse(
            ' AND '.join(parts)
        )

        hits = searcher.search(query, num_items +100)
        
        # Database connection parameters
        DB_NAME = 'issues'
        DB_USER = 'postgres'
        DB_PASSWORD = 'pass'
        DB_HOST = IP_ADDRESS
        DB_PORT = '5432'
        
        # Connect to the database
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        cursor = conn.cursor()
        issue_ids = [searcher.doc(hit.doc).get("key") for hit in hits.scoreDocs]
        
        with conn:
            with conn.cursor() as cursor:
                comments = self.get_comments(issue_ids, cursor)
                # Example usage:
                db_name = 'JiraRepos'
                collection_name = 'Apache'
                id_list = [str(searcher.doc(hit.doc).get("id")).replace("Apache-","") for hit in hits.scoreDocs]
                attachments = self.get_attachments_by_ids(db_name, collection_name, id_list)
                print(attachments)

        response = []
        for hit in hits.scoreDocs:
            doc = searcher.doc(hit.doc)
            # comments = self.getComments(doc.get("key"))
            
            # Fetch attachments using the helper method
            attachments = get_attachments_by_id(str(doc.get("id")).replace("Apache-",""))
            issue_id = doc.get("key")
            response.append(
                {
                    "hit_score": hit.score,
                    # "issue_id": doc.get("issue_id").encode("utf-8"),
                    # "issue_key": doc.get("issue_key").encode("utf-8"),
                    # "summary": doc.get("summary").encode("utf-8"),
                    # "description": doc.get("description").encode('utf8'),
                    # "existence": doc.get("existence").encode("utf-8"),
                    # "property": doc.get("property").encode("utf-8"),
                    # "executive": doc.get("executive").encode("utf-8"),
                    "issue_id": doc.get("id"),
                    "issue_key": doc.get("key"),
                    "summary": doc.get("summary"),
                    "description": doc.get("description"),
                    "comments": doc.get("comments"),
                    "comment": comments.get(issue_id, []),
                    "existence": doc.get("existence"),
                    "existence_confidence": doc.get("existence_confidence"),
                    "property": doc.get("property"),
                    "property_confidence": doc.get("property_confidence"),
                    "executive": doc.get("executive"),
                    "executive_confidence": doc.get("executive_confidence"),
                    # Attachments array fetched from the Jira API
                    "attachments": attachments
                    
                }
            )
            # Close the cursor and connection
        cursor.close()
        conn.close()    
        # Rerank the response before returning
        # response = self.rerank_issues(response)

        # Print the re-ranked response
        # self.print_issues(response)

        return True, response[0:10]
            
    def calculate_new_score(self, issue, max_hit_score,):
        # Normalize hit score
        s = issue['hit_score'] / max_hit_score if max_hit_score != 0 else 0
        
        ext = float(issue['existence_confidence'])
        exe = float(issue['executive_confidence'])
        prop = float(issue['property_confidence'])
        
        
        
        # Initialize comment confidences to empty lists
        ext_C_values = []
        exe_C_values = []
        prop_C_values = []
        
        comment_count = 0
        if issue['comment']:
            
            # Extract comment confidences and ignore None values
            for comment in issue['comment']:
                comment_count = comment_count +1
                if comment[5] is not None:
                    comment_confidences = comment[5]
                    if comment_confidences['existence']['confidence'] is not None:
                        ext_C_values.append(comment_confidences['existence']['confidence'])
                    if comment_confidences['executive']['confidence'] is not None:
                        exe_C_values.append(comment_confidences['executive']['confidence'])
                    if comment_confidences['property']['confidence'] is not None:
                        prop_C_values.append(comment_confidences['property']['confidence'])
        
        # Calculate average confidences for comments, default to 0 if no valid values
        ext_C = np.mean(ext_C_values) if ext_C_values else 0
        exe_C = np.mean(exe_C_values) if exe_C_values else 0
        prop_C = np.mean(prop_C_values) if prop_C_values else 0
        
        # Select the weights for the 
        key = str(self.w_exe) +str(self.w_ext) + str(self.w_prop)
        weightsDict = {
        "100": [0.67,0.33,0],
        "010": [0.1,0.85,0.14],
        "001": [0,.78,.22]
        }
        w_exec_c,w_ext_c,w_prop_c = weightsDict[key]


        # Normalize issue weights
        total_issue_weight = self.w_exe + self.w_ext + self.w_prop
        w_exe_normalized = self.w_exe / total_issue_weight
        w_ext_normalized = self.w_ext / total_issue_weight
        w_prop_normalized = self.w_prop / total_issue_weight
        
        n = comment_count

        # Calculate the new score
        new_score = (
            self.w_s * s +
            (1-self.w_s)*((np.log(4)/(np.log(4)+np.log(n +1))) *((w_exe_normalized * exe + w_ext_normalized * ext + w_prop_normalized * prop)) +
            (np.log(n+1) /(np.log(4)+np.log(n+1)))* ((w_exec_c * exe_C + w_ext_c * ext_C + w_prop_c * prop_C)))
        )
        
        return new_score

    def rerank_issues(self, issues):
        print("reranking issues _-------------------")
        # Find the maximum hit score
        max_hit_score = max(issue['hit_score'] for issue in issues)
        
        # Calculate the new score for each issue
        for issue in issues:
            issue['new_score'] = self.calculate_new_score(issue, max_hit_score)
            
        
        # print(issues[0])
        # Sort issues by 'new_score' in descending order
        reranked_issues = sorted(issues, key=lambda x: x['new_score'], reverse=True)
        return reranked_issues