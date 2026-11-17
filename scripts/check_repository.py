"""Prevent accidental inclusion of medical data and local secrets."""
import json
import subprocess
from pathlib import Path
root = Path(__file__).resolve().parents[1]
blocked = {'.csv', '.tsv', '.sqlite', '.sqlite3', '.db', '.parquet', '.dump', '.bak', '.pem', '.key', '.gz', '.zip'}
skip = {'.git', '.venv', '.conda', '__pycache__', 'node_modules', '.pytest_cache'}
official = root / 'evals/official'
local_roots = {'runs', 'artifacts', 'exports', 'catalog', 'audit'}
for local in sorted(local_roots):
    if not (root / local).exists():
        continue
    tracked = subprocess.run(['git', 'ls-files', '--', local], cwd=root, capture_output=True, text=True, check=True)
    ignored = subprocess.run(['git', 'check-ignore', '-q', '--no-index', local + '/.exclusion-probe'], cwd=root)
    if tracked.stdout.strip() or ignored.returncode:
        raise SystemExit(f'Local runtime directory must be fully Git-ignored and untracked: {local}')
if official.exists():
    # A local Gold archive is allowed only when Git excludes it and no file is
    # already tracked. Never weaken the public-tree data checks elsewhere.
    tracked = subprocess.run(['git', 'ls-files', '--', 'evals/official'], cwd=root, capture_output=True, text=True, check=True)
    ignored = subprocess.run(['git', 'check-ignore', '-q', '--no-index', 'evals/official/.exclusion-probe'], cwd=root)
    if tracked.stdout.strip() or ignored.returncode != 0:
        raise SystemExit('Official resources must be fully Git-ignored and untracked')
for path in root.rglob('*'):
    rel = path.relative_to(root)
    if set(rel.parts) & skip or not path.is_file():
        continue
    if rel.parts[0] in local_roots:
        continue
    if rel.parts[:2] == ('evals', 'official'):
        continue
    if path.suffix.lower() in blocked or path.name in {'.env', '.pgpass', 'pgpass.conf'} or 'credentials' in rel.parts:
        raise SystemExit(f'Forbidden public repository file: {rel}')
    if rel.parts[0] == 'config' and path.suffix == '.json':
        database = json.loads(path.read_text(encoding='utf-8')).get('database', {})
        if any(any(word in k.lower() for word in ('password', 'token', 'secret')) for k in database):
            raise SystemExit(f'Credential field present: {rel}')
print('Repository data/config exclusion checks passed; manual secret review is still required.')
