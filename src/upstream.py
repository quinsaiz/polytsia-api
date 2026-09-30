import logging
from json import JSONDecodeError

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class InvalidUpstreamPayload(Exception):
    """A catalog response cannot be decoded or used by the application."""


def parse_upstream[T: BaseModel](response: httpx.Response, schema: type[T]) -> T:
    try:
        return schema.model_validate(response.json())
    except (JSONDecodeError, UnicodeDecodeError, ValidationError):
        # Payloads and validation errors can contain credential-bearing URLs.
        logger.warning("Invalid upstream payload for %s", schema.__name__)
        raise InvalidUpstreamPayload(
            f"Invalid upstream payload for {schema.__name__}"
        ) from None
