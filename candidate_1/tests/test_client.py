import time

import pytest   # type: ignore
import requests # type: ignore
import responses    # type: ignore

from src.client import GitHubClient, RateLimitExceededError, TransientServerError


@pytest.fixture(autouse=True)
def no_real_sleep(mocker):
    mocker.patch("time.sleep", return_value=None)


@responses.activate
def test_get_rate_limit_returns_parsed_json():
    responses.add(
        responses.GET,
        "https://api.github.com/rate_limit",
        json={"rate": {"remaining": 5000}},
        status=200,
    )

    client = GitHubClient(token="fake-token")
    result = client.get_rate_limit()

    assert result == {"rate": {"remaining": 5000}}


@responses.activate
def test_pagination_follows_link_header_across_three_pages():
    base_url = "https://api.github.com/orgs/testorg/repos"

    responses.add(
        responses.GET,
        base_url,
        json=[{"id": 1, "name": "repo1"}],
        status=200,
        headers={"Link": f'<{base_url}?page=2>; rel="next"'},
    )
    responses.add(
        responses.GET,
        f"{base_url}?page=2",
        json=[{"id": 2, "name": "repo2"}],
        status=200,
        headers={"Link": f'<{base_url}?page=3>; rel="next"'},
    )
    responses.add(
        responses.GET,
        f"{base_url}?page=3",
        json=[{"id": 3, "name": "repo3"}],
        status=200,
    )

    client = GitHubClient(token="fake-token", raw_data_dir="test_data/raw")
    records = client.get_org_repos("testorg", run_timestamp="20260101T000000Z")

    assert len(records) == 3
    assert [r["id"] for r in records] == [1, 2, 3]
    assert len(responses.calls) == 3


@responses.activate
def test_timeout_then_successful_retry():
    url = "https://api.github.com/rate_limit"

    responses.add(
        responses.GET,
        url,
        body=requests.exceptions.Timeout("simulated timeout"),
    )
    responses.add(
        responses.GET,
        url,
        json={"rate": {"remaining": 100}},
        status=200,
    )

    client = GitHubClient(token="fake-token")
    result = client.get_rate_limit()

    assert result == {"rate": {"remaining": 100}}
    assert len(responses.calls) == 2


@responses.activate
def test_connection_error_then_successful_retry():
    url = "https://api.github.com/rate_limit"

    responses.add(
        responses.GET,
        url,
        body=requests.exceptions.ConnectionError("simulated connection drop"),
    )
    responses.add(
        responses.GET,
        url,
        json={"rate": {"remaining": 100}},
        status=200,
    )

    client = GitHubClient(token="fake-token")
    result = client.get_rate_limit()

    assert result == {"rate": {"remaining": 100}}


@responses.activate
def test_5xx_triggers_backoff_then_succeeds():
    url = "https://api.github.com/rate_limit"

    responses.add(responses.GET, url, status=503)
    responses.add(responses.GET, url, json={"rate": {"remaining": 100}}, status=200)

    client = GitHubClient(token="fake-token")
    result = client.get_rate_limit()

    assert result == {"rate": {"remaining": 100}}
    assert len(responses.calls) == 2


@responses.activate
def test_5xx_fails_cleanly_after_max_retries():
    url = "https://api.github.com/rate_limit"

    for _ in range(4):
        responses.add(responses.GET, url, status=500)

    client = GitHubClient(token="fake-token")

    with pytest.raises(TransientServerError):
        client.get_rate_limit()

    assert len(responses.calls) == 4


@responses.activate
def test_rate_limit_waits_when_configured_to_wait():
    url = "https://api.github.com/rate_limit"
    reset_ts = int(time.time()) + 1

    responses.add(
        responses.GET,
        url,
        status=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_ts)},
    )
    responses.add(
        responses.GET,
        url,
        json={"rate": {"remaining": 100}},
        status=200,
    )

    client = GitHubClient(token="fake-token", wait_on_rate_limit=True)
    result = client.get_rate_limit()

    assert result == {"rate": {"remaining": 100}}
    assert len(responses.calls) == 2


@responses.activate
def test_rate_limit_fails_fast_when_configured_not_to_wait():
    url = "https://api.github.com/rate_limit"
    reset_ts = int(time.time()) + 1

    responses.add(
        responses.GET,
        url,
        status=429,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_ts)},
    )

    client = GitHubClient(token="fake-token", wait_on_rate_limit=False)

    with pytest.raises(RateLimitExceededError):
        client.get_rate_limit()


@responses.activate
def test_malformed_json_is_logged_and_treated_as_empty_page(caplog):
    url = "https://api.github.com/orgs/testorg/repos"

    responses.add(
        responses.GET,
        url,
        body="{invalid json",
        status=200,
        content_type="application/json",
    )

    client = GitHubClient(token="fake-token", raw_data_dir="test_data/raw")

    with caplog.at_level("ERROR"):
        records = client.get_org_repos("testorg", run_timestamp="20260101T000000Z")

    assert records == []
    assert "Malformed JSON" in caplog.text


@responses.activate
def test_get_repo_returns_parsed_json():
    responses.add(
        responses.GET,
        "https://api.github.com/repos/stripe/stripe-ruby",
        json={"id": 1953385, "name": "stripe-ruby"},
        status=200,
    )

    client = GitHubClient(token="fake-token")
    result = client.get_repo("stripe", "stripe-ruby")

    assert result["id"] == 1953385


def test_client_warns_when_no_token(monkeypatch, caplog):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with caplog.at_level("WARNING"):
        GitHubClient(token=None)

    assert "GITHUB_TOKEN not set" in caplog.text
