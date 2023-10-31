from pydantic import BaseModel
from typing import Optional


class ApiResponse:

    @staticmethod
    def __respond(success: bool = True, message: Optional[str] = None, page: Optional[int] = None,
                  size: Optional[int] = None, total: Optional[int] = None, data: Optional[BaseModel] = None) -> dict:
        return dict(success=success, message=message, page=page, size=size, total=total, data=data)

    @staticmethod
    def respond(data: Optional[BaseModel] = None) -> dict:
        return ApiResponse.__respond(data=data)

    @staticmethod
    def respond_paged(page: int, size: int, total: int, data=None) -> dict:
        return ApiResponse.__respond(page=page, size=size, total=total, data=data)

    @staticmethod
    def respond_error(message: str, data=None) -> dict:
        return ApiResponse.__respond(success=False, message=message, data=data)
