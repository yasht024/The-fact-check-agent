BEGIN;

CREATE TABLE runs (
    run_id uuid PRIMARY KEY,
    access_token_hash bytea NOT NULL CHECK (octet_length(access_token_hash) = 32),
    state text NOT NULL DEFAULT 'queued' CHECK (
        state IN ('queued', 'running', 'completed', 'completed_with_gaps', 'failed')
    ),
    stage text NOT NULL DEFAULT 'queued',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE uploads (
    run_id uuid PRIMARY KEY REFERENCES runs (run_id) ON DELETE CASCADE,
    pdf bytea NOT NULL
);

COMMIT;
