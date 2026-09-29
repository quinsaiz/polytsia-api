from enum import StrEnum

SEARCH_CACHE_TTL = 3600
SEARCH_MAX_PAGE = 100
GAME_DETAIL_CACHE_TTL = 86400
GENRES_CACHE_TTL = 604800
PLATFORMS_CACHE_TTL = 604800


class PlayStatus(StrEnum):
    PLANNED = "planned"
    PLAYING = "playing"
    COMPLETED = "completed"
    DROPPED = "dropped"


class TierRank(StrEnum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"
