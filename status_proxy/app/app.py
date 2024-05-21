import os

import fastapi
import httpx
import uvicorn
import threading


app = fastapi.FastAPI(root_path="/search-engine")
api_lock = threading.Lock()

BACKEND_SERVER = os.environ['SE_BACKEND_SERVER']
ALLOW_UNSAFE_SSL = os.environ['SE_ALLOW_UNSAFE_SSL'].lower() == 'true'

@app.get('/index-status')
async def get_index_status(request: fastapi.Request):
    if api_lock.acquire(blocking=False):
        status = 'idle'
        api_lock.release()
    else:
        status = 'busy'
    return status

@app.post('/create-index')
async def create_index(request: fastapi.Request):
    if api_lock.acquire(blocking=False):
        try:
            # return await request.json()
            async with httpx.AsyncClient(verify=not ALLOW_UNSAFE_SSL, timeout=None) as client:
                response = await client.post(f'{BACKEND_SERVER}/create-index',
                                             json=await request.json())
                if response.status_code != 200:
                    raise fastapi.HTTPException(status_code=response.status_code, detail=response.json())
                return response.json()
        finally:
            api_lock.release()


@app.post("/search")
async def search(request: fastapi.Request):
    if api_lock.acquire(blocking=False):
        try:
            async with httpx.AsyncClient(verify=not ALLOW_UNSAFE_SSL, timeout=None) as client:
                response = await client.post(f'{BACKEND_SERVER}/search',
                                             json=await request.json())
                if response.status_code != 200:
                    raise fastapi.HTTPException(status_code=response.status_code, detail=response.json())
                return response.json()
        finally:
            api_lock.release()


def run_app():
    uvicorn.run(
        "app.app:app",
        port=8042,
        host='0.0.0.0',
        reload=True
    )
