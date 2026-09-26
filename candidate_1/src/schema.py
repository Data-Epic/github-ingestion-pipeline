"""
Pydantic schema definitions for the GitHub ingestion pipeline.

Defines the core Repo model used to validate GitHub repository records
pulled from the /orgs/{org}/repos and /repos/{owner}/{repo} endpoints,
along with field-level and cross-field business rule validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator     #type: ignore   


class Repo(BaseModel):
    """
    Validated representation of a single GitHub repository record.

    Only the fields required by the project brief are modeled here.
    Extra fields returned by the GitHub API are ignored rather than
    rejected, since we only need a defined subset.
    """

    id: int
    name: str
    full_name: str
    private: bool
    html_url: str
    description: Optional[str] = None
    stargazers_count: int
    forks_count: int
    open_issues_count: int
    archived: bool
    created_at: datetime
    updated_at: datetime
    pushed_at: datetime

    # Non-fatal flag set by the cross-field validator below.
    # Not part of the raw GitHub payload
    # Used downstream by the quality engine.
    recently_pushed_while_archived: bool = Field(default=False, exclude=False)

    model_config = {
        "extra": "ignore",
    }

    # Field-level Validators

    @field_validator("name", "full_name")
    @classmethod
    def name_must_not_be_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("stargazers_count", "forks_count", "open_issues_count")
    @classmethod
    def counts_must_be_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be a non-negative integer")
        return value

    @field_validator("description")
    @classmethod
    def strip_description_whitespace(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        stripped = value.strip()
        return stripped if stripped else None

    # Cross-field Validators

    @model_validator(mode="after")
    def pushed_at_not_before_created_at(self) -> "Repo":
        if self.pushed_at < self.created_at:
            raise ValueError("pushed_at must not be earlier than created_at")
        return self

    @model_validator(mode="after")
    def flag_recently_pushed_archived_repos(self) -> "Repo":
        """
        Archived repos that were pushed to very recently are unusual
        (archiving is supposed to freeze a repo) but not invalid on
        their own -- GitHub allows a final push around archival time.
        This sets a non-fatal flag rather than raising, so the record
        is still valid but can be surfaced in the quality report.
        """
        if self.archived:
            days_since_push = (datetime.now(tz=self.pushed_at.tzinfo) - self.pushed_at).days
            if days_since_push < 7:
                self.recently_pushed_while_archived = True
        return self


class QuarantineRecord(BaseModel):
    """
    Represents a record that failed validation and is routed to the
    github_repos_quarantine table instead of github_repos.
    """

    raw_payload: dict
    failed_field: str
    error_message: str
    organization: str
    quarantined_at: datetime = Field(default_factory=datetime.now)
