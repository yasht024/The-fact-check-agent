"""Validated deployment entry points. Never print environment values."""
import argparse
import os
import sys
from backend.app.config import Settings


def command(role: str) -> list[str]:
    settings = Settings.from_env()
    if not settings.database_url:
        raise ValueError('DATABASE_URL must be configured.')
    if role == 'worker':
        if not settings.gemini_api_key or not settings.tavily_api_key:
            raise ValueError('Configure GEMINI_API_KEY and TAVILY_API_KEY on the worker.')
        return [sys.executable, '-m', 'backend.app.processing.worker']
    try:
        port = int(os.environ.get('PORT', '8000'))
    except ValueError:
        raise ValueError('PORT must be an integer between 1 and 65535.') from None
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be an integer between 1 and 65535.')
    return [sys.executable, '-m', 'uvicorn', 'backend.app.main:app', '--host', '0.0.0.0',
            '--port', str(port), '--no-access-log']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['web', 'worker'])
    args = parser.parse_args()
    try:
        args = command(args.role)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    os.execv(sys.executable, args)


if __name__ == '__main__':
    raise SystemExit(main())
