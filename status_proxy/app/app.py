import os
import fastapi
import httpx
import uvicorn
import asyncio

app = fastapi.FastAPI(root_path="/search-engine")
api_lock = asyncio.Lock()  # Use asyncio.Lock for async-safe locking

BACKEND_SERVER = os.environ['SE_BACKEND_SERVER']
ALLOW_UNSAFE_SSL = os.environ['SE_ALLOW_UNSAFE_SSL'].lower() == 'true'

@app.get('/index-status')
async def get_index_status(request: fastapi.Request):
    # Check lock status in an async-safe way
    if api_lock.locked():
        return 'busy'
    return 'idle'

@app.post('/create-index')
async def create_index(request: fastapi.Request):
    async with api_lock:  # Ensure lock is properly released
        async with httpx.AsyncClient(verify=not ALLOW_UNSAFE_SSL, timeout=None) as client:
            response = await client.post(f'{BACKEND_SERVER}/create-index',
                                         json=await request.json())
            if response.status_code != 200:
                raise fastapi.HTTPException(status_code=response.status_code, detail=response.json())
            return response.json()

@app.post("/search")
async def search(request: fastapi.Request):
    async with api_lock:  # Ensure lock is properly released
        async with httpx.AsyncClient(verify=not ALLOW_UNSAFE_SSL, timeout=None) as client:
            response = await client.post(f'{BACKEND_SERVER}/search',
                                         json=await request.json())
            if response.status_code != 200:
                raise fastapi.HTTPException(status_code=response.status_code, detail=response.json())
            return response.json()

def run_app():
    uvicorn.run(
        "app.app:app",
        port=8042,
        host='0.0.0.0',
        reload=False  # Disable reload for production
    )
