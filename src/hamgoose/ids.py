"""Deterministic id generation for missions, milestones, features and workers."""
from __future__ import annotations

import secrets
from datetime import datetime


def mission_id() -> str:
    """Unique mission id of the form M-<year>-<HHMMSS><2hex>."""
    now = datetime.now()
    return f"M-{now.year}-{now.strftime('%H%M%S')}{secrets.token_hex(1).upper()}"


def feature_seq(n: int) -> str:
    """Zero-padded sequential feature id: F001, F002, ..."""
    return f"F{n:03d}"


def milestone_id(n: int) -> str:
    return f"MS{n:02d}"


def worker_id() -> str:
    return f"W-{secrets.token_hex(4).upper()}"


def fix_id(feature_id: str, attempt: int) -> str:
    return f"{feature_id}-FIX{attempt}"
