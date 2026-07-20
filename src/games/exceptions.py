from fastapi import HTTPException, status


class GameNotFoundException(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game not found",
        )


class GameAlreadyTrackedException(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="Game is already in your tracking list",
        )


class RAWGServiceUnavailableException(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Game database service is temporarily unavailable",
        )
