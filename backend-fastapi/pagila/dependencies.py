import mongo.client
from sql.database import DatabaseApi


async def get_db():
    db = DatabaseApi.session()
    try:
        yield db
    finally:
        db.close()


async def get_m_db():
    return mongo.client.MongoApi.database()
