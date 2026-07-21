from enum import StrEnum


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
