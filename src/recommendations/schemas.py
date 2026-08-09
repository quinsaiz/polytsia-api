from pydantic import BaseModel


class RecommendedMovieSchema(BaseModel):
    tmdb_id: int
    title: str
    rating: float
    overview: str = ""
    genre_ids: list[int] = []
    poster_path: str | None = None


class RecommendedGameSchema(BaseModel):
    rawg_id: int
    name: str
    rating: float
    background_image: str | None = None


class RecommendationsResponseSchema(BaseModel):
    movie_threshold: float | None
    game_threshold: float | None
    is_movies_personalized: bool
    is_games_personalized: bool
    movies: list[RecommendedMovieSchema] = []
    games: list[RecommendedGameSchema] = []


class MovieRecommendationsResponseSchema(BaseModel):
    threshold: float | None
    is_personalized: bool
    movies: list[RecommendedMovieSchema] = []


class GameRecommendationsResponseSchema(BaseModel):
    threshold: float | None
    is_personalized: bool
    games: list[RecommendedGameSchema] = []
