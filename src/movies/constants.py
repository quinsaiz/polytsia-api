from enum import StrEnum


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
