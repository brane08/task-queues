import base64
import logging
from dependencies import get_db
from encrypt import Encryptor
from fastapi import APIRouter, Depends, Request, HTTPException, status
from sql import operations, schema
from sql.schema import UserBase
from sqlalchemy.orm import Session
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pg/users", tags=["actors"], )


def verify_token(req: Request, db: Session = Depends(get_db)) -> str:
    token = req.headers.get("authorization", "")
    tokens = base64.b64decode(token.encode()).decode().split(":")
    if len(tokens) != 3:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password",
                            headers={"WWW-Authenticate": "Basic"})
    success = operations.authenticate(db, tokens[0], Encryptor.sha384_hash(tokens[1]))
    if not success:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password",
                            headers={"WWW-Authenticate": "Basic"})
    return tokens[0]


@router.get("/@self", response_model_exclude_unset=True, response_model=schema.UserBase)
def get_user(db: Session = Depends(get_db), auth_user: str = Depends(verify_token)):
    user = operations.get_user(db, auth_user)
    return user


#
@router.post("", response_model_exclude_unset=True, status_code=201, response_model=schema.UserBase)
def create_user(user: schema.UserCreate, db: Session = Depends(get_db), auth_user: str = Depends(verify_token)) -> \
    Optional[UserBase]:
    logger.info("Who is logged in:", auth_user)
    return operations.create_user(db, user)
