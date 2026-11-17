"""Deterministic evidence-linked scientific reports and standalone figures."""
from __future__ import annotations

import html
from pathlib import Path


def review_results(spec: dict, quality: dict, definitions: dict, primary: dict, sensitivities: list[dict]) -> dict:
    issues = []
    if not quality["passed"]:
        issues.extend(quality["issues"])
    if not definitions["passed"]:
        issues.append("definitions_or_sources_need_review")
    if primary.get("warnings"):
        issues.append("statistical_warnings_require_review")
    if any(x["status"] != "completed" for x in sensitivities):
        issues.append("sensitivity_analysis_failed")
    if not sensitivities and primary["method"] != "describe":
        issues.append("no_prespecified_sensitivity_analysis")
    if primary.get("overlap", {}).get("clipped_fraction", 0) > .05:
        issues.append("poor_treatment_overlap")
    if any(p < .05 for p in primary.get("ph_test_p_values", {}).values()):
        issues.append("proportional_hazards_diagnostic_flag")
    stability = []
    for variant in sensitivities:
        if variant["status"] != "completed":
            continue
        reference = {x["term"]: x for x in primary.get("coefficients", [])}
        for coefficient in variant["result"].get("coefficients", []):
            term = coefficient["term"]
            if term in reference and term != "const":
                sign_changed = coefficient["estimate"] * reference[term]["estimate"] < 0
                stability.append({"sensitivity": variant["name"], "term": term, "sign_changed": sign_changed})
                if sign_changed:
                    issues.append("estimate_direction_changed_in_sensitivity")
    return {"status": "needs_review" if issues else "checks_passed", "issues": sorted(set(issues)),
            "sensitivity_stability": stability, "scientific_validity_certified": False,
            "scope": "Automated consistency diagnostics; clinical/scientific validity requires domain review"}


def render_report(output: Path, spec: dict, primary: dict, quality: dict, definitions: dict,
                  review: dict, sensitivities: list[dict]) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    artifacts = []
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    if "curves" in primary:
        for curve in primary["curves"]:
            ax.step(curve["time"], curve["estimate"], where="post", label=curve["group"])
            if all(v is not None for v in curve["lower"]+curve["upper"]):
                ax.fill_between(curve["time"], curve["lower"], curve["upper"], step="post", alpha=.15)
        ax.set_xlabel("Follow-up time (declared protocol units)")
        ax.set_ylabel("Cumulative incidence" if primary["method"] == "competing_risk" else "Survival probability")
        ax.set_ylim(0, 1)
        ax.legend()
    elif "coefficients" in primary:
        coefficients = [c for c in primary["coefficients"] if c["term"] != "const"] or primary["coefficients"]
        y = list(range(len(coefficients)))
        estimates = [c["estimate"] for c in coefficients]
        ax.errorbar(estimates, y, xerr=[[c["estimate"]-c["lower"] for c in coefficients],
                    [c["upper"]-c["estimate"] for c in coefficients]], fmt="o", capsize=3)
        ax.set_yticks(y, [c["term"] for c in coefficients])
        ax.axvline(0, color="grey", linestyle="--")
        ax.set_xlabel("Coefficient / declared estimand (confidence interval)")
    elif "calibration" in primary:
        ax.plot([0, 1], [0, 1], "--", color="grey")
        ax.plot([r["predicted"] for r in primary["calibration"]],
                [r["observed"] for r in primary["calibration"]], "o-")
        ax.set(xlabel="Predicted probability", ylabel="Observed outcome fraction", xlim=(0, 1), ylim=(0, 1))
    else:
        groups = primary["groups"]
        ax.bar([g["group"] for g in groups], [g["mean"] for g in groups])
        ax.set_ylabel("Mean (declared protocol units)")
    ax.set_title(primary["method"])
    fig.tight_layout()
    for suffix in ("png", "svg"):
        name = "analysis."+suffix
        fig.savefig(output/name, dpi=160)
        artifacts.append(name)
    plt.close(fig)
    report = ["# "+spec["study_id"], "", spec["question"], "",
        f"Dataset: {spec['dataset']['name']} / declared version {spec['dataset']['version']}",
        f"Analysis unit: {spec['analysis_unit']}; target: {spec['population']}",
        f"Method: {primary['method']}; automated review: {review['status']}", "",
        "## Prespecified design", "",
        *[f"- {key}: {value}" for key, value in spec["design"].items()], "",
        "## Evidence and interpretation", "",
        "These estimates describe the declared analysis and require clinical review. "
        "An executed model or passed diagnostic does not establish scientific validity.", "",
        "Evidence: [protocol](study.json), [quality](quality.json), [definitions](definitions.json), "
        "[primary analysis](analysis.json), [sensitivity](sensitivity.json), [review](review.json), "
        "[provenance](state.json).", ""]
    if "coefficients" in primary:
        report += ["| Term | Estimate | Lower | Upper |", "|---|---:|---:|---:|"]
        for c in primary["coefficients"]:
            safe_term = str(c["term"]).replace("|", "\\|").replace("\n", " ")
            report.append(f"| {safe_term} | {c['estimate']:.6g} | {c['lower']:.6g} | {c['upper']:.6g} |")
    elif "metrics" in primary:
        report += [f"Held-out AUC: {primary['metrics']['auc']:.4f}; Brier: {primary['metrics']['brier']:.4f}."]
    elif "groups" in primary:
        report += ["| Group | n | Mean | SD |", "|---|---:|---:|---:|"]
        report += [f"| {g['group']} | {g['n']} | {g['mean']:.6g} | {g['sd']:.6g} |" for g in primary["groups"]]
    report += ["", "![Analysis figure](analysis.png)", "", "## Review findings", ""]
    report += ["- "+issue for issue in review["issues"]] or ["Automated checks passed; scientific validity is not certified."]
    report += ["", "## Limitations", "", "- Dataset release and clinical coding correctness require independent verification."]
    report += ["- "+text for text in primary.get("limitations", [])]
    report += ["", "## Definition sources", ""]
    report += [f"- {e['id']}: {e['title']} — {e['url']} ({e['verification']})" for e in spec["evidence"]]
    report += ["", "## Sensitivity analyses", ""]
    report += [f"- {v['name']}: {v['status']}" for v in sensitivities] or ["None prespecified; robustness has not been assessed."]
    (output/"report.md").write_text("\n".join(report)+"\n", encoding="utf-8")
    # Escape all data before constructing portable HTML. No JavaScript or remote assets.
    page = "<!doctype html><html lang='en'><meta charset='utf-8'><title>"+html.escape(spec["study_id"])+"</title>"
    page += "<style>body{max-width:1000px;margin:40px auto;font:16px/1.6 system-ui}pre{white-space:pre-wrap}img{max-width:100%}</style>"
    page += "<h1>"+html.escape(spec["study_id"])+"</h1><img src='analysis.png' alt='Statistical analysis figure'>"
    page += "<pre>"+html.escape("\n".join(report))+"</pre></html>"
    (output/"report.html").write_text(page, encoding="utf-8")
    return artifacts+["report.md", "report.html"]
