import calendar
import multiprocessing
import os
import time
from pathlib import Path

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from crons import start_process_after_check
from mongo.client import MongoApi
from routers import pg_actors, mg_movies, pg_users
from sql.database import DatabaseApi
from threads.main import ThreadPool

app = FastAPI()
scheduler = AsyncIOScheduler()

origins = ["http://localhost", "https://localhost", "http://localhost:8000"]

app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", ],
                   allow_headers=["*"], )

app.include_router(pg_actors.router)
app.include_router(mg_movies.router)
app.include_router(pg_users.router)


@app.on_event("startup")
def startup_event() -> None:
    DatabaseApi.init_db()
    MongoApi.init_db()
    # ProcessPool.setup_pool(10)
    ThreadPool.setup_pool(multiprocessing.cpu_count() * 2)
    scheduler.add_job(start_process_after_check, "interval", seconds=5)
    scheduler.start()


@app.on_event("shutdown")
def shutdown_event():
    DatabaseApi.close()
    MongoApi.close()
    # ProcessPool.shutdown()
    ThreadPool.shutdown()
    scheduler.shutdown()


def get_log_folder() -> Path:
    utc_now = time.gmtime()
    hour = int(int((utc_now.tm_hour / 8)) * 8)
    final_struct = (utc_now.tm_year, utc_now.tm_mon, utc_now.tm_mday, hour, 0, 0, 0, 0, 0)
    hour_folder = str(calendar.timegm(time.struct_time(final_struct)))
    return Path(Path(os.getcwd()).parent, "logs", hour_folder).resolve()


if __name__ == "__main__":
    log_dir = get_log_folder()
    if not log_dir.exists():
        log_dir.mkdir(parents=True)
    uvicorn.run("__main__:app", host="0.0.0.0", port=8000, reload=False)
