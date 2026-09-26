import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (        #type: ignore
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID        #type: ignore
from sqlalchemy.dialects.postgresql import insert as pg_insert      #type: ignore
from sqlalchemy.exc import IntegrityError       #type: ignore
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker       #type: ignore


class Base(DeclarativeBase):
    pass


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    run_id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization = Column(Text, nullable=False)
    started_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
    records_fetched = Column(BigInteger, nullable=False, default=0)
    records_valid = Column(BigInteger, nullable=False, default=0)
    records_quarantined = Column(BigInteger, nullable=False, default=0)
    status = Column(Text, nullable=False, default="running")

    __table_args__ = (
        CheckConstraint("records_fetched >= 0"),
        CheckConstraint("records_valid >= 0"),
        CheckConstraint("records_quarantined >= 0"),
        CheckConstraint("status IN ('running', 'completed', 'failed')"),
    )


class GithubRepo(Base):
    __tablename__ = "github_repos"

    id = Column(BigInteger, primary_key=True)
    run_id = Column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_runs.run_id"), nullable=False
    )
    organization = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    full_name = Column(Text, nullable=False)
    private = Column(Boolean, nullable=False)
    html_url = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    stargazers_count = Column(BigInteger, nullable=False)
    forks_count = Column(BigInteger, nullable=False)
    open_issues_count = Column(BigInteger, nullable=False)
    archived = Column(Boolean, nullable=False)
    recently_pushed_while_archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    pushed_at = Column(DateTime(timezone=True), nullable=False)
    ingested_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("stargazers_count >= 0"),
        CheckConstraint("forks_count >= 0"),
        CheckConstraint("open_issues_count >= 0"),
        CheckConstraint("pushed_at >= created_at"),
    )


class QuarantinedRepo(Base):
    __tablename__ = "github_repos_quarantine"

    quarantine_id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_runs.run_id"), nullable=False
    )
    organization = Column(Text, nullable=False)
    repo_id = Column(BigInteger, nullable=True)
    raw_payload = Column(JSONB, nullable=False)
    failed_field = Column(Text, nullable=False)
    error_message = Column(Text, nullable=False)
    quarantined_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dedup_key = Column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("organization", "dedup_key"),)


def get_engine(database_url: Optional[str] = None):
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        db_name = os.getenv("DB_NAME")
        db_user = os.getenv("DB_USER")
        db_password = os.getenv("DB_PASSWORD")
        db_host = os.getenv("DB_HOST")
        db_port = os.getenv("DB_PORT")
        if all([db_name, db_user, db_password, db_host, db_port]):
            database_url = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL or DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT must be set"
        )
    return create_engine(database_url, pool_pre_ping=True)


def get_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def compute_dedup_key(raw_payload: dict, failed_field: str, error_message: str) -> str:
    payload_str = json.dumps(raw_payload, sort_keys=True, default=str)
    combined = f"{payload_str}|{failed_field}|{error_message}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def create_pipeline_run(session: Session, organization: str) -> uuid.UUID:
    run = PipelineRun(organization=organization, status="running")
    session.add(run)
    session.commit()
    return run.run_id


def finish_pipeline_run(
    session: Session,
    run_id: uuid.UUID,
    records_fetched: int,
    records_valid: int,
    records_quarantined: int,
    status: str,
) -> None:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise ValueError(f"No pipeline run found with run_id {run_id}")
    run.finished_at = datetime.now(timezone.utc)
    run.records_fetched = records_fetched
    run.records_valid = records_valid
    run.records_quarantined = records_quarantined
    run.status = status
    session.commit()


def upsert_repo(
    session: Session, repo_data: dict, organization: str, run_id: uuid.UUID
) -> None:
    values = {
        "id": repo_data["id"],
        "run_id": run_id,
        "organization": organization,
        "name": repo_data["name"],
        "full_name": repo_data["full_name"],
        "private": repo_data["private"],
        "html_url": repo_data["html_url"],
        "description": repo_data.get("description"),
        "stargazers_count": repo_data["stargazers_count"],
        "forks_count": repo_data["forks_count"],
        "open_issues_count": repo_data["open_issues_count"],
        "archived": repo_data["archived"],
        "recently_pushed_while_archived": repo_data.get(
            "recently_pushed_while_archived", False
        ),
        "created_at": repo_data["created_at"],
        "updated_at": repo_data["updated_at"],
        "pushed_at": repo_data["pushed_at"],
    }

    stmt = pg_insert(GithubRepo).values(**values)
    update_columns = {col: stmt.excluded[col] for col in values if col not in ("id",)}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_columns)

    session.execute(stmt)
    session.commit()


def insert_quarantine_record(
    session: Session,
    raw_payload: dict,
    failed_field: str,
    error_message: str,
    organization: str,
    run_id: uuid.UUID,
    repo_id: Optional[int] = None,
) -> bool:
    dedup_key = compute_dedup_key(raw_payload, failed_field, error_message)

    record = QuarantinedRepo(
        run_id=run_id,
        organization=organization,
        repo_id=repo_id,
        raw_payload=raw_payload,
        failed_field=failed_field,
        error_message=error_message,
        dedup_key=dedup_key,
    )

    try:
        session.add(record)
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


def get_pipeline_run(session: Session, run_id: uuid.UUID) -> Optional[PipelineRun]:
    return session.get(PipelineRun, run_id)


def list_pipeline_runs(session: Session, limit: int = 20) -> list:
    return (
        session.query(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .limit(limit)
        .all()
    )


def get_quarantine_records(
    session: Session, organization: str, page: int = 1, page_size: int = 50
) -> list:
    offset = (page - 1) * page_size
    return (
        session.query(QuarantinedRepo)
        .filter(QuarantinedRepo.organization == organization)
        .order_by(QuarantinedRepo.quarantined_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
