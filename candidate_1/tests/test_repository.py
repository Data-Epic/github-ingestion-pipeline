import uuid

import pytest  # type: ignore
from sqlalchemy.exc import IntegrityError   # type: ignore

from src.database import (
    GithubRepo,
    QuarantinedRepo,
    create_pipeline_run,
    finish_pipeline_run,
    get_engine,
    get_pipeline_run,
    get_quarantine_records,
    get_session_factory,
    insert_quarantine_record,
    upsert_repo,
)


@pytest.fixture
def session():
    engine = get_engine()
    Session = get_session_factory(engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def test_org():
    return f"test-org-{uuid.uuid4().hex[:8]}"


def make_repo_payload(repo_id: int, **overrides) -> dict:
    payload = {
        "id": repo_id,
        "name": "test-repo",
        "full_name": "test-org/test-repo",
        "private": False,
        "html_url": "https://github.com/test-org/test-repo",
        "description": "a test repo",
        "stargazers_count": 10,
        "forks_count": 2,
        "open_issues_count": 1,
        "archived": False,
        "recently_pushed_while_archived": False,
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2020-06-01T00:00:00Z",
        "pushed_at": "2020-06-01T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_new_record_inserts_successfully(session, test_org):
    run_id = create_pipeline_run(session, test_org)
    repo_id = 900001

    upsert_repo(session, make_repo_payload(repo_id), test_org, run_id)

    stored = session.get(GithubRepo, repo_id)
    assert stored is not None
    assert stored.name == "test-repo"
    assert stored.organization == test_org


def test_reinserting_same_id_upserts_instead_of_duplicating(session, test_org):
    run_id = create_pipeline_run(session, test_org)
    repo_id = 900002

    upsert_repo(
        session, make_repo_payload(repo_id, stargazers_count=10), test_org, run_id
    )
    upsert_repo(
        session, make_repo_payload(repo_id, stargazers_count=500), test_org, run_id
    )

    matches = session.query(GithubRepo).filter(GithubRepo.id == repo_id).all()

    assert len(matches) == 1
    assert matches[0].stargazers_count == 500


def test_quarantined_record_persists_with_raw_payload_and_error_metadata(
    session, test_org
):
    run_id = create_pipeline_run(session, test_org)
    raw_payload = {"id": 900003, "name": None, "note": "broken record"}

    inserted = insert_quarantine_record(
        session=session,
        raw_payload=raw_payload,
        failed_field="name",
        error_message="must not be empty",
        organization=test_org,
        run_id=run_id,
        repo_id=900003,
    )

    assert inserted is True

    records = get_quarantine_records(session, test_org)
    assert len(records) == 1
    assert records[0].raw_payload == raw_payload
    assert records[0].failed_field == "name"
    assert records[0].error_message == "must not be empty"


def test_malformed_foreign_key_is_rejected_by_constraint(session, test_org):
    fake_run_id = uuid.uuid4()

    bad_repo = GithubRepo(
        id=900004,
        run_id=fake_run_id,
        organization=test_org,
        name="orphan-repo",
        full_name="test-org/orphan-repo",
        private=False,
        html_url="https://github.com/test-org/orphan-repo",
        stargazers_count=0,
        forks_count=0,
        open_issues_count=0,
        archived=False,
        created_at="2020-01-01T00:00:00Z",
        updated_at="2020-01-01T00:00:00Z",
        pushed_at="2020-01-01T00:00:00Z",
    )

    session.add(bad_repo)

    with pytest.raises(IntegrityError):
        session.commit()


def test_pipeline_run_reflects_correct_counts(session, test_org):
    run_id = create_pipeline_run(session, test_org)

    finish_pipeline_run(
        session,
        run_id,
        records_fetched=10,
        records_valid=8,
        records_quarantined=2,
        status="completed",
    )

    run = get_pipeline_run(session, run_id)

    assert run.records_fetched == 10    # type: ignore
    assert run.records_valid == 8       # type: ignore
    assert run.records_quarantined == 2 # type: ignore
    assert run.status == "completed"    # type: ignore
    assert run.finished_at is not None  # type: ignore


def test_finish_pipeline_run_raises_for_unknown_run_id(session):
    fake_run_id = uuid.uuid4()

    with pytest.raises(ValueError):
        finish_pipeline_run(session, fake_run_id, 0, 0, 0, "completed")


def test_quarantine_dedup_key_prevents_duplicate_on_repeat_insert(session, test_org):
    run_id = create_pipeline_run(session, test_org)
    raw_payload = {"id": 900005, "name": None}

    first = insert_quarantine_record(
        session=session,
        raw_payload=raw_payload,
        failed_field="name",
        error_message="must not be empty",
        organization=test_org,
        run_id=run_id,
        repo_id=900005,
    )
    second = insert_quarantine_record(
        session=session,
        raw_payload=raw_payload,
        failed_field="name",
        error_message="must not be empty",
        organization=test_org,
        run_id=run_id,
        repo_id=900005,
    )

    assert first is True
    assert second is False

    records = get_quarantine_records(session, test_org)
    assert len(records) == 1
