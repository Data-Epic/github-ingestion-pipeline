import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from tenacity import (      #type: ignore
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class TransientServerError(Exception):
    pass


class RateLimitExceededError(Exception):
    pass


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: Optional[str] = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_retries: int = 3,
        wait_on_rate_limit: bool = True,
        raw_data_dir: str = "data/raw",
    ):
        self.token = token or os.getenv("GITHUB_TOKEN")
        if not self.token:
            logger.warning("GITHUB_TOKEN not set, proceeding unauthenticated")

        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_retries = max_retries
        self.wait_on_rate_limit = wait_on_rate_limit
        self.raw_data_dir = Path(raw_data_dir)

        self.session = requests.Session()
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.session.headers.update(headers)

    def _timeout(self):
        return (self.connect_timeout, self.read_timeout)

    def _check_rate_limit(self, response: requests.Response) -> bool:
        if (
            response.status_code in (403, 429)
            and response.headers.get("X-RateLimit-Remaining") == "0"
        ):
            reset_ts = int(response.headers.get("X-RateLimit-Reset", time.time()))
            wait_seconds = max(reset_ts - time.time(), 0)
            if self.wait_on_rate_limit:
                logger.warning(
                    "Rate limit exhausted, waiting %.0fs until reset", wait_seconds
                )
                time.sleep(wait_seconds + 1)
                return True
            raise RateLimitExceededError(f"Rate limit exhausted, resets at {reset_ts}")
        return False

    @retry(
        retry=retry_if_exception_type(
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                TransientServerError,
            )
        ),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _send(self, method: str, url: str, **kwargs) -> requests.Response:
        response = self.session.request(method, url, timeout=self._timeout(), **kwargs)
        if response.status_code >= 500:
            raise TransientServerError(f"{response.status_code} from {url}")
        return response

    def _get(self, url: str, params: Optional[dict] = None) -> requests.Response:
        while True:
            response = self._send("GET", url, params=params)
            if self._check_rate_limit(response):
                continue
            return response

    def get_rate_limit(self) -> dict:
        response = self._get(f"{self.BASE_URL}/rate_limit")
        response.raise_for_status()
        return response.json()

    def get_repo(self, owner: str, repo: str) -> dict:
        response = self._get(f"{self.BASE_URL}/repos/{owner}/{repo}")
        response.raise_for_status()
        return response.json()

    def get_org_repos(self, org: str, run_timestamp: Optional[str] = None) -> list:
        run_timestamp = run_timestamp or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        url = f"{self.BASE_URL}/orgs/{org}/repos"
        params = {"per_page": 100}
        all_records: list = []
        page_number = 1

        while url:
            response = self._get(url, params=params if page_number == 1 else None)
            response.raise_for_status()

            self._save_raw_page(org, run_timestamp, page_number, response.text)

            try:
                page_records = response.json()
            except ValueError as exc:
                logger.error("Malformed JSON on %s page %s: %s", org, page_number, exc)
                page_records = []

            all_records.extend(page_records)

            url = response.links.get("next", {}).get("url")
            page_number += 1

        return all_records

    def _save_raw_page(
        self, org: str, run_timestamp: str, page_number: int, raw_text: str
    ) -> None:
        target_dir = self.raw_data_dir / org
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"{run_timestamp}_page{page_number}.json"
        file_path.write_text(raw_text, encoding="utf-8")
