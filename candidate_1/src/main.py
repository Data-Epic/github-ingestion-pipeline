import logging
import uuid

from dotenv import load_dotenv  # type: ignore

load_dotenv()

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query  # type: ignore
from sqlalchemy.orm import Session  # type: ignore

from src.client import GitHubClient
from src.database import (
    PipelineRun,
    create_pipeline_run,
    get_engine,
    get_pipeline_run,
    get_quarantine_records,
    get_session_factory,
    list_pipeline_runs,
)
from src.pipeline import ingest_organization
from src.quality import build_quality_report

logger = logging.getLogger(__name__)

app = FastAPI(title="GitHub Ingestion Pipeline")

engine = None
SessionLocal = None


def get_db():
    global engine, SessionLocal

    if engine is None or SessionLocal is None:
        engine = get_engine()
        SessionLocal = get_session_factory(engine)

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def run_is_active(session: Session, organization: str) -> bool:
    active = (
        session.query(PipelineRun)
        .filter(
            PipelineRun.organization == organization, PipelineRun.status == "running"
        )
        .first()
    )
    return active is not None


@app.post("/ingest/{org}", status_code=202)
def trigger_ingestion(
    org: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    if run_is_active(db, org):
        raise HTTPException(
            status_code=409, detail=f"An ingestion run is already in progress for {org}"
        )

    run_id = create_pipeline_run(db, org)
    background_tasks.add_task(ingest_organization, org, run_id)

    return {"run_id": str(run_id), "organization": org, "status": "running"}


@app.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = get_pipeline_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail="No pipeline run found with that run_id"
        )

    return {
        "run_id": str(run.run_id),
        "organization": run.organization,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "records_fetched": run.records_fetched,
        "records_valid": run.records_valid,
        "records_quarantined": run.records_quarantined,
        "status": run.status,
    }


@app.get("/runs")
def list_runs(
    limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)
):
    runs = list_pipeline_runs(db, limit)
    return [
        {
            "run_id": str(run.run_id),
            "organization": run.organization,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "records_fetched": run.records_fetched,
            "records_valid": run.records_valid,
            "records_quarantined": run.records_quarantined,
            "status": run.status,
        }
        for run in runs
    ]


@app.get("/quality-report/{org}")
def get_quality_report(org: str, db: Session = Depends(get_db)):
    client = GitHubClient()
    return build_quality_report(db, client, org)


@app.get("/quarantine/{org}")
def get_quarantine(
    org: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    records = get_quarantine_records(db, org, page, page_size)
    return {
        "organization": org,
        "page": page,
        "page_size": page_size,
        "records": [
            {
                "quarantine_id": record.quarantine_id,
                "repo_id": record.repo_id,
                "failed_field": record.failed_field,
                "error_message": record.error_message,
                "quarantined_at": record.quarantined_at,
            }
            for record in records
        ],
    }
