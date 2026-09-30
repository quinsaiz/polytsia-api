"""Catalog fields captured independently of personal data and external ratings."""

import re
from datetime import UTC, date, datetime

from src.games.schemas import RAWGGameSchema
from src.movies.schemas import TMDBMovieSchema


def _release_date(value: str | None) -> date | None:
    # Existing catalog schemas accept arbitrary strings; do not tighten their
    # public contract or persist those strings as dates in the private library.
    if value is None or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def catalog_snapshot(details: TMDBMovieSchema | RAWGGameSchema) -> dict[str, object]:
    if isinstance(details, TMDBMovieSchema):
        fields = {
            "catalog_title": details.title,
            "catalog_poster_path": details.poster_path,
        }
        released = details.release_date
    else:
        fields = {
            "catalog_title": details.name,
            "catalog_background_image": details.background_image,
        }
        released = details.released
    return {
        **fields,
        "catalog_release_date": _release_date(released),
        "catalog_metadata_fetched_at": datetime.now(UTC),
    }
