CREATE TABLE IF NOT EXISTS github_delivery (
    id text PRIMARY KEY,
    installation_id bigint NOT NULL,
    repository_id bigint NOT NULL,
    pr_number integer NOT NULL,
    next_dispatch_at timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL DEFAULT 0,
    processed_at timestamptz,
    outcome text
);

CREATE INDEX IF NOT EXISTS github_delivery_pending
    ON github_delivery (next_dispatch_at) WHERE processed_at IS NULL;

CREATE TABLE IF NOT EXISTS github_review_run (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    installation_id bigint NOT NULL,
    repository_id bigint NOT NULL,
    pr_number integer NOT NULL,
    base_ref text NOT NULL,
    base_sha text NOT NULL,
    head_sha text NOT NULL,
    state text NOT NULL DEFAULT 'running',
    coverage_partial boolean NOT NULL DEFAULT false,
    finding_fingerprints text[] NOT NULL DEFAULT '{}',
    github_review_id bigint,
    UNIQUE (installation_id, repository_id, pr_number, base_ref, base_sha, head_sha)
);

CREATE TABLE IF NOT EXISTS github_model_usage (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES github_review_run (id),
    installation_id bigint NOT NULL,
    billing_month date NOT NULL,
    microdollars bigint NOT NULL CHECK (microdollars >= 0),
    input_tokens integer,
    output_tokens integer
);

CREATE INDEX IF NOT EXISTS github_model_usage_month
    ON github_model_usage (installation_id, billing_month);

CREATE INDEX IF NOT EXISTS github_model_usage_run ON github_model_usage (run_id);
