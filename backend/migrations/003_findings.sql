BEGIN;
ALTER TABLE runs
    ADD COLUMN processing_deadline timestamptz,
    ADD COLUMN completed_at timestamptz,
    ADD COLUMN expires_at timestamptz,
    ADD COLUMN coverage_gaps jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE claims (
    run_id uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    sequence integer NOT NULL CHECK (sequence > 0),
    claim jsonb NOT NULL,
    PRIMARY KEY (run_id, sequence)
);
CREATE TABLE claim_checks (
    run_id uuid NOT NULL,
    sequence integer NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'incomplete')),
    verdict text CHECK (verdict IN ('verified', 'inaccurate', 'false')),
    explanation text,
    reason text,
    correction jsonb,
    evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    search_attempts integer NOT NULL DEFAULT 0 CHECK (search_attempts >= 0),
    assessment_attempts integer NOT NULL DEFAULT 0 CHECK (assessment_attempts >= 0),
    search_events jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_failures jsonb NOT NULL DEFAULT '[]'::jsonb,
    checked_at timestamptz,
    PRIMARY KEY (run_id, sequence),
    FOREIGN KEY (run_id, sequence) REFERENCES claims(run_id, sequence) ON DELETE CASCADE,
    CHECK ((status = 'completed' AND verdict IS NOT NULL) OR (status <> 'completed' AND verdict IS NULL)),
    CHECK (correction IS NULL OR verdict = 'inaccurate')
);
CREATE TABLE evidence (
    run_id uuid NOT NULL,
    sequence integer NOT NULL,
    evidence_id text NOT NULL,
    source_url text NOT NULL,
    title text NOT NULL,
    retrieved_at timestamptz NOT NULL,
    publication_date text,
    reporting_period text,
    passage text NOT NULL,
    page_number integer,
    PRIMARY KEY (run_id, sequence, evidence_id),
    FOREIGN KEY (run_id, sequence) REFERENCES claim_checks(run_id, sequence) ON DELETE CASCADE
);
CREATE INDEX runs_expiry_idx ON runs(expires_at) WHERE expires_at IS NOT NULL;
COMMIT;
