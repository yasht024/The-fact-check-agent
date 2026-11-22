"""Deployment entry points and real PostgreSQL migration release gate."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from backend import deploy, migrate


class DeploymentCommandTests(unittest.TestCase):
    def test_worker_requires_database_and_provider_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'DATABASE_URL'):
                deploy.command('worker')
        with patch.dict(os.environ, {'DATABASE_URL': 'private-database'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'GEMINI_API_KEY and TAVILY_API_KEY'):
                deploy.command('worker')
        with patch.dict(os.environ, {'DATABASE_URL': 'private-database', 'GEMINI_API_KEY': 'private-key', 'TAVILY_API_KEY': 'private-key'}, clear=True):
            command = deploy.command('worker')
            self.assertEqual(command[1:], ['-m', 'backend.app.processing.worker'])
            self.assertNotIn('private', ' '.join(command))

    def test_web_binds_platform_port_without_provider_keys_or_access_logs(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'private-database', 'PORT': '8123'}, clear=True):
            command = deploy.command('web')
            self.assertIn('0.0.0.0', command)
            self.assertIn('8123', command)
            self.assertIn('--no-access-log', command)
            self.assertNotIn('private', ' '.join(command))
        for value in ['0', '65536', 'invalid']:
            with patch.dict(os.environ, {'DATABASE_URL': 'private-database', 'PORT': value}, clear=True):
                with self.assertRaises(ValueError): deploy.command('web')


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for deployment migration tests.')
class DeploymentMigrationTests(unittest.TestCase):
    def setUp(self):
        schema = 'deployment_test_' + uuid4().hex
        admin = psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True)
        self.addCleanup(admin.close)
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        self.addCleanup(admin.execute, sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        self.url = make_conninfo(os.environ['TEST_DATABASE_URL'], options=f'-csearch_path={schema}')

    def test_concurrent_fresh_release_applies_once_and_reruns_safely(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(migrate.apply_migrations, [self.url, self.url]))
        self.assertEqual(sorted(map(len, results)), [0, 3])
        with psycopg.connect(self.url) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 3)
            for table in ['runs', 'uploads', 'claims', 'claim_checks', 'evidence']:
                self.assertIsNotNone(db.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0])
        self.assertEqual(migrate.apply_migrations(self.url), [])

    def test_rejects_untracked_schema_and_changed_migration(self):
        with psycopg.connect(self.url) as db: db.execute('CREATE TABLE runs (run_id uuid)')
        with self.assertRaisesRegex(migrate.MigrationError, 'untracked schema'):
            migrate.apply_migrations(self.url)
        with psycopg.connect(self.url) as db: db.execute('DROP TABLE runs')
        migrate.apply_migrations(self.url)
        with psycopg.connect(self.url) as db:
            db.execute("UPDATE schema_migrations SET sha256='changed' WHERE name='001_runs_and_uploads.sql'")
        with self.assertRaisesRegex(migrate.MigrationError, 'migration changed'):
            migrate.apply_migrations(self.url)

    def test_failed_migration_rolls_back_its_body_and_ledger(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / '001_test.sql').write_text('BEGIN;\nCREATE TABLE first_release (id int);\nCOMMIT;')
            (path / '002_test.sql').write_text('BEGIN;\nCREATE TABLE interrupted (id int);\nSELECT nonexistent_column;\nCOMMIT;')
            with patch.object(migrate, 'MIGRATIONS', path):
                with self.assertRaises(psycopg.Error): migrate.apply_migrations(self.url)
        with psycopg.connect(self.url) as db:
            self.assertEqual(db.execute('SELECT name FROM schema_migrations').fetchall(), [('001_test.sql',)])
            self.assertIsNone(db.execute("SELECT to_regclass('interrupted')").fetchone()[0])
