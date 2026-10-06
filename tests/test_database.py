from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from postgrest.exceptions import APIError

from core.database import ContactRepository
from core.exceptions import DatabaseError
from core.models import Contact


def _make_repo(mock_client: MagicMock) -> ContactRepository:
    """Build a ContactRepository around a mock Supabase client, bypassing
    __init__ (which needs real Settings + a live-ish Supabase client)."""
    repo = ContactRepository.__new__(ContactRepository)
    repo._client = mock_client
    repo._table = "contacts"
    return repo


def _contact() -> Contact:
    return Contact(
        lead_id="lead-1", name="Jane Doe", title="Owner",
        email="jane@example.com", profile_url="https://yp/example#jane-doe",
    )


def _unique_violation(constraint: str) -> APIError:
    return APIError({
        "code": "23505",
        "message": f'duplicate key value violates unique constraint "{constraint}"',
        "details": "Key (email_canonical)=(jane@example.com) already exists."
                    if constraint == "contacts_email_canonical_key" else "Key (id)=(1) already exists.",
        "hint": None,
    })


def test_upsert_returns_true_on_success():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
        MagicMock(data=[])
    )
    client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "c1"}])

    repo = _make_repo(client)
    assert repo.upsert(_contact()) is True


def test_upsert_returns_false_on_email_canonical_conflict():
    """A duplicate email must be silently skipped, not raised - the DB-level
    UNIQUE constraint is the atomic dedup mechanism, so this is a normal
    outcome, not a failure."""
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
        MagicMock(data=[])
    )
    client.table.return_value.upsert.return_value.execute.side_effect = _unique_violation(
        "contacts_email_canonical_key"
    )

    repo = _make_repo(client)
    assert repo.upsert(_contact()) is False


def test_upsert_reraises_unrelated_unique_violation_as_database_error():
    """A UNIQUE violation on some other constraint (e.g. the primary key) is a
    real failure and must still surface as DatabaseError, not be swallowed."""
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
        MagicMock(data=[])
    )
    client.table.return_value.upsert.return_value.execute.side_effect = _unique_violation("contacts_pkey")

    repo = _make_repo(client)
    with pytest.raises(DatabaseError):
        repo.upsert(_contact())


def test_upsert_reraises_other_failures_as_database_error():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
        MagicMock(data=[])
    )
    client.table.return_value.upsert.return_value.execute.side_effect = RuntimeError("connection reset")

    repo = _make_repo(client)
    with pytest.raises(DatabaseError):
        repo.upsert(_contact())
