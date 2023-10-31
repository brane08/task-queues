import logging
import os
from pymongo import MongoClient
from pymongo.database import Database
from typing import Optional

logger = logging.getLogger(__name__)


class MongoApi:
    __client: Optional[MongoClient] = None

    @classmethod
    def init_db(cls):
        if cls.__client is None:
            driver_url = os.environ.get("APP_MONGO_URL")
            cls.__client = MongoClient(driver_url, maxPoolSize=10)
            logger.info("I am connected!")
        return cls.__client

    @classmethod
    def client(cls) -> MongoClient:
        return cls.__client

    @classmethod
    def database(cls) -> Database:
        return cls.__client["sample_mflix"]

    @classmethod
    def close(cls):
        if cls.__client is not None:
            cls.__client.close()
