from datetime import datetime, timezone

import pytest   # type: ignore
from pydantic import ValidationError    # type: ignore

from src.schema import QuarantineRecord, Repo   # type: ignore


def make_valid_repo_data(**overrides) -> dict:
    data = {
        "id": 1953385,
        "name": "stripe-ruby",
        "full_name": "stripe/stripe-ruby",
        "private": False,
        "html_url": "https://github.com/stripe/stripe-ruby",
        "description": "Ruby library for the Stripe API.",
        "stargazers_count": 2166,
        "forks_count": 685,
        "open_issues_count": 25,
        "archived": False,
        "created_at": "2011-06-25T19:51:57Z",
        "updated_at": "2026-09-17T20:07:24Z",
        "pushed_at": "2026-09-19T00:16:29Z",
    }
    data.update(overrides)
    return data


def test_valid_record_passes():
    repo = Repo(**make_valid_repo_data())
    assert repo.id == 1953385
    assert repo.name == "stripe-ruby"
    assert repo.archived is False


def test_missing_required_field_is_rejected():
    data = make_valid_repo_data()
    del data["full_name"]

    with pytest.raises(ValidationError) as exc_info:
        Repo(**data)

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("full_name",) for error in errors)


def test_wrong_type_is_rejected():
    data = make_valid_repo_data(stargazers_count="not a number")

    with pytest.raises(ValidationError) as exc_info:
        Repo(**data)

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("stargazers_count",) for error in errors)


def test_negative_star_count_is_rejected():
    data = make_valid_repo_data(stargazers_count=-5)

    with pytest.raises(ValidationError):
        Repo(**data)


def test_empty_name_is_rejected():
    data = make_valid_repo_data(name="")

    with pytest.raises(ValidationError):
        Repo(**data)


def test_pushed_before_created_is_rejected():
    data = make_valid_repo_data(
        created_at="2020-01-01T00:00:00Z",
        pushed_at="2019-01-01T00:00:00Z",
    )

    with pytest.raises(ValidationError):
        Repo(**data)


def test_pushed_equal_to_created_is_allowed():
    same_timestamp = "2020-01-01T00:00:00Z"
    data = make_valid_repo_data(created_at=same_timestamp, pushed_at=same_timestamp)

    repo = Repo(**data)
    assert repo.pushed_at == repo.created_at


def test_null_description_is_allowed():
    data = make_valid_repo_data(description=None)

    repo = Repo(**data)
    assert repo.description is None


def test_description_whitespace_is_stripped():
    data = make_valid_repo_data(description="  a payments library  ")

    repo = Repo(**data)
    assert repo.description == "a payments library"


def test_blank_description_becomes_none():
    data = make_valid_repo_data(description="    ")

    repo = Repo(**data)
    assert repo.description is None


def test_archived_and_recently_pushed_is_flagged():
    now = datetime.now(timezone.utc)
    data = make_valid_repo_data(
        archived=True,
        created_at="2020-01-01T00:00:00Z",
        pushed_at=now.isoformat(),
    )

    repo = Repo(**data)
    assert repo.recently_pushed_while_archived is True


def test_archived_but_pushed_long_ago_is_not_flagged():
    data = make_valid_repo_data(
        archived=True,
        created_at="2011-01-01T00:00:00Z",
        pushed_at="2019-12-17T17:07:55Z",
    )

    repo = Repo(**data)
    assert repo.recently_pushed_while_archived is False


def test_extra_fields_from_github_response_are_ignored():
    data = make_valid_repo_data()
    data["owner"] = {"login": "stripe", "id": 856813}
    data["topics"] = ["stripe", "stripe-sdk"]
    data["license"] = {"key": "mit"}

    repo = Repo(**data)
    assert repo.id == 1953385


def test_quarantine_record_stores_raw_payload_and_error_metadata():
    raw_payload = {"id": 1, "name": None}
    record = QuarantineRecord(
        raw_payload=raw_payload,
        failed_field="name",
        error_message="must not be empty",
        organization="stripe",
    )

    assert record.raw_payload == raw_payload
    assert record.failed_field == "name"
    assert record.organization == "stripe"
    assert record.quarantined_at is not None
