from enum import StrEnum


class TierRank(StrEnum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class MediaType(StrEnum):
    MOVIE = "movie"
    GAME = "game"
