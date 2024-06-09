import enum
import json
import os
import shutil

import requests
import issue_db_api

import lucene
from java.nio.file import Paths
from org.apache.lucene.analysis.standard import StandardAnalyzer
from org.apache.lucene.document import Document, TextField, Field, StoredField
from org.apache.lucene.index import (
    IndexWriter,
    IndexWriterConfig,
    DirectoryReader,
    MultiReader,
)
from org.apache.lucene.queryparser.classic import QueryParser
from org.apache.lucene.search import IndexSearcher
from org.apache.lucene.store import SimpleFSDirectory


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

    def index_issues(self,
                     database_url,  
                     projects_by_repo: dict[str, list[str]], # projects name as repo
                     model_id: str | None = None, # machine learning model id that should be used for predictions
                     version_id: str | None = None): # version of that model
        # Retrieve data from API
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
        predictions = {}
        if model_id is not None:
            predictions = requests.get(f"http://100.64.15.32:8000/models/{model_id}/versions/{version_id}/predictions",
            # predictions = requests.get(f"http://172.30.0.1:8000/models/{model_id}/versions/{version_id}/predictions",
                json={
                    'issue_ids': [i.identifier for i in issues]
                })
            predictions = predictions.json()["predictions"]
            
        # Setup Lucene stuff
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
        
        issues_with_comments_count = 0
        # Store issues
        for issue in issues:
            if predictions.get(issue.identifier) == None:
                print("no prediction available")
                continue
            comments = ""
            try:
                if issue.comments:
                    comments = issue.comments
                    issues_with_comments_count += 1
            except:
                print("Catched PanicException:")
                
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
        return selected_index is not None, selected_index

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

        hits = searcher.search(query, num_items)

        response = []
        for hit in hits.scoreDocs:
            doc = searcher.doc(hit.doc)
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
                    "existence": doc.get("existence"),
                    "property": doc.get("property"),
                    "executive": doc.get("executive"),
                }
            )
        return True, response