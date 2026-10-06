from __future__ import annotations

from core.email_normalize import canonicalize_email


def test_gmail_dots_are_ignored():
    assert canonicalize_email("John.Smith@gmail.com") == canonicalize_email("JohnSmith@gmail.com")


def test_gmail_plus_tag_is_stripped():
    assert canonicalize_email("john.smith+realtor@gmail.com") == "johnsmith@gmail.com"


def test_googlemail_folds_into_gmail():
    assert canonicalize_email("john.smith@googlemail.com") == "johnsmith@gmail.com"


def test_non_gmail_dots_are_significant():
    # Dots are NOT stripped outside Gmail - these are different mailboxes.
    assert canonicalize_email("john.smith@outlook.com") != canonicalize_email("johnsmith@outlook.com")


def test_non_gmail_plus_tag_is_still_stripped():
    assert canonicalize_email("jane+newsletter@acmebrokers.com") == "jane@acmebrokers.com"


def test_case_and_whitespace_insensitive():
    assert canonicalize_email("  Jane.Doe@ACME.com  ") == canonicalize_email("jane.doe@acme.com")


def test_malformed_email_returns_none():
    assert canonicalize_email("not-an-email") is None
    assert canonicalize_email("@nolocal.com") is None
    assert canonicalize_email("nodomain@") is None
