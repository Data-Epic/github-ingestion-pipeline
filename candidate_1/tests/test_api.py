import uuid

import pytest  # type: ignore
from fastapi.testclient import TestClient  # type: ignore

from src.database import (
    create_pipeline_run,
    get_engine,
    get_session_factory,
    insert_quarantine_record,
)
from src.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    engine = get_engine()
    Session = get_session_factory(engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def test_org():
    return f"test-api-org-{uuid.uuid4().hex[:8]}"


def test_ingest_returns_run_id_immediately_without_blocking(client, test_org, mocker):
    mocker.patch("src.main.ingest_organization", return_value=None)

    response = client.post(f"/ingest/{test_org}")

    assert response.status_code == 202
    body = response.json()
    assert "run_id" in body
    assert body["organization"] == test_org
    assert body["status"] == "running"

    uuid.UUID(body["run_id"])


def test_ingest_returns_409_when_run_already_active(
    client, test_org, db_session, mocker
):
    mocker.patch("src.main.ingest_organization", return_value=None)
    create_pipeline_run(db_session, test_org)

    response = client.post(f"/ingest/{test_org}")

    assert response.status_code == 409


def test_get_run_returns_404_for_unknown_run(client):
    fake_run_id = uuid.uuid4()

    response = client.get(f"/runs/{fake_run_id}")

    assert response.status_code == 404


def test_get_run_returns_correct_payload_for_known_run(client, db_session, test_org):
    run_id = create_pipeline_run(db_session, test_org)

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == str(run_id)
    assert body["organization"] == test_org
    assert body["status"] == "running"


def test_get_run_with_invalid_uuid_returns_422(client):
    response = client.get("/runs/not-a-valid-uuid")

    assert response.status_code == 422


def test_quality_report_returns_correctly_shaped_scores(client, test_org, mocker):
    fake_report = {
        "organization": test_org,
        "completeness": {
            "total_records": 0,
            "field_completeness": {},
            "description_completeness": None,
        },
        "validity": None,
        "uniqueness": {"total_rows": 0, "distinct_ids": 0, "duplicate_count": 0},
        "consistency": {
            "total_records": 0,
            "consistency_rate": None,
            "flagged_anomalies": 0,
        },
        "accuracy": None,
    }
    mocker.patch("src.main.build_quality_report", return_value=fake_report)

    response = client.get(f"/quality-report/{test_org}")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "organization",
        "completeness",
        "validity",
        "uniqueness",
        "consistency",
        "accuracy",
    }


def test_quarantine_returns_paginated_results(client, db_session, test_org):
    run_id = create_pipeline_run(db_session, test_org)
    insert_quarantine_record(
        session=db_session,
        raw_payload={"id": 1, "name": None},
        failed_field="name",
        error_message="must not be empty",
        organization=test_org,
        run_id=run_id,
        repo_id=1,
    )

    response = client.get(f"/quarantine/{test_org}?page=1&page_size=10")

    assert response.status_code == 200
    body = response.json()
    assert body["organization"] == test_org
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert len(body["records"]) == 1
    assert body["records"][0]["failed_field"] == "name"


def test_quarantine_with_invalid_page_returns_422(client, test_org):
    response = client.get(f"/quarantine/{test_org}?page=0")

    assert response.status_code == 422


def test_quarantine_with_invalid_page_size_returns_422(client, test_org):
    response = client.get(f"/quarantine/{test_org}?page_size=99999")

    assert response.status_code == 422


def test_list_runs_returns_200_with_list(client):
    response = client.get("/runs")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_list_runs_with_invalid_limit_returns_422(client):
    response = client.get("/runs?limit=0")

    assert response.status_code == 422
