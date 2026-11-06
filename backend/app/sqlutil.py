"""Small typing helpers for SQLAlchemy results."""
from collections.abc import Iterable
from typing import Any


def rows_to_dict(rows: Iterable[Any]) -> dict[Any, Any]:
    """{first column: second column} from a two-column query result.
    (`dict(query.all())` is fine at runtime, but mypy cannot see a SQLAlchemy Row as a pair.)"""
    return {r[0]: r[1] for r in rows}
