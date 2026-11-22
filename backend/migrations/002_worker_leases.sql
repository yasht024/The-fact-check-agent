BEGIN;

ALTER TABLE runs
    ADD COLUMN lease_id uuid,
    ADD COLUMN lease_expires_at timestamptz,
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN failure_code text,
    ADD CONSTRAINT runs_lease_fields_match CHECK (
        (lease_id IS NULL AND lease_expires_at IS NULL)
        OR (state = 'running' AND lease_id IS NOT NULL AND lease_expires_at IS NOT NULL)
    );

CREATE INDEX runs_worker_queue_idx ON runs (created_at, run_id)
    WHERE state IN ('queued', 'running');

COMMIT;
