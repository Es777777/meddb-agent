"""Offline checks: never query PostgreSQL or call an LLM."""
import ast
import os
from pathlib import Path
import subprocess
import sys
root = Path(__file__).resolve().parents[1]
env = dict(os.environ, PYTHONPATH=str(root / 'src'), PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
for folder in ('src', 'scripts', 'tests', 'evals/legacy'):
    for path in (root / folder).rglob('*.py'):
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
subprocess.run([sys.executable, str(root / 'scripts/check_repository.py')], env=env, cwd=root, check=True)
subprocess.run([sys.executable, str(root / 'scripts/run_meddb.py'), '--help'], env=env, cwd=root, check=True)
subprocess.run([sys.executable, str(root / 'scripts/run_research.py'), '--help'], env=env, cwd=root, check=True)
subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], env=env, cwd=root, check=True)
print('Offline checks passed; database and LLM integration were not tested.')
