from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.email_normalize import canonicalize_email


class Lead(BaseModel):
    """Validated representation of a single real estate decision-maker lead."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1)
    email: Optional[str] = None
    phone: Optional[str] = None
    brokerage: Optional[str] = None
    location: Optional[str] = None
    website_url: Optional[str] = None
    profile_url: str = Field(min_length=1)
    listing_count: Optional[int] = Field(default=None, ge=0)
    source: str = "realtor"
    status: str = "new"
    description_raw: Optional[str] = None    # raw scraped text (YP category, LinkedIn headline) - verbatim, no interpretation
    description_source: Optional[str] = None  # where description_raw came from, e.g. "yellowpages_category"
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_phone(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        digits = re.sub(r"\D", "", str(v))
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits if len(digits) == 10 else None

    @field_validator("email", mode="before")
    @classmethod
    def _validate_email(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip().lower()
        if not s or "***" in s:
            return None
        return s if re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else None

    @field_validator("name", "brokerage", "location", "description_raw", mode="before")
    @classmethod
    def _reject_empty_strings(cls, v: object) -> Optional[str]:
        if isinstance(v, str) and not v.strip():
            return None  # type: ignore[return-value]
        return v  # type: ignore[return-value]

    def to_db_dict(self) -> dict:
        """Serialize to a flat dict suitable for Supabase upsert."""
        data = self.model_dump()
        data["scraped_at"] = self.scraped_at.isoformat()
        return data

    @property
    def is_contactable(self) -> bool:
        """True if the lead has a phone number and is worth saving to the DB."""
        return bool(self.phone)


class Contact(BaseModel):
    """A single decision maker scraped from a brokerage's team/about page."""

    model_config = ConfigDict(str_strip_whitespace=True)

    lead_id: str = Field(min_length=1)       # FK → leads.id
    name: str = Field(min_length=1)
    title: Optional[str] = None
    email: Optional[str] = None
    email_canonical: Optional[str] = None    # derived from email — see core/email_normalize.py
    profile_url: str = Field(min_length=1)   # unique key — YP listing URL + name slug
    status: str = "new"
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("email", mode="before")
    @classmethod
    def _validate_email(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip().lower()
        if not s or "***" in s:
            return None
        return s if re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else None

    @field_validator("name", "title", mode="before")
    @classmethod
    def _reject_empty_strings(cls, v: object) -> Optional[str]:
        if isinstance(v, str) and not v.strip():
            return None  # type: ignore[return-value]
        return v  # type: ignore[return-value]

    @model_validator(mode="after")
    def _derive_email_canonical(self) -> "Contact":
        """Always recompute — email_canonical is derived, never set directly."""
        self.email_canonical = canonicalize_email(self.email) if self.email else None
        return self

    def to_db_dict(self) -> dict:
        """Serialize to a flat dict suitable for Supabase upsert."""
        data = self.model_dump()
        data["scraped_at"] = self.scraped_at.isoformat()
        return data

    @property
    def is_contactable(self) -> bool:
        """True if the contact has a verified email address."""
        return bool(self.email)
