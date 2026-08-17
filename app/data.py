"""Synthetic HR records. Every value here is fictional test data."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .settings import MOCK_DATA_DIR


def _read(name: str) -> list[dict[str, Any]]:
    return json.loads((MOCK_DATA_DIR / name).read_text())


def employee(employee_id: str) -> dict[str, Any] | None:
    return next((row for row in _read("employees.json") if row["employee_id"] == employee_id), None)


def pto_balance(employee_id: str) -> dict[str, Any] | None:
    return next((row for row in _read("pto_balances.json") if row["employee_id"] == employee_id), None)


def benefits(employee_id: str) -> dict[str, Any] | None:
    return next((row for row in _read("benefits.json") if row["employee_id"] == employee_id), None)


def create_ticket(employee_id: str, summary: str, category: str) -> dict[str, Any]:
    """Create a confirmed mock HR draft. No external system is modified.

    The reference is derived from the ticket's own content rather than the clock,
    so that the same request always produces the same identifier and evaluation
    runs stay reproducible.
    """
    safe_summary = " ".join(summary.split())[:300]
    safe_category = " ".join(category.split()).lower()[:64] or "general-hr"
    digest = hashlib.sha256(f"{employee_id}|{safe_category}|{safe_summary}".encode()).hexdigest()[:10].upper()
    return {
        "ticket_id": f"MOCK-{digest}",
        "employee_id": employee_id,
        "category": safe_category,
        "summary": safe_summary,
        "status": "draft",
        "confirmation_obtained": True,
        "mock_only": True,
        "drafted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
