from fastapi import HTTPException, status


class MovieNotFoundException(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Movie not found",
        )


class MovieAlreadyTrackedException(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="Movie is already in your tracking list",
        )


class TMDBServiceUnavailableException(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Movie database service is temporarily unavailable",
        )
