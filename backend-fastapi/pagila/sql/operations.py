import logging
from encrypt import Encryptor
from sql import models, schema
from sql.models import Actor, User
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List, Optional

logger = logging.getLogger(__name__)


def get_actors(db: Session, page: int, size: int) -> List[Actor]:
    return db.query(models.Actor).offset(((page - 1) * size)).limit(size).all()


def get_user(db: Session, user_name) -> Optional[schema.UserBase]:
    u = db.query(models.User).filter_by(user_name=user_name).first()
    return schema.UserBase(user_id=u.id, user_name=u.user_name)


def create_user(db: Session, user: schema.UserCreate) -> Optional[schema.UserBase]:
    try:
        u = User(user_name=user.user_name, password=Encryptor.sha384_hash(user.password))
        db.add(u)
        db.commit()
        logger.info("New ID:", u.id)
        return schema.UserBase(user_id=u.id, user_name=u.user_name)
    except IntegrityError as e:
        return None


def get_actor(db: Session, actor_id: int) -> Actor:
    return db.query(models.Actor).filter(models.Actor.id == actor_id).first()


def authenticate(db: Session, user: str, password: str) -> bool:
    sel = select(User.id).filter_by(user_name=user, password=password)
    data = db.execute(sel).first()
    return data is not None
