from __future__ import annotations

from core.models import Contact


def _contact(email: str | None) -> Contact:
    return Contact(lead_id="lead-1", name="Jane Doe", title="Owner", email=email, profile_url="u#jane-doe")


def test_email_canonical_is_derived_from_email():
    c = _contact("Jane.Doe+realtor@GMAIL.com")
    assert c.email == "jane.doe+realtor@gmail.com"  # validator lowercases but keeps the real address
    assert c.email_canonical == "janedoe@gmail.com"  # canonical form used only for dedup


def test_email_canonical_is_none_when_no_email():
    c = _contact(None)
    assert c.email is None
    assert c.email_canonical is None


def test_two_contacts_with_equivalent_gmail_addresses_share_canonical_email():
    a = _contact("j.doe@gmail.com")
    b = _contact("jdoe+leads@gmail.com")
    assert a.email_canonical == b.email_canonical
