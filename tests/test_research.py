from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meddb.config import load_settings
from meddb.errors import SecurityError
from meddb.models import QueryResult
from medresearch.benchmark import compare_results, discover_validation, file_hash, run_benchmark
from medresearch.cli import main
from medresearch.contracts import LIST_FIELDS, SPEC_FIELDS, proposal_schema, validate_proposal
from medresearch.provider import ResponsesGenerator
from medresearch.runtime import ResearchRunner, generate_proposal
from medresearch.tools import MedDBTool


ROOT = Path(__file__).resolve().parents[1]


def proposal(question="Synthetic question", task_id="synthetic"):
    spec = {name: "not applicable" for name in SPEC_FIELDS}
    spec.update({name: [] for name in LIST_FIELDS})
    spec.update(task_id=task_id, question=question, dataset="mimiciv", dataset_version="3.1",
                analysis_unit="admission", objective="descriptive", output_columns=["n"])
    return {"status": "ready", "spec": spec,
            "sql": "SELECT COUNT(*) AS n FROM mimiciv_hosp.admissions",
            "params_json": "{}", "limitations": ["Synthetic test only"]}


def result(truncated=False):
    return QueryResult("synthetic_query", ["n"], [(2,)], 1, truncated, 1.0)


def official_fixture(root: Path):
    base = root / "clinsql/data/splits/validation/Synthetic/easy_level_queries/001"
    base.mkdir(parents=True)
    (base / "query_001.txt").write_text("Synthetic question", encoding="utf-8")
    (base / "result_001.csv").write_text("n\n2\n", encoding="utf-8")
    (root / "MANIFEST.json").write_text(json.dumps({"files": [
        {"path": p.relative_to(root).as_posix(), "sha256": file_hash(p)}
        for p in base.iterdir()]}), encoding="utf-8")
    return base


class ContractsTests(unittest.TestCase):
    def test_published_schema_and_manual_example_match_contract(self):
        published = json.loads((ROOT / "context/schemas/research-proposal.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(published, proposal_schema())
        example = json.loads((ROOT / "examples/mimic-admission-count.proposal.json").read_text(encoding="utf-8"))
        validate_proposal(example)

    def test_rejects_unsupported_objective_and_nonfinite_params(self):
        value = proposal()
        value["spec"]["objective"] = "causal"
        with self.assertRaises(ValueError):
            validate_proposal(value)
        for encoded in ('{"x": NaN}', '{"x": 1e400}', '[]', '{"x": [1]}'):
            value = proposal()
            value["params_json"] = encoded
            with self.assertRaises(ValueError):
                validate_proposal(value)

    def test_generation_rejects_changed_question(self):
        generator = mock.Mock()
        generator.generate.return_value = proposal("Other question")
        with self.assertRaisesRegex(ValueError, "identity"):
            generate_proposal(generator, question="Synthetic question", task_id="synthetic",
                              dataset_version="3.1", schema="synthetic", rules="synthetic")

    def test_provider_is_tool_free_and_accepts_only_public_context(self):
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(proposal())}]}]}
        handle = mock.MagicMock()
        handle.__enter__.return_value = io.StringIO(json.dumps(response))
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-key"}):
            with mock.patch("urllib.request.urlopen", return_value=handle) as send:
                generated = ResponsesGenerator("synthetic-model").generate(
                    "Synthetic question", "synthetic", "3.1", "schema", "rules")
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["tools"], [])
        self.assertFalse(payload["store"])
        self.assertNotIn("synthetic-key", payload["input"])
        self.assertEqual(generated, proposal())


class RuntimeTests(unittest.TestCase):
    def test_resume_does_not_reexecute_and_refuses_changed_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            tool = mock.Mock()
            tool.execute.return_value = result()
            runner = ResearchRunner(tool, Path(temp) / "run", {"schema": "v1"})
            self.assertEqual(runner.run(proposal())["status"], "completed")
            runner.run(proposal(), resume=True)
            self.assertEqual(tool.execute.call_count, 1)
            changed = proposal()
            changed["spec"]["denominator"] = "different"
            with self.assertRaisesRegex(ValueError, "changed"):
                runner.run(changed, resume=True)
            (Path(temp) / "run/result.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact"):
                runner.run(proposal(), resume=True)

    def test_unsupported_does_not_touch_database(self):
        with tempfile.TemporaryDirectory() as temp:
            tool = mock.Mock()
            value = proposal()
            value.update(status="unsupported", sql="")
            state = ResearchRunner(tool, Path(temp) / "run", {}).run(value)
            self.assertEqual(state["status"], "unsupported")
            tool.execute.assert_not_called()

    def test_failure_is_recorded_without_clinical_error_text_and_can_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            tool = mock.Mock()
            tool.execute.side_effect = RuntimeError("synthetic-sensitive-value")
            runner = ResearchRunner(tool, Path(temp) / "run", {})
            self.assertEqual(runner.run(proposal())["status"], "failed")
            self.assertNotIn("synthetic-sensitive-value", (Path(temp) / "run/state.json").read_text())
            tool.execute.side_effect = None
            tool.execute.return_value = result()
            self.assertEqual(runner.run(proposal(), resume=True)["status"], "completed")


class ToolTests(unittest.TestCase):
    def test_rejects_writes_inner_only_aggregate_and_identifiers_before_connecting(self):
        tool = MedDBTool(load_settings(ROOT / "config/meddb-mimic-official.json"))
        queries = ["DELETE FROM mimiciv_hosp.admissions",
                   "SELECT COUNT(*) AS n INTO mimiciv_hosp.bad FROM mimiciv_hosp.admissions",
                   "SELECT COUNT(*) OVER () AS n FROM mimiciv_hosp.admissions",
                   "WITH x AS (SELECT COUNT(*) AS n FROM mimiciv_hosp.admissions) SELECT n FROM x",
                   "SELECT subject_id, COUNT(*) AS n FROM mimiciv_hosp.admissions GROUP BY subject_id",
                   "SELECT MIN(subject_id) AS n FROM mimiciv_hosp.admissions",
                   "SELECT COUNT(*) AS n FROM seer8.tumors",
                   "SELECT COUNT(*) AS n FROM mimiciv_hosp.admissions; DELETE FROM mimiciv_hosp.admissions"]
        with mock.patch.object(tool.database, "connect") as connect:
            for sql in queries:
                value = proposal()
                value["sql"] = sql
                with self.subTest(sql=sql), self.assertRaises(SecurityError):
                    tool.execute(value)
            connect.assert_not_called()

    def test_aggregate_uses_meddb_core_and_checks_output_contract(self):
        tool = MedDBTool(load_settings(ROOT / "config/meddb-mimic-official.json"))
        with mock.patch.object(tool, "preflight"), mock.patch.object(tool.catalog, "validate_query_table"), \
                mock.patch.object(tool.database, "execute", return_value=result()) as execute, \
                mock.patch("medresearch.tools.AuditLog"):
            self.assertEqual(tool.execute(proposal()).rows, [(2,)])
            self.assertFalse(execute.call_args.kwargs["detail"])
            value = proposal()
            value["spec"]["output_columns"] = ["different"]
            with self.assertRaisesRegex(SecurityError, "columns"):
                tool.execute(value)


class BenchmarkTests(unittest.TestCase):
    def test_duplicate_rows_nulls_aliases_tolerance_and_truncation(self):
        self.assertFalse(compare_results(["n"], [[1], [1]], ["n"], [[1], [2]])["value_match"])
        self.assertFalse(compare_results(["n"], [[None]], ["n"], [[0]])["value_match"])
        comparison = compare_results(["n"], [[1.00000001]], ["count"], [["1"]])
        self.assertTrue(comparison["value_match"])
        self.assertFalse(comparison["alias_sensitive_match"])
        self.assertFalse(compare_results(["n"], [[2]], ["n"], [[2]], truncated=True)["value_match"])
        self.assertTrue(compare_results(["n"], [], ["n"], [])["value_match"])
        self.assertFalse(compare_results(["n"], [[float("nan")]], ["n"], [["nan"]])["value_match"])

    def test_tolerance_matching_is_not_greedy(self):
        # First row can match either reference, second only the first.
        self.assertTrue(compare_results(["n"], [[1.0000005], [0.9999995]],
                                       ["n"], [[1.0], [1.0000014]])["value_match"])

    def test_many_duplicate_rows_keep_multiplicity(self):
        rows = [[2]] * 1001
        self.assertTrue(compare_results(["n"], rows, ["n"], rows)["value_match"])

    def test_model_provenance_tampering_prevents_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "proposal.json"
            path.write_text(json.dumps(proposal()), encoding="utf-8")
            path.with_suffix(".json.provenance.json").write_text(
                json.dumps({"proposal_sha256": "invalid"}), encoding="utf-8")
            with mock.patch("medresearch.cli.MedDBTool") as tool, \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                code = main(["run", "--proposal", str(path), "--config",
                    str(ROOT / "config/meddb-mimic-official.json"), "--output", str(Path(temp) / "run")])
            self.assertEqual(code, 1)
            tool.return_value.execute.assert_not_called()

    def test_discovery_checks_hash_and_never_selects_test(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = official_fixture(root)
            task = discover_validation(root, 1)[0]
            self.assertTrue(task["task_id"].startswith("validation/"))
            (base / "query_001.txt").write_text("modified", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "modified"):
                discover_validation(root, 1)

    def test_generation_failure_never_loads_reference_and_uses_full_denominator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official_fixture(root / "official")
            generator = mock.Mock()
            generator.generate.side_effect = RuntimeError("synthetic failure")
            with mock.patch("medresearch.benchmark.score_task") as scorer:
                summary = run_benchmark(official=root / "official", output=root / "run",
                    limit=1, generator=generator, tool=mock.Mock(), schema="schema", rules="rules", provenance={})
            scorer.assert_not_called()
            self.assertEqual(summary["selected"], 1)
            self.assertEqual(summary["generation_rate"], 0)
            self.assertEqual(summary["executed"], 0)

    def test_full_synthetic_benchmark_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official_fixture(root / "official")
            generator = mock.Mock()
            task_id = "validation/Synthetic/easy_level_queries/001"
            generator.generate.return_value = proposal(task_id=task_id)
            tool = mock.Mock()
            tool.execute.return_value = result()
            kwargs = dict(official=root / "official", output=root / "run", limit=1,
                          generator=generator, tool=tool, schema="schema", rules="rules", provenance={})
            summary = run_benchmark(**kwargs)
            self.assertEqual(summary["value_match_rate"], 1)
            self.assertIsNone(summary["official_rubric_score"])
            run_benchmark(**kwargs, resume=True)
            self.assertEqual(tool.execute.call_count, 1)
            self.assertEqual(generator.generate.call_count, 1)

    def test_default_benchmark_preflight_has_no_network_or_database_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official_fixture(root / "official")
            with mock.patch("medresearch.cli.read_context", return_value=("schema", "rules")), \
                    mock.patch("medresearch.cli.MedDBTool") as tool, \
                    mock.patch("medresearch.cli.ResponsesGenerator") as provider, \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                code = main(["benchmark", "--official", str(root / "official"), "--limit", "1"])
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(output.getvalue())["benchmark_executed"])
            tool.return_value.preflight.assert_not_called()
            provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
