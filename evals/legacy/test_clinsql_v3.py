"""Behavioral checks for aliases, truncation, frozen context and static SQL checks."""
import importlib.util
import unittest
import threading
import time
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import run_clinsql_model_generation as runner
import run_clinsql_validation_pilot as scorer
import low_impact_benchmark as impact
import mimic_dictionary_grounding as grounding
import audit_clinsql_v4_development as v4_audit
import run_clinsql_v4_full as full

path = Path(r"C:\Users\lenovo\.codex\skills\mimic-postgres-cli\scripts\validate_generated_sql.py")
spec = importlib.util.spec_from_file_location("validator", path)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class Contracts(unittest.TestCase):
    def test_adaptive_workers_preserve_reserve_and_cap_at_eight(self):
        self.assertEqual(impact.adaptive_workers({"free_gb": 3.6, "free_fraction": .1125, "cpu_pct": 5}), 0)
        self.assertEqual(impact.adaptive_workers({"free_gb": 8, "free_fraction": .25, "cpu_pct": 5}), 3)
        self.assertEqual(impact.adaptive_workers({"free_gb": 12, "free_fraction": .375, "cpu_pct": 5}), 8)
        self.assertEqual(impact.adaptive_workers({"free_gb": 12, "free_fraction": .375, "cpu_pct": 45}), 4)
        self.assertEqual(impact.adaptive_workers({"free_gb": 12, "free_fraction": .375, "cpu_pct": 80}), 0)

    def test_configured_model_cap_is_shared_across_calls(self):
        active = maximum = 0
        lock = threading.Lock()
        @impact.bounded_model_call
        def fake_generation():
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(.02)
            with lock:
                active -= 1
            return {"status": "generated"}
        impact.configure_model_slots(4)
        try:
            with patch.object(impact, "wait_for_capacity", return_value=(True, {})):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    results = list(executor.map(lambda _: fake_generation(), range(12)))
            self.assertEqual(len(results), 12)
            self.assertLessEqual(maximum, 4)
            self.assertGreater(maximum, 1)
        finally:
            impact.configure_model_slots(2)

    def test_frozen_evaluation_preserves_question_hash_and_split_boundary(self):
        selection = [{"task_key": "validation/Example/easy/001", "question_sha256": "questionhash"}]
        pack = {"purpose": "frozen_validation_evaluation", "profile_sha256": "profilehash", "tasks": selection}
        runner.validate_evaluation_manifest(pack, selection, "profilehash")
        with self.assertRaises(ValueError):
            runner.validate_evaluation_manifest(pack, selection, "changedprofile")
        with self.assertRaises(ValueError):
            runner.validate_evaluation_manifest(pack, [{**selection[0], "question_sha256": "changedquestion"}], "profilehash")
        with self.assertRaises(ValueError):
            runner.validate_evaluation_manifest({**pack, "tasks": [{**selection[0], "task_key": "test/Example/easy/001"}]}, selection, "profilehash")

    def test_atomic_checkpoint_is_valid_json_and_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            runner.atomic_json(path, {"status": "first"})
            runner.atomic_json(path, {"status": "second"})
            self.assertEqual(json.loads(path.read_text())["status"], "second")
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_full_metrics_do_not_confuse_execution_with_gold(self):
        result = full.metric([self.score_fixture("generated", True, False)], "official")
        self.assertEqual(result["executed"], 1)
        self.assertEqual(result["gold_value_correct"], 0)
        self.assertEqual(result["gold_value_pct"], 0)
        self.assertEqual(full.wilson(1, 1)[1], 100)

    @staticmethod
    def score_fixture(generation, executed=False, correct=False):
        return {
            **{target: {"generation": {"status": generation}, "execution": {"status": "executed" if executed else "not_executed"}} for target in ("official", "local")},
            "comparisons": {
                **{f"{target}_vs_reference": {"unordered_numeric_tolerant_value_only_match": correct} for target in ("official", "local")},
                "official_vs_local": {},
            },
        }

    def test_resource_deferred_is_unmeasured_not_zero_accuracy(self):
        result = v4_audit.metrics([self.score_fixture("resource_deferred")])
        self.assertEqual(result["model_attempted"], 0)
        self.assertIsNone(result["targets"]["official"]["gold_accuracy_pct"])

    def test_real_failed_attempt_remains_in_accuracy_denominator(self):
        result = v4_audit.metrics([self.score_fixture("model_abstain"), self.score_fixture("generated", True, True)])
        self.assertEqual(result["targets"]["official"]["gold_accuracy_pct"], 50)

    def test_executable_wrong_answer_does_not_become_correct(self):
        result = v4_audit.metrics([self.score_fixture("generated", True, False)])
        self.assertEqual(result["targets"]["official"]["executed"], 1)
        self.assertEqual(result["targets"]["official"]["gold_accuracy_pct"], 0)

    def test_development_profile_rejects_unlisted_questions(self):
        path = Path(r"F:\database\benchmark\configs\clinsql_v4_exposed_development_20261027.json")
        pack = runner.load_development_manifest(path, {"validation/Medication_Management/easy/010"})
        self.assertIn("already_exposed_ids", pack)
        with self.assertRaisesRegex(ValueError, "unlisted/held-out"):
            runner.load_development_manifest(path, {"validation/Medication_Management/hard/999"})

    def test_lab_and_disease_concepts_have_separate_budgets(self):
        plans = grounding.retrieval_plan("Median platelet count among sepsis admissions")
        self.assertEqual({(p["concept"], p["dictionary_index"]) for p in plans}, {("platelet", 0), ("sepsis", 2)})
        self.assertNotIn("platelet", next(p for p in plans if p["concept"] == "sepsis")["terms"])
        self.assertIn("septic shock", next(p for p in plans if p["concept"] == "sepsis")["terms"])

    def test_demographic_statistics_do_not_trigger_four_dictionary_scans(self):
        plans = grounding.retrieval_plan("What is the interquartile range of hospital mortality among male patients aged 51 to 61?")
        self.assertEqual(plans, [])

    def test_medication_class_uses_ingredients_not_icd_search(self):
        registry = runner.load_json(Path(r"C:\Users\lenovo\.codex\skills\mimic-postgres-cli\references\medication-class-registry.json"))
        classes = grounding.medication_context("inpatient NITRATES prescription duration", registry)
        self.assertEqual(classes[0]["ingredient_patterns"], ["%nitroglycerin%", "%isosorbide%"])
        self.assertEqual(grounding.retrieval_plan("inpatient nitrate prescription duration", classes), [])
        self.assertEqual(grounding.medication_context("nitrated specimen", registry), [])

    def test_dictionary_query_is_parameterized_and_read_only(self):
        for descriptor in grounding.DICTIONARIES:
            sql = grounding.dictionary_sql(*descriptor)
            self.assertEqual(validator.validate(sql, {"terms": ["sepsis"], "prefixes": ["sepsis%"], "patterns": ["%sepsis%"]}, "mimiciv", None), [])
        with self.assertRaises(ValueError):
            grounding.dictionary_sql("mimiciv_hosp.patients", "gender", "subject_id", ("subject_id",))

    def test_busy_machine_defers_work(self):
        ready, state = impact.wait_for_capacity(
            snapshot=lambda: {"cpu_pct": 95, "free_gb": 1, "free_fraction": 0.05},
            policy={**impact.POLICY, "max_wait_seconds": 0},
        )
        self.assertFalse(ready)

    def test_prefetch_is_bounded_and_preparation_stays_on_main_thread(self):
        active = 0
        maximum = 0
        lock = threading.Lock()
        main_id = threading.get_ident()
        preparations = []

        def prepare(path):
            preparations.append((path, threading.get_ident()))

        def generate(path):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return path * 2

        result = list(impact.shared_generation_stream([1, 2, 3, 4], prepare, generate, 2))
        self.assertEqual(result, [(1, 2), (2, 4), (3, 6), (4, 8)])
        self.assertLessEqual(maximum, 2)
        self.assertTrue(all(thread_id == main_id for _, thread_id in preparations))

    def test_db_resource_options_are_session_local_and_preserve_existing(self):
        original = {"PGOPTIONS": "-c application_name=regression"}
        env = impact.bounded_db_env(original)
        self.assertIn("max_parallel_workers_per_gather=0", env["PGOPTIONS"])
        self.assertIn("jit=off", env["PGOPTIONS"])
        self.assertEqual(original["PGOPTIONS"], "-c application_name=regression")

    def test_development_gold_cannot_overlap_scored_ids(self):
        pack_path = Path(r"F:\database\benchmark\configs\clinsql_declared_development_examples_20261027.json")
        key = "validation/Diagnostic_Procedures/easy/001"
        with self.assertRaisesRegex(ValueError, "Gold leakage"):
            runner.load_development_examples(pack_path, {key})
        accepted = runner.load_development_examples(pack_path, {"validation/Medication_Management/easy/010"})
        self.assertEqual(len(accepted["examples"]), 6)

    def test_alias_is_not_a_numeric_error(self):
        result = scorer.compare(["gold_name"], [{"gold_name": 3}], {
            "status": "executed", "columns": ["new_name"], "rows": [{"new_name": 3}],
        })
        self.assertFalse(result["unordered_numeric_tolerant_match"])
        self.assertTrue(result["unordered_numeric_tolerant_value_only_match"])

    def test_changed_numeric_value_still_fails(self):
        result = scorer.compare(["x"], [{"x": 3}], {
            "status": "executed", "columns": ["y"], "rows": [{"y": 4}],
        })
        self.assertFalse(result["unordered_numeric_tolerant_value_only_match"])

    def test_truncation_cannot_score_as_correct(self):
        result = scorer.compare(["x"], [{"x": 3}], {
            "status": "executed", "columns": ["x"], "rows": [{"x": 3}], "truncated": True,
        })
        self.assertFalse(result["comparable"])

    def test_sql_parser_handles_literals_ctes_and_extract(self):
        query = """WITH x AS (SELECT subject_id, anchor_year FROM mimiciv_hosp.patients)
        SELECT COUNT(*) FROM x WHERE anchor_year > EXTRACT(YEAR FROM CAST(:t AS timestamp))
        AND 'delete; :fake $1 ? %s' <> 'nothing'"""
        self.assertEqual(validator.validate(query, {"t": "2100-01-01"}, "mimiciv", None), [])

    def test_sql_parser_rejects_unsafe_or_wrong_contract(self):
        for sql, params, error in [
            ("SELECT * FROM mimiciv_hosp.patients", {}, "select_star"),
            ("SELECT COUNT(*) FROM mimiciii.patients", {}, "wrong_or_unapproved_schema"),
            ("SELECT COUNT(*) FROM patients", {}, "unqualified_physical_source"),
            ("DELETE FROM mimiciv_hosp.patients", {}, "not_select_or_with_select"),
            ("SELECT :x", {}, "named_parameter_key_mismatch"),
            ("SELECT $1", {}, "positional_or_driver_parameter"),
        ]:
            with self.subTest(sql=sql):
                self.assertTrue(any(error in item for item in validator.validate(sql, params, "mimiciv", None)))


if __name__ == "__main__":
    unittest.main()
