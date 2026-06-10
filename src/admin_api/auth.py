import os

from fastapi import Header, HTTPException


def verify_admin_api_key(x_admin_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("ADMIN_API_KEY", "").strip()
    if not expected:
        return
    if x_admin_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid admin api key")
