"""Apply reviewed service settings through the authenticated Railway CLI.

No secrets are supplied by this script. DATABASE_URL/provider variables use a
separate private input workflow. Legacy railway.json config paths are not used.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MUTATION = '''mutation ConfigureService($service: String!, $environment: String!, $input: ServiceInstanceUpdateInput!) {
  serviceInstanceUpdate(serviceId: $service, environmentId: $environment, input: $input)
}'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=['api', 'worker'], required=True)
    parser.add_argument('--service', required=True, help='Exact Railway service ID')
    parser.add_argument('--environment', required=True, help='Exact Railway environment ID')
    args = parser.parse_args()
    cli = os.environ.get('RAILWAY_CLI') or shutil.which('railway')
    if not cli:
        print('Install/authenticate the Railway CLI or set RAILWAY_CLI to its executable path.', file=sys.stderr)
        return 1
    settings = json.loads((ROOT / f'deploy/railway-{args.role}.settings.json').read_text())
    variables = {'service': args.service, 'environment': args.environment, 'input': settings}
    return subprocess.run([cli, 'api', MUTATION, '--variables', json.dumps(variables)], cwd=ROOT).returncode


if __name__ == '__main__':
    raise SystemExit(main())
