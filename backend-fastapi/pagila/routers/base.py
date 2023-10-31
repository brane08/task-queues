from fastapi import APIRouter

from main import app
from processes.main import ProcessPool
from threads.main import ThreadPool

router = APIRouter(prefix="/", tags=["proc thread"])


@app.get("/proc")
async def root(workers: int = 10):
    ProcessPool.submit(workers)
    return {"message": "Hello World"}


@app.get("/proc/futures", response_model=dict)
async def updates():
    return ProcessPool.get_status()


@app.post("/th")
async def root(workers: int = 10):
    ThreadPool.submit(workers)
    return {"message": "Hello World"}


@app.get("/th/futures", response_model=dict)
async def updates():
    return ThreadPool.get_status()
