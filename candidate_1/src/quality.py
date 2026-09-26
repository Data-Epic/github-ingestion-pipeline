import random
from typing import Optional

from sqlalchemy import func     #type: ignore
from sqlalchemy.orm import Session      #type: ignore

from src.database import GithubRepo, PipelineRun, QuarantinedRepo

REQUIRED_FIELDS = [
    "name",
    "full_name",
    "html_url",
    "stargazers_count",
    "forks_count",
    "open_issues_count",
    "created_at",
    "updated_at",
    "pushed_at",
]


def compute_completeness(session: Session, organization: str) -> dict:
    total = (
        session.query(func.count(GithubRepo.id))
        .filter(GithubRepo.organization == organization)
        .scalar()
    )

    if total == 0:
        return {
            "total_records": 0,
            "field_completeness": {},
            "description_completeness": None,
        }

    field_completeness = {}
    for field_name in REQUIRED_FIELDS:
        column = getattr(GithubRepo, field_name)
        non_null_count = (
            session.query(func.count(GithubRepo.id))
            .filter(GithubRepo.organization == organization, column.isnot(None))
            .scalar()
        )
        field_completeness[field_name] = round(non_null_count / total, 4)

    description_non_null = (
        session.query(func.count(GithubRepo.id))
        .filter(
            GithubRepo.organization == organization, GithubRepo.description.isnot(None)
        )
        .scalar()
    )

    return {
        "total_records": total,
        "field_completeness": field_completeness,
        "description_completeness": round(description_non_null / total, 4),
    }


def compute_validity(session: Session, organization: str) -> Optional[dict]:
    latest_run = (
        session.query(PipelineRun)
        .filter(
            PipelineRun.organization == organization, PipelineRun.status == "completed"
        )
        .order_by(PipelineRun.finished_at.desc())
        .first()
    )

    if latest_run is None or latest_run.records_fetched == 0:
        return None

    validity_rate = latest_run.records_valid / latest_run.records_fetched

    return {
        "records_fetched": latest_run.records_fetched,
        "records_valid": latest_run.records_valid,
        "records_quarantined": latest_run.records_quarantined,
        "validity_rate": round(validity_rate, 4),
    }


def compute_uniqueness(session: Session, organization: str) -> dict:
    total_rows = (
        session.query(func.count(GithubRepo.id))
        .filter(GithubRepo.organization == organization)
        .scalar()
    )

    distinct_ids = (
        session.query(func.count(func.distinct(GithubRepo.id)))
        .filter(GithubRepo.organization == organization)
        .scalar()
    )

    duplicate_count = total_rows - distinct_ids

    return {
        "total_rows": total_rows,
        "distinct_ids": distinct_ids,
        "duplicate_count": duplicate_count,
    }


def compute_consistency(session: Session, organization: str) -> dict:
    total = (
        session.query(func.count(GithubRepo.id))
        .filter(GithubRepo.organization == organization)
        .scalar()
    )

    if total == 0:
        return {"total_records": 0, "consistency_rate": None, "flagged_anomalies": 0}

    consistent_count = (
        session.query(func.count(GithubRepo.id))
        .filter(
            GithubRepo.organization == organization,
            GithubRepo.pushed_at >= GithubRepo.created_at,
        )
        .scalar()
    )

    flagged_anomalies = (
        session.query(func.count(GithubRepo.id))
        .filter(
            GithubRepo.organization == organization,
            GithubRepo.recently_pushed_while_archived.is_(True),
        )
        .scalar()
    )

    return {
        "total_records": total,
        "consistency_rate": round(consistent_count / total, 4),
        "flagged_anomalies": flagged_anomalies,
    }


def compute_accuracy(
    session: Session, client, organization: str, sample_size: int = 5
) -> Optional[dict]:
    repos = (
        session.query(GithubRepo).filter(GithubRepo.organization == organization).all()
    )

    if not repos:
        return None

    sample = random.sample(repos, min(sample_size, len(repos)))
    matches = 0
    checked = 0

    for repo in sample:
        owner, _, repo_name = repo.full_name.partition("/")
        if not owner or not repo_name:
            continue

        try:
            live_data = client.get_repo(owner, repo_name)
        except Exception:
            continue

        checked += 1
        if (
            live_data.get("id") == repo.id
            and live_data.get("full_name") == repo.full_name
            and live_data.get("archived") == repo.archived
        ):
            matches += 1

    if checked == 0:
        return None

    return {
        "sample_size": checked,
        "matches": matches,
        "accuracy_rate": round(matches / checked, 4),
    }


def build_quality_report(session: Session, client, organization: str) -> dict:
    return {
        "organization": organization,
        "completeness": compute_completeness(session, organization),
        "validity": compute_validity(session, organization),
        "uniqueness": compute_uniqueness(session, organization),
        "consistency": compute_consistency(session, organization),
        "accuracy": compute_accuracy(session, client, organization),
    }
