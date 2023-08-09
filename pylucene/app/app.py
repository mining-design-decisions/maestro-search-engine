import lucene
import uvicorn
from fastapi import FastAPI
from java.nio.file import Paths
from org.apache.lucene.analysis.standard import StandardAnalyzer
from org.apache.lucene.document import Document, TextField, Field
from org.apache.lucene.index import (
    IndexWriter,
    IndexWriterConfig,
    DirectoryReader,
)
from org.apache.lucene.queryparser.classic import QueryParser
from org.apache.lucene.search import IndexSearcher
from org.apache.lucene.store import SimpleFSDirectory
from pydantic import BaseModel
import threading
import requests

from .config import SSL_KEYFILE, SSL_CERTFILE

app = FastAPI()
initialized_vms = {}


def init_vm():
    thread_id = threading.get_ident()
    if thread_id not in initialized_vms:
        lucene.initVM()
        initialized_vms[thread_id] = True


class Search(BaseModel):
    query: str
    # nullable
    existence: bool
    executive: bool
    property: bool


@app.post("/create-index")
def create_index():
    init_vm()
    index_directory = SimpleFSDirectory(Paths.get("/index/"))
    writer_config = IndexWriterConfig(StandardAnalyzer())
    writer = IndexWriter(index_directory, writer_config)

    url = "https://issues-db.nl:8000"
    domains = [
        "data storage & processing",
        "content management",
        "devops and cloud",
        "software development tools",
        "web development",
        "soa and middlewares",
    ]
    for domain in domains:
        payload = {"filter": {"tags": {"$eq": f"project-merged_domain={domain}"}}}
        issue_ids = requests.get(
            f"{url}/issue-ids",
            json=payload,
        ).json()
        predictions = requests.get(
            f"{url}/models/648ee4526b3fde4b1b33e099/versions/648f1f6f6b3fde4b1b3429cf/predictions",
            json=issue_ids,
        ).json()["predictions"]
        issue_ids["attributes"] = ["key", "summary", "description"]
        issue_data = requests.get(f"{url}/issue-data", json=issue_ids).json()["data"]

        for issue_id in issue_ids["issue_ids"]:
            doc = Document()
            doc.add(Field("issue_id", issue_id, TextField.TYPE_STORED))
            doc.add(
                Field("issue_key", issue_data[issue_id]["key"], TextField.TYPE_STORED)
            )
            text = ". ".join(
                [issue_data[issue_id]["summary"], issue_data[issue_id]["description"]]
            )
            doc.add(Field("text", text, TextField.TYPE_STORED))
            for class_ in ["existence", "property", "executive"]:
                doc.add(
                    Field(
                        class_,
                        str(predictions[issue_id][class_]["prediction"]),
                        TextField.TYPE_STORED,
                    )
                )
            writer.addDocument(doc)

    writer.close()


@app.get("/search")
def search(request: Search):
    init_vm()
    index_directory = SimpleFSDirectory(Paths.get("/index/"))
    reader = DirectoryReader.open(index_directory)
    searcher = IndexSearcher(reader)

    search_string = f"text: {request.query}"
    if request.executive is not None:
        search_string += " AND executive: " + str(request.executive)
    if request.existence is not None:
        search_string += " AND existence: " + str(request.existence)
    if request.property is not None:
        search_string += " AND property: " + str(request.property)
    
    query = QueryParser("text", StandardAnalyzer()).parse(search_string)
    hits = searcher.search(query, 10)

    response = []
    for hit in hits.scoreDocs:
        doc = searcher.doc(hit.doc)
        response.append(
            {
                "hit_score": hit.score,
                "issue_id": doc.get("issue_id").encode("utf-8"),
                "issue_key": doc.get("issue_key").encode("utf-8"),
                "text": doc.get("text").encode("utf-8"),
                "existence": doc.get("existence").encode("utf-8"),
                "property": doc.get("property").encode("utf-8"),
                "executive": doc.get("executive").encode("utf-8"),
            }
        )
    return response


def run_app():
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8042,
        ssl_keyfile=SSL_KEYFILE,
        ssl_certfile=SSL_CERTFILE,
    )
