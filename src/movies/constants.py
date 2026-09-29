from enum import StrEnum

SEARCH_CACHE_TTL = 3600
MOVIE_DETAIL_CACHE_TTL = 86400
GENRES_CACHE_TTL = 604800


class WatchStatus(StrEnum):
    PLANNED = "planned"
    WATCHING = "watching"
    COMPLETED = "completed"
    DROPPED = "dropped"


class TierRank(StrEnum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"
