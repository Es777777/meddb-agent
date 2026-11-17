from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from medresearch.demo import example_spec, synthetic_data
from medresearch.literature import audit_definitions, search_pubmed, verify_pubmed_sources
from medresearch.planning import plan_study, revise_session
from medresearch.quality import check_quality
from medresearch.statistics import analyze
from medresearch.study_contract import METHODS, study_schema, validate_study
from medresearch.workflow import StudyRunner, validate_across_runs
from medresearch.data import load_data
from meddb.config import load_settings
from meddb.models import QueryResult


class StudyContractTests(unittest.TestCase):
    def test_all_example_methods_validate(self):
        for method in METHODS:
            dataset = "nhanes" if method.startswith("survey_") else "mimiciv"
            validate_study(example_spec(method, "synthetic.csv", dataset=dataset))

    def test_rejects_population_claim_without_survey_design(self):
        spec = example_spec("describe", "synthetic.csv", dataset="nhanes")
        spec["population"] = "population"
        with self.assertRaisesRegex(ValueError, "survey"):
            validate_study(spec)

    def test_rejects_feature_leakage_and_missing_causal_assumptions(self):
        spec = example_spec("predict", "synthetic.csv")
        spec["analysis"]["covariates"].append("binary")
        with self.assertRaisesRegex(ValueError, "features"):
            validate_study(spec)
        spec = example_spec("predict", "synthetic.csv")
        next(d for d in spec["definitions"] if d["column"] == "x")["availability"] = "post_index"
        with self.assertRaisesRegex(ValueError, "baseline"):
            validate_study(spec)
        spec = example_spec("aipw", "synthetic.csv")
        spec["analysis"]["causal_assumptions"] = []
        with self.assertRaises(ValueError):
            validate_study(spec)

    def test_rejects_missing_variable_provenance_and_unknown_methods(self):
        spec = example_spec("linear", "synthetic.csv")
        spec["definitions"][0]["source_id"] = "fabricated-source"
        with self.assertRaisesRegex(ValueError, "sources"):
            validate_study(spec)
        spec = example_spec("linear", "synthetic.csv")
        spec["analysis"]["method"] = "run_python"
        with self.assertRaises(ValueError):
            validate_study(spec)

    def test_published_study_schema_is_current(self):
        path = Path(__file__).resolve().parents[1]/"context/schemas/study-v2.schema.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), study_schema())


class StatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = synthetic_data(n=1200)

    def test_linear_recovers_known_effect(self):
        result = analyze(self.frame, example_spec("linear", "unused")["analysis"])
        treatment = next(x for x in result["coefficients"] if x["term"] == "treatment")
        self.assertAlmostEqual(treatment["estimate"], 1.2, delta=.2)
        self.assertLess(treatment["lower"], treatment["estimate"])
        self.assertGreater(treatment["upper"], treatment["estimate"])

    def test_binary_model_rejects_invalid_codes(self):
        frame = self.frame.copy()
        frame.loc[0, "binary"] = 3
        with self.assertRaisesRegex(ValueError, "0/1"):
            analyze(frame, example_spec("logistic", "unused")["analysis"])

    def test_kaplan_meier_matches_hand_computed_risk_sets(self):
        frame = pd.DataFrame({"duration": [1., 2., 3., 4., 5.], "event": [1, 1, 0, 1, 0]})
        result = analyze(frame, {"method": "kaplan_meier", "duration": "duration", "event": "event"})
        curve = result["curves"][0]
        observed = dict(zip(curve["time"], curve["estimate"]))
        self.assertAlmostEqual(observed[1.], .8)
        self.assertAlmostEqual(observed[2.], .6)
        self.assertAlmostEqual(observed[4.], .3)

    def test_competing_risk_matches_complete_followup_probability(self):
        frame = pd.DataFrame({"duration": [1., 2., 3., 4., 5.], "event": [1, 2, 1, 2, 1]})
        result = analyze(frame, {"method": "competing_risk", "duration": "duration", "event": "event", "seed": 7})
        self.assertAlmostEqual(result["curves"][0]["estimate"][-1], .6)

    def test_survey_mean_and_taylor_variance_match_hand_calculation(self):
        frame = pd.DataFrame({"outcome": [1, 1, 3, 3, 2, 2, 6, 6], "weight": [1.]*8,
            "strata": [1]*4+[2]*4, "psu": [1, 1, 2, 2, 1, 1, 2, 2]})
        a = {"method": "survey_mean", "outcome": "outcome", "weight": "weight", "strata": "strata", "psu": "psu"}
        result = analyze(frame, a)
        self.assertEqual(result["design_df"], 2)
        self.assertAlmostEqual(result["coefficients"][0]["estimate"], 3.)
        self.assertAlmostEqual(result["coefficients"][0]["se"], np.sqrt(1.25))
        scaled = frame.assign(weight=frame.weight*100)
        self.assertAlmostEqual(analyze(scaled, a)["coefficients"][0]["se"], np.sqrt(1.25))

    def test_survey_singletons_and_nonpositive_weights_fail(self):
        frame = self.frame.copy()
        a = example_spec("survey_mean", "unused", dataset="nhanes")["analysis"]
        frame["psu"] = 1
        with self.assertRaisesRegex(ValueError, "two PSUs"):
            analyze(frame, a)
        frame = self.frame.copy()
        frame.loc[0, "weight"] = -1
        with self.assertRaisesRegex(ValueError, "positive"):
            analyze(frame, a)

    def test_survey_domain_retains_full_design_degrees_of_freedom(self):
        frame = self.frame.copy()
        frame["_domain"] = frame.x > 0
        a = example_spec("survey_mean", "unused", dataset="nhanes")["analysis"]
        result = analyze(frame, a)
        self.assertEqual(result["design_df"], 28)
        self.assertEqual(result["flow"]["analysis_rows"], int(frame._domain.sum()))

    def test_prediction_group_split_and_training_only_preprocessing(self):
        frame = self.frame.copy()
        frame.loc[::11, "x"] = np.nan
        a = example_spec("predict", "unused")["analysis"]
        result = analyze(frame, a)
        self.assertEqual(result["group_overlap"], 0)
        self.assertTrue(0 <= result["metrics"]["auc"] <= 1)
        self.assertNotIn("predictions", result)
        a.update(split="temporal", split_column="time_order")
        self.assertEqual(analyze(frame, a)["group_overlap"], 0)

    def test_aipw_recovers_known_ate_under_measured_confounding(self):
        result = analyze(self.frame, example_spec("aipw", "unused")["analysis"])
        self.assertAlmostEqual(result["coefficients"][0]["estimate"], 1.2, delta=.25)
        self.assertEqual(result["independent_clusters"], 600)
        self.assertTrue(0 <= result["overlap"]["clipped_fraction"] <= 1)

    def test_cox_and_survey_regressions_produce_finite_estimates(self):
        for method in ("cox", "survey_linear", "survey_logistic"):
            with self.subTest(method=method):
                spec = example_spec(method, "unused", dataset="seer17" if method == "cox" else "brfss")
                result = analyze(self.frame, spec["analysis"])
                self.assertTrue(all(np.isfinite(c["estimate"]) for c in result["coefficients"]))


class WorkflowTests(unittest.TestCase):
    def test_local_sql_extraction_reuses_core_and_blocks_truncated_data(self):
        root = Path(__file__).resolve().parents[1]
        spec = example_spec("linear", "unused")
        spec["data"] = {"kind": "postgres", "config": str(root/"config/meddb-mimic-official.json"),
            "sql": "SELECT hadm_id AS record_id FROM mimiciv_hosp.admissions", "params": {}, "output_columns": ["record_id"]}
        result = QueryResult("synthetic-query", ["record_id"], [(1,), (2,)], 1, False, 1.)
        with mock.patch("medresearch.data.MedDBTool") as preflight, \
                mock.patch("medresearch.data.Catalog") as catalog, \
                mock.patch("medresearch.data.Database") as database, \
                mock.patch("medresearch.data.AuditLog"):
            database.return_value.execute.return_value = result
            frame, intake = load_data(spec, root)
            self.assertEqual(len(frame), 2)
            self.assertFalse(intake["truncated"])
            self.assertTrue(database.return_value.execute.call_args.kwargs["detail"])
            preflight.return_value.preflight.assert_called_once_with({"mimiciv_hosp", "mimiciv_icu"})
            result.truncated = True
            with self.assertRaisesRegex(ValueError, "truncated"):
                load_data(spec, root)

    def test_output_cache_cannot_be_written_into_public_repo_paths(self):
        from medresearch.workflow import protected_output
        root = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(ValueError, "runs"):
            protected_output(root/"examples/patient-cache")

    def test_quality_catches_join_multiplication_and_codes(self):
        frame = synthetic_data()
        frame.loc[1, "record_id"] = frame.loc[0, "record_id"]
        frame.loc[0, "treatment"] = 9
        quality = check_quality(frame, example_spec("linear", "unused"))
        self.assertFalse(quality["passed"])
        self.assertIn("analysis_unit_cardinality_violation", quality["issues"])
        self.assertIn("invalid_categorical_codes", quality["issues"])

    def test_full_pipeline_report_figures_resume_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            synthetic_data().to_csv(base/"input.csv", index=False)
            spec = example_spec("linear", "input.csv")
            runner = StudyRunner(base/"run")
            state = runner.run(spec, base=base)
            self.assertIn(state["status"], {"completed", "needs_review"})
            self.assertIn("report", state["stages"])
            self.assertTrue((base/"run/analysis.png").is_file())
            self.assertTrue((base/"run/report.html").is_file())
            with mock.patch("medresearch.workflow.load_data") as load:
                runner.run(spec, base=base, resume=True)
                load.assert_not_called()
            (base/"run/analysis.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "modified"):
                runner.run(spec, base=base, resume=True)

    def test_quality_failure_blocks_statistics(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            frame = synthetic_data()
            frame["record_id"] = 1
            frame.to_csv(base/"input.csv", index=False)
            with mock.patch("medresearch.workflow.analyze") as analyze_mock:
                state = StudyRunner(base/"run").run(example_spec("linear", "input.csv"), base=base)
            self.assertEqual(state["status"], "blocked_quality")
            analyze_mock.assert_not_called()

    def test_failed_stage_resumes_without_refitting_completed_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            synthetic_data().to_csv(base/"input.csv", index=False)
            spec = example_spec("linear", "input.csv")
            runner = StudyRunner(base/"run")
            with mock.patch("medresearch.workflow.render_report", side_effect=RuntimeError("synthetic-sensitive-value")):
                state = runner.run(spec, base=base)
            self.assertEqual(state["status"], "failed")
            self.assertNotIn("synthetic-sensitive-value", (base/"run/state.json").read_text())
            with mock.patch("medresearch.workflow.analyze") as calculate:
                state = runner.run(spec, base=base, resume=True)
            self.assertIn(state["status"], {"completed", "needs_review"})
            calculate.assert_not_called()

    def test_cross_dataset_incompatibility_does_not_pool(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            synthetic_data().to_csv(base/"input.csv", index=False)
            for i, dataset in enumerate(("mimiciv", "seer17")):
                spec = example_spec("linear", "input.csv", dataset=dataset, study_id=f"study-{i}")
                StudyRunner(base/f"run-{i}").run(spec, base=base)
            result = validate_across_runs([base/"run-0", base/"run-1"], base/"compare")
            self.assertFalse(result["compatible_declared_constructs"])
            self.assertIsNone(result["pooled_effect"])
            self.assertFalse(result["external_validation_certified"])

    def test_session_lineage_and_changed_history(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            spec = example_spec("linear", "input.csv")
            revise_session(spec, base/"session", base=base, note="initial design")
            spec["question"] += " revision"
            revised = revise_session(spec, base/"session", base=base, note="clarified question")
            self.assertEqual(revised["revision"], 2)
            state = json.loads((base/"session/session.json").read_text())
            self.assertEqual(state["revisions"][1]["parent_sha256"], state["revisions"][0]["protocol_sha256"])
            (base/"session/revision-001.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "modified"):
                revise_session(spec, base/"session", base=base, note="must fail")


class LiteraturePlanningTests(unittest.TestCase):
    def test_pubmed_search_parses_actual_metadata_shape_offline(self):
        def fetch(url):
            if "esearch" in url:
                return b'{"esearchresult":{"idlist":["123"]}}'
            return b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>Clinical <i>study</i></ArticleTitle><Journal><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'
        result = search_pubmed("public synthetic query", fetch=fetch)
        self.assertEqual(result["sources"][0]["title"], "Clinical study")
        self.assertEqual(result["sources"][0]["pmid"], "123")

    def test_unreviewed_definition_is_not_certified(self):
        spec = example_spec("linear", "unused")
        spec["definitions"][0]["reviewed"] = False
        self.assertFalse(audit_definitions(spec)["passed"])

    def test_pubmed_verification_flags_fabricated_title(self):
        def fetch(url):
            if "esearch" in url:
                return b'{"esearchresult":{"idlist":["123"]}}'
            return b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>Actual title</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'
        result = verify_pubmed_sources([{"id": "source", "pmid": "123", "title": "Invented title"}], fetch=fetch)
        self.assertEqual(result["sources"][0]["status"], "metadata_mismatch")

    def test_planner_receives_no_paths_observations_or_credentials(self):
        spec = example_spec("linear", "PRIVATE_PATH.csv")
        brief = {k: v for k, v in spec.items() if k not in {"schema_version", "analysis"}}
        generator = mock.Mock()
        generator.request_json.return_value = copy.deepcopy(spec)
        planned = plan_study(brief, generator)
        prompt = generator.request_json.call_args.args[0]
        self.assertNotIn("PRIVATE_PATH", prompt)
        self.assertEqual(planned["data"]["path"], "PRIVATE_PATH.csv")
        changed = copy.deepcopy(spec)
        changed["definitions"][0]["reviewed"] = False
        generator.request_json.return_value = changed
        with self.assertRaisesRegex(ValueError, "bound"):
            plan_study(brief, generator)


class AgentTests(unittest.TestCase):
    def test_bounded_planning_budget_without_data_access(self):
        from medresearch.agent import run_agent
        spec = example_spec("linear", "not-opened.csv")
        brief = {k: v for k, v in spec.items() if k not in {"schema_version", "analysis"}}
        generator = mock.Mock(model="synthetic-model")
        generator.request_json.side_effect = RuntimeError("synthetic-provider-error")
        with tempfile.TemporaryDirectory() as temp, mock.patch("medresearch.agent.StudyRunner") as runner:
            state = run_agent(brief, generator, Path(temp)/"agent", base=Path(temp), max_attempts=2)
            self.assertEqual(state["status"], "planning_failed")
            self.assertEqual(len(state["attempts"]), 2)
            runner.assert_not_called()

    def test_agent_runs_local_workflow_and_resumes_without_model_call(self):
        from medresearch.agent import run_agent
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            synthetic_data().to_csv(base/"input.csv", index=False)
            spec = example_spec("linear", "input.csv")
            brief = {k: v for k, v in spec.items() if k not in {"schema_version", "analysis"}}
            generator = mock.Mock(model="synthetic-model")
            generator.request_json.return_value = copy.deepcopy(spec)
            state = run_agent(brief, generator, base/"agent", base=base)
            self.assertIn(state["status"], {"completed", "needs_review"})
            self.assertFalse(state["observations_sent_to_model"])
            run_agent(brief, generator, base/"agent", base=base, resume=True)
            self.assertEqual(generator.request_json.call_count, 1)


if __name__ == "__main__":
    unittest.main()
