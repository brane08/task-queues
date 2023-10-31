from dependencies import get_db
from fastapi import APIRouter, Depends
from responses import ApiResponse
from sql import operations, models
from sql.models import Actor
from sqlalchemy.orm import Session

router = APIRouter(prefix="/pg/actors", tags=["actors"], )


@router.get("", response_model_exclude_unset=True, response_model=dict)
def get_actors(db: Session = Depends(get_db), page: int = 1, size: int = 20):
    actors = operations.get_actors(db, page, size)
    actor_list = list()
    for act in actors:
        actor_list.append(sql_to_schema(act))
    return ApiResponse.respond(list(actor_list))


@router.get("/{id}")
def get_actor(id: int):
    return {"success": True, "data": []}


def sql_to_schema(actor: models.Actor):
    return Actor(id=actor.id, first_name=actor.first_name, last_name=actor.last_name,
                 last_update=actor.last_update)
