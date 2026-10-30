import os
import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status


def require_api_key(
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    expected_api_key = os.getenv("API_KEY")
    if not expected_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key is not configured",
        )

    if api_key is None or not secrets.compare_digest(
        api_key.encode(), expected_api_key.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
        )
