from dependencies import get_m_db
from fastapi import APIRouter, Depends
from mongo import operations
from pymongo.database import Database
from responses import ApiResponse

router = APIRouter(prefix="/mg/movies", tags=["movies"])


@router.get("", response_model_exclude_unset=True)
def get_movies(db: Database = Depends(get_m_db), page: int = 1, size: int = 20):
    movies = operations.get_movies(db, page, size)
    return ApiResponse.respond_paged(page, size, operations.count_movies(db), movies)


@router.get("/{a_id}")
def get_actor(a_id: int):
    return {"success": True, "data": []}
