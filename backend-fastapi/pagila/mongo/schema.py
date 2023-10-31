from typing import Optional, Any

from bson import ObjectId
from pydantic import BaseModel, Field
from pydantic_core import core_schema


class PyObjectId(ObjectId):

    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type: Any, _handler: Any) -> core_schema.CoreSchema:
        object_id_schema = core_schema.chain_schema([
            core_schema.str_schema(),
            core_schema.no_info_plain_validator_function(cls.validate),
        ])
        return core_schema.json_or_python_schema(
            json_schema=object_id_schema,
            python_schema=core_schema.union_schema(
                [core_schema.is_instance_schema(ObjectId), object_id_schema]
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda x: str(x)
            ),
        )

    @classmethod
    def validate(cls, value):
        if not ObjectId.is_valid(value):
            raise ValueError("Invalid id")

        return ObjectId(value)


class Award(BaseModel):
    wins: int
    nominations: int
    text: str

    class Config:
        arbitrary_types_allowed = True


class Movie(BaseModel):
    o_id: PyObjectId = Field(alias="_id", title="id")
    awards: Award
    full_plot: Optional[str] = Field(None, alias="fullplot")
    last_updated: str = Field(..., alias="lastupdated")
    plot: Optional[str]
    title: str
    type: str
    year: int

    class Config:
        json_encoders = {
            ObjectId: str
        }
