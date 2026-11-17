"""Copy official resources locally as opaque files; never print Gold contents.

No download, database connection, conversion, generated predictions or patient
database files. The destination stays Git-ignored; byte hashes verify fidelity.
"""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--clinsql', type=Path, required=True)
    p.add_argument('--ehrsql', type=Path, required=True)
    args = p.parse_args()
    target = ROOT / 'evals/official'
    ignored = subprocess.run(['git', 'check-ignore', '-q', '--no-index', 'evals/official/.probe'], cwd=ROOT)
    if ignored.returncode or subprocess.run(['git', 'ls-files', '--', 'evals/official'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip():
        raise SystemExit('Destination must be ignored and untracked')
    if target.exists():
        raise SystemExit('Destination exists; refusing to overwrite official resources')
    plan = []

    def add(source, relative):
        if not source.is_file():
            raise FileNotFoundError(source)
        plan.append((source, relative))

    counts = {}
    for split in ('validation', 'test'):
        directory = args.clinsql / 'data/splits' / split
        questions = sorted(directory.glob('*/*_level_queries/*/query_*.txt'))
        counts[split] = len(questions)
        for question in questions:
            task = question.parent
            names = [question, *task.glob('sql_*.sql'), *task.glob('result_*.csv'), task/'sql_rubric_tree.json', task/'results_rubric_tree.json']
            if len(names) != 5 or len({f.name for f in names}) != 5:
                raise ValueError('Expected one question, one SQL, one CSV and two rubrics')
            for file in names:
                add(file, Path('clinsql/data/splits') / file.relative_to(args.clinsql / 'data/splits'))
    if counts != {'validation': 190, 'test': 443}:
        raise ValueError(f'Unexpected local CLINSQL split counts: {counts}')
    add(args.clinsql/'README.md', Path('clinsql/README.upstream.md'))
    add(args.clinsql/'model_inference/mimic4_schema.txt', Path('clinsql/model_inference/mimic4_schema.txt'))
    for file in args.clinsql.glob('LICENSE*'):
        add(file, Path('clinsql')/file.name)
    add(args.ehrsql/'README.md', Path('ehrsql/README.upstream.md'))
    add(args.ehrsql/'LICENSE', Path('ehrsql/LICENSE'))
    dataset = args.ehrsql/'dataset/ehrsql'
    add(dataset/'tables.json', Path('ehrsql/dataset/ehrsql/tables.json'))
    add(dataset/'CHANGELOG.md', Path('ehrsql/dataset/ehrsql/CHANGELOG.md'))
    ehr_counts = {}
    for db in ('mimic_iii', 'eicu'):
        ehr_counts[db] = {}
        add(dataset/db/(db+'.sql'), Path('ehrsql/dataset/ehrsql')/db/(db+'.sql'))
        for split in ('train', 'valid', 'test'):
            file = dataset/db/(split+'.json')
            # Parse only to count records; no question, SQL or answer values enter logs.
            records = json.loads(file.read_text(encoding='utf-8'))
            ehr_counts[db][split] = len(records)
            del records
            add(file, Path('ehrsql/dataset/ehrsql')/db/file.name)
    manifest = []
    for source, relative in plan:
        destination = target/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        digest = sha(source)
        if sha(destination) != digest:
            raise ValueError('Copy hash mismatch')
        manifest.append({'path': relative.as_posix(), 'bytes': source.stat().st_size, 'sha256': digest})
    commit = subprocess.run(['git', '-C', str(args.ehrsql), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    provenance = {
        'purpose': 'local official benchmark resources; not a public redistribution grant',
        'sources': {'clinsql': {'repository': 'https://github.com/Barryshen1/ClinSQL', 'git_commit': None, 'note': 'Local archive snapshot; integrity identified by per-file SHA-256, no fabricated commit'},
                    'ehrsql': {'repository': 'https://github.com/glee4810/EHRSQL', 'git_commit': commit}},
        'clinsql_task_counts': counts, 'ehrsql_record_counts': ehr_counts,
        'ehrsql_standalone_answer_csv_available': False,
        'clinsql_gold_dialect': 'Original upstream BigQuery SQL, not our PostgreSQL port',
        'test_gold_read_for_generation_or_semantic_analysis': False,
        'excluded': ['SQLite/other clinical databases', 'generated predictions', 'local PostgreSQL Gold ports', 'credentials', 'historical model outputs'],
        'files': manifest,
    }
    (target/'MANIFEST.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'files_copied_and_hash_verified': len(manifest), 'bytes': sum(f['bytes'] for f in manifest), 'clinsql_tasks': counts, 'ehrsql_records': ehr_counts}, ensure_ascii=False))


if __name__ == '__main__':
    main()
