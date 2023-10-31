from fastapi import APIRouter

router = APIRouter(prefix="/pg/films", tags=["films"])


@router.get("")
def get_films():
    return {"success": True, "data": []}
