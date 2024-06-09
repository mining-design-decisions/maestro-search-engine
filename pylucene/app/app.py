import threading
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Json
import uvicorn

import lucene

from .adapter import IssueIndex, PredictionSelection, MissingPrediction

index = IssueIndex(loc='/index') # location for the repo containing indexes data 

app = FastAPI(root_path="/pylucene")
initialized_vms = {}

index_build_lock = threading.Lock()

class CreatTextIndex(BaseModel):
    database_url: str
    query: Json



def init_vm():
    thread_id = threading.get_ident()
    if thread_id not in initialized_vms:
        lucene.initVM()
        initialized_vms[thread_id] = True



class CreateIndex(BaseModel):
    database_url: str
    repos_and_projects: dict[str, list[str]]
    model_id: str | None
    version_id: str | None


class PredictionSpec(BaseModel):
    existence: Optional[bool]
    executive: Optional[bool]
    property: Optional[bool]


class Search(BaseModel):
    num_results: int
    repos_and_projects: dict[str, list[str]]
    query: str
    model_id: str | None
    version_id: str | None 
    predictions: PredictionSpec


@app.get('/index-status')
def get_index_status():
    init_vm()
    if index_build_lock.acquire(blocking=False):
        status = 'idle'
        index_build_lock.release()
    else:
        status = 'busy'
    return {
        'status': status,
        'indexes': index.indexes
    }


@app.post('/create-index')
def add_predictions_index(request: CreateIndex):
    init_vm()
    if not index_build_lock.acquire(blocking=False):
        return {'result': 'busy'}
    try:
        index.index_issues(
            database_url=request.database_url,
            model_id=request.model_id,
            version_id=request.version_id,
            projects_by_repo=request.repos_and_projects
        )
        return {'result': 'done'}
    except MissingPrediction as e:
        return {
            'result': 'missing-prediction',
            'payload': {
                'ident': e.ident,
                'key': e.ident
            }
        }
    except Exception as e:
        return {
            'result': 'unexpected-error',
            'payload': str(e)
        }
    finally:
        index_build_lock.release()


@app.post("/search")
def search(request: Search):
    init_vm()
    if not index_build_lock.acquire(blocking=False):
        return {'result': 'busy', 'payload': None}
    try:
        success, payload = index.search(
            text_query=request.query,
            model_id=request.model_id,
            version_id=request.version_id,
            projects_by_repo=request.repos_and_projects,
            predictions={
                'existence': _get_pred(request.predictions.existence),
                'executive': _get_pred(request.predictions.executive),
                'property': _get_pred(request.predictions.property),
            },
            num_items=request.num_results
        )
        if not success:
            return {'result': 'missing-indexes', 'payload': payload}
        return {'result': 'done', 'payload': payload}
    except Exception as e:
        return {
            'result': 'unexpected-error',
            'payload': str(e)
        }
    finally:
        index_build_lock.release()


def _get_pred(x):
    print(x)
    if x is None:
        return PredictionSelection.EITHER
    if x:
        return PredictionSelection.TRUE
    return PredictionSelection.FALSE


def run_app():
    uvicorn.run(
        "app.app:app",
        port=8043,
        host='0.0.0.0',
        reload=True
    )
