import os

import fastapi
import httpx
import uvicorn
import threading

from .config import SSL_KEYFILE, SSL_CERTFILE

app = fastapi.FastAPI()
api_lock = threading.Lock()

BACKEND_SERVER = os.environ['SE_BACKEND_SERVER']

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
            return await httpx.post(f'{BACKEND_SERVER}/create-index', json=await request.json())
        finally:
            api_lock.release()


@app.post("/search")
async def search(request: fastapi.Request):
    if api_lock.acquire(blocking=False):
        try:
            return await httpx.post(f'{BACKEND_SERVER}/search', json=await request.json())
        finally:
            api_lock.release()


def run_app():
    uvicorn.run(
        app,
        port=8042,
        host='0.0.0.0',
        ssl_keyfile=SSL_KEYFILE,
        ssl_certfile=SSL_CERTFILE,
    )


