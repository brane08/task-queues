from sql.database import Base
from sqlalchemy import Column, Integer, String, DateTime


class Actor(Base):
    __tablename__ = "actor"

    id = Column(Integer, primary_key=True, index=True, name="actor_id")
    first_name = Column(String, unique=True, index=True, name="first_name")
    last_name = Column(String, name="last_name")
    last_update = Column(DateTime, default=True, name="last_update")


class User(Base):
    __tablename__ = "users_t"

    id = Column(Integer, primary_key=True, index=True, name="user_id", autoincrement=True)
    user_name = Column(String, unique=True, index=True, name="user_name")
    password = Column(String, name="password_hash")
