import logging

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

logger = logging.getLogger(__name__)

Base = declarative_base()


class DatabaseApi:
    # SQLALCHEMY_DATABASE_URL = os.environ.get("APP_DATABASE_URL")
    SQLALCHEMY_DATABASE_URL = "postgresql+psycopg2://postgres:@localhost:5432/pagila"
    __engine: Engine = None
    __session_local: sessionmaker = None

    @classmethod
    def init_db(cls):
        logger.info("This should init once!")
        cls.__engine = create_engine(cls.SQLALCHEMY_DATABASE_URL, isolation_level="SERIALIZABLE", echo=True,
                                     future=True)
        cls.__session_local = sessionmaker(autocommit=False, autoflush=False, bind=cls.__engine)
        cls.__base = declarative_base()

    @classmethod
    def engine(cls):
        return cls.__engine

    @classmethod
    def session(cls) -> Session:
        return cls.__session_local()

    @classmethod
    def close(cls):
        if cls.__engine is not None:
            cls.__engine.dispose()
