CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    records_fetched INTEGER NOT NULL DEFAULT 0 CHECK (records_fetched >= 0),
    records_valid INTEGER NOT NULL DEFAULT 0 CHECK (records_valid >= 0),
    records_quarantined INTEGER NOT NULL DEFAULT 0 CHECK (records_quarantined >= 0),
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_organization ON pipeline_runs (organization);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at ON pipeline_runs (started_at DESC);

CREATE TABLE IF NOT EXISTS github_repos (
    id BIGINT PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES pipeline_runs (run_id),
    organization TEXT NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    private BOOLEAN NOT NULL,
    html_url TEXT NOT NULL,
    description TEXT,
    stargazers_count INTEGER NOT NULL CHECK (stargazers_count >= 0),
    forks_count INTEGER NOT NULL CHECK (forks_count >= 0),
    open_issues_count INTEGER NOT NULL CHECK (open_issues_count >= 0),
    archived BOOLEAN NOT NULL,
    recently_pushed_while_archived BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    pushed_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (pushed_at >= created_at)
);

CREATE INDEX IF NOT EXISTS idx_github_repos_organization ON github_repos (organization);
CREATE INDEX IF NOT EXISTS idx_github_repos_run_id ON github_repos (run_id);

CREATE TABLE IF NOT EXISTS github_repos_quarantine (
    quarantine_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES pipeline_runs (run_id),
    organization TEXT NOT NULL,
    repo_id BIGINT,
    raw_payload JSONB NOT NULL,
    failed_field TEXT NOT NULL,
    error_message TEXT NOT NULL,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dedup_key TEXT NOT NULL,
    UNIQUE (organization, dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_quarantine_organization ON github_repos_quarantine (organization);
CREATE INDEX IF NOT EXISTS idx_quarantine_run_id ON github_repos_quarantine (run_id);