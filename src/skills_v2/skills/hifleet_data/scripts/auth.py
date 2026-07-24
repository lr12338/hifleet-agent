"""Authentication key selection for HiFleet ship APIs.

Do not log or print values returned by these helpers.
"""
from __future__ import annotations

import os


def first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def public_api_key() -> str:
    """Public api.hifleet.com key for position/archive/voyage/area/strait/redsea."""
    return first_env("api_key", "HIFLEET_API_KEY", "hifleet_key1")


def psc_api_key() -> str:
    """PSC APIs require the hifleet_key1 scope."""
    return first_env("hifleet_key1", "HIFLEET_API_KEY", "api_key")


def ttse_key() -> str:
    """Internal ttseapi key for text ship search and write operations."""
    return first_env("hifleet_key2", "HIFLEET_TTSE_KEY", "HIFLEET_API_KEY")

