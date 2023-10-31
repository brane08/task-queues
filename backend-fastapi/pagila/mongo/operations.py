from mongo.schema import Movie
from pymongo.database import Database
from typing import List


def get_movies(db: Database, page: int, size: int) -> List[Movie]:
    movies = list()
    for mv in db["movies"].find().skip(((page - 1) * size)).limit(size):
        movies.append(Movie(**mv))
    return movies


def count_movies(db: Database) -> int:
    return db["movies"].count_documents({})
