"""Apply ordered SQL migrations once, under a database advisory lock.

For fresh databases or databases initialized by this runner. Existing databases
that were migrated manually must be reviewed before adopting the ledger.
"""
import hashlib
import os
from pathlib import Path
import sys
import psycopg

MIGRATIONS = Path(__file__).parent / 'migrations'
LOCK_ID = 701193004


class MigrationError(Exception):
    pass


def apply_migrations(database_url: str) -> list[str]:
    applied = []
    with psycopg.connect(database_url, autocommit=True, connect_timeout=10) as db:
        db.execute('SELECT pg_advisory_lock(%s)', (LOCK_ID,))
        try:
            ledger_exists = db.execute("SELECT to_regclass('schema_migrations')").fetchone()[0]
            if not ledger_exists and db.execute("SELECT to_regclass('runs')").fetchone()[0]:
                raise MigrationError('Existing untracked schema: review previously applied migrations before using this runner.')
            db.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
                name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())''')
            for path in sorted(MIGRATIONS.glob('*.sql')):
                source = path.read_text(encoding='utf-8')
                checksum = hashlib.sha256(source.encode()).hexdigest()
                saved = db.execute('SELECT sha256 FROM schema_migrations WHERE name=%s', (path.name,)).fetchone()
                if saved:
                    if saved[0] != checksum:
                        raise MigrationError(f'Previously applied migration changed: {path.name}')
                    continue
                # The standalone SQL files include transaction markers for psql.
                # Keep their body and ledger insert inside one psycopg transaction.
                lines = source.strip().splitlines()
                if lines[0].strip().upper() != 'BEGIN;' or lines[-1].strip().upper() != 'COMMIT;':
                    raise MigrationError(f'Migration must have explicit transaction boundaries: {path.name}')
                with db.transaction():
                    db.execute("SET LOCAL lock_timeout = '30s'")
                    db.execute('\n'.join(lines[1:-1]))
                    db.execute('INSERT INTO schema_migrations (name,sha256) VALUES (%s,%s)', (path.name, checksum))
                applied.append(path.name)
        finally:
            db.execute('SELECT pg_advisory_unlock(%s)', (LOCK_ID,))
    return applied


def main():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print('Configure DATABASE_URL before applying migrations.', file=sys.stderr)
        return 1
    try:
        applied = apply_migrations(database_url)
    except MigrationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except psycopg.Error as error:
        print(f'Database migration failed (SQLSTATE {error.sqlstate or "connection_error"}).', file=sys.stderr)
        return 1
    print(f'Migrations ready: {len(applied)} applied.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
