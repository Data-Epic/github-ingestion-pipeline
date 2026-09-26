import logging
import uuid
from typing import Optional

from pydantic import ValidationError        #type: ignore

from src.client import GitHubClient
from src.database import (
    create_pipeline_run,
    finish_pipeline_run,
    get_engine,
    get_session_factory,
    insert_quarantine_record,
    upsert_repo,
)
from src.schema import Repo

logger = logging.getLogger(__name__)


def ingest_organization(organization: str, run_id: Optional[uuid.UUID] = None) -> dict:
    engine = get_engine()
    Session = get_session_factory(engine)
    session = Session()

    if run_id is None:
        run_id = create_pipeline_run(session, organization)

    records_fetched = 0
    records_valid = 0
    records_quarantined = 0
    status = "completed"

    try:
        client = GitHubClient()
        raw_records = client.get_org_repos(organization, run_timestamp=str(run_id))
        records_fetched = len(raw_records)

        for raw_record in raw_records:
            try:
                validated = Repo(**raw_record)
                upsert_repo(session, validated.model_dump(), organization, run_id)
                records_valid += 1
            except ValidationError as exc:
                first_error = exc.errors()[0]
                failed_field = ".".join(str(part) for part in first_error["loc"])
                error_message = first_error["msg"]
                inserted = insert_quarantine_record(
                    session=session,
                    raw_payload=raw_record,
                    failed_field=failed_field,
                    error_message=error_message,
                    organization=organization,
                    run_id=run_id,
                    repo_id=raw_record.get("id"),
                )
                if inserted:
                    records_quarantined += 1

    except Exception:
        logger.exception("Ingestion failed for %s", organization)
        status = "failed"

    finish_pipeline_run(
        session, run_id, records_fetched, records_valid, records_quarantined, status
    )
    session.close()

    return {
        "organization": organization,
        "run_id": str(run_id),
        "records_fetched": records_fetched,
        "records_valid": records_valid,
        "records_quarantined": records_quarantined,
        "status": status,
    }
