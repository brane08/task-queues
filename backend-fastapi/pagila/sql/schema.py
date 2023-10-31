from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class Actor(BaseModel):
    id: int
    first_name: str
    last_name: str
    last_update: datetime

    class Config:
        from_attributes = True


class UserBase(BaseModel):
    user_id: Optional[int]
    user_name: str
    password: Optional[str]


class UserCreate(UserBase):
    pass
