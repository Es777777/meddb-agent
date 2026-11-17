"""Question-only retrieval of MIMIC dictionary metadata through the read-only CLI.

No task SQL, rubric, reference CSV, clinical rows, or expected results are read.
The records are candidate code definitions, not an asserted complete phenotype.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from low_impact_benchmark import bounded_db_env, child_flags


STOP = set("""a an the this that is are was were be been being have has had my i
for among with without versus vs compare comparing to of in on by and or from
per during within first last initial before after hours hour days day years year
old aged age male males man men female females woman women patient patients
hospital hospitalized hospitalization hospitalizations admission admissions icu
stay stays report what which how number count counts total distinct unique
average mean minimum maximum min max median percentile percentiles quartile
quartiles quintile quintiles standard deviation sd iqr intensity load burden
stratify stratified diagnostic diagnosis primary secondary procedures procedure
results result evaluating evaluation evaluate associated percentage percent
population groups group matched other general control controls outcome outcomes
length mortality overall cardiac""".split())
STOP.update({"over", "similar", "year-old", "years-old", "between", "distribution", "their", "those", "than", "across"})

# Lexical expansions only; no benchmark-specific ICD families or item IDs.
EXPANSIONS = {
    "acs": ["acute coronary", "unstable angina", "myocardial infarction"],
    "ami": ["myocardial infarction"],
    "ich": ["intracranial hemorrhage", "intracerebral hemorrhage", "subarachnoid hemorrhage"],
    "cabg": ["coronary bypass", "coronary artery bypass"],
    "echo": ["echocardiography", "echocardiogram"],
    "ecg": ["electrocardiogram", "electrocardiography"],
    "aki": ["acute kidney", "acute renal"],
    "ct": ["computed tomography"],
    "cardiac": ["cardiac", "coronary", "heart"],
}

DICTIONARIES = (
    ("mimiciv_hosp.d_labitems", "label", "itemid", ("itemid", "label", "fluid", "category")),
    ("mimiciv_icu.d_items", "label", "itemid", ("itemid", "label", "linksto", "category", "unitname")),
    ("mimiciv_hosp.d_icd_diagnoses", "long_title", "icd_code, icd_version", ("icd_code", "icd_version", "long_title")),
    ("mimiciv_hosp.d_icd_procedures", "long_title", "icd_code, icd_version", ("icd_code", "icd_version", "long_title")),
)

# Source routing is lexical, not a task-ID lookup or an exhaustive phenotype.
# Each concept receives its own candidate budget; unrelated terms cannot evict it.
CONCEPTS = (
    ("platelet", r"\bplatelets?\b", (0,), ("platelet", "platelets", "platelet count")),
    ("sepsis", r"\bsepsis\b|\bseptic\b", (2,), ("sepsis", "septic shock", "septicemia", "septicaemia")),
    ("acute_kidney_injury", r"\baki\b|acute (?:kidney|renal)", (2,), ("acute kidney failure", "acute renal failure", "acute kidney injury")),
    ("coronary_bypass", r"\bcabg\b|coronary.*bypass", (3,), ("coronary artery bypass", "coronary bypass", "aortocoronary bypass")),
    ("echocardiography", r"\becho\b|echocardi", (3,), ("echocardiography", "echocardiogram")),
    ("electrocardiography", r"\becg\b|electrocardio", (3,), ("electrocardiography", "electrocardiogram")),
)


def medication_context(question: str, registry: dict) -> list[dict]:
    result = []
    for item in registry.get("classes", []):
        if any(re.search(r"\b" + re.escape(alias) + r"\b", question, re.I) for alias in item["aliases"]):
            result.append(item)
    return result


def retrieval_plan(question: str, medication_classes: list[dict] | None = None) -> list[dict]:
    plans = []
    for name, pattern, indices, terms in CONCEPTS:
        if re.search(pattern, question, re.I):
            plans.extend({"concept": name, "dictionary_index": index, "terms": list(terms)} for index in indices)
    if plans:
        return plans
    if medication_classes:
        # Ingredient registry is the appropriate source; no diagnosis-table scan.
        return []
    terms = search_terms(question)
    if not terms:
        return []
    text = question.lower()
    if re.search(r"\b(?:prescription|medication|drug)s?\b", text):
        # Do not pretend that ICD dictionaries resolve an unknown medication class.
        return []
    if re.search(r"\b(?:lab|laboratory|analyte)\b", text):
        indices = (0,)
    elif re.search(r"\b(?:vital|bedside|heart rate|blood pressure)\b", text):
        indices = (1,)
    elif re.search(r"\b(?:procedure|procedures|surgery|imaging)\b", text):
        indices = (3,)
    elif re.search(r"\b(?:diagnosis|diagnoses|disease)\b", text):
        indices = (2,)
    else:
        # No identifiable concept/source is not a reason to scan all dictionaries.
        # In particular, statistical words in a demographic question are not
        # clinical concepts. Leave the unresolved route visible to the caller.
        indices = ()
    return [{"concept": "unresolved_lexical_discovery", "dictionary_index": i, "terms": terms} for i in indices]


def search_terms(question: str) -> list[str]:
    words = re.findall(r"[a-z][a-z-]*", question.lower())
    candidates = []
    for word in words:
        if word in EXPANSIONS:
            candidates.extend(EXPANSIONS[word])
        elif len(word) >= 4 and word not in STOP and not word.endswith("-old"):
            candidates.append(word)
    return list(dict.fromkeys(candidates))[:12]


def dictionary_sql(table: str, label: str, order: str, columns: tuple[str, ...]) -> str:
    if (table, label, order, columns) not in DICTIONARIES:
        raise ValueError("Dictionary identifiers must be fixed allowlisted metadata")
    projected = ", ".join(f"BTRIM({column}) AS {column}" if column == "icd_code" else column for column in columns)
    object_args = ", ".join(f"'{column}', candidates.{column}" for column in columns)
    # Only dictionary rows. Rank exact > prefix > substring, then stable IDs.
    # Count BEFORE limiting; a saturated list must not appear complete.
    return f"""WITH matched AS (
      SELECT {projected},
        CASE WHEN lower({label}) = ANY(CAST(:terms AS text[])) THEN 0
             WHEN lower({label}) LIKE ANY(CAST(:prefixes AS text[])) THEN 1
             ELSE 2 END AS relevance_rank,
        LENGTH({label}) AS label_length
      FROM {table}
      WHERE lower({label}) LIKE ANY(CAST(:patterns AS text[]))
    ), candidates AS (
      SELECT {', '.join(columns)}, relevance_rank, label_length
      FROM matched
      ORDER BY relevance_rank, label_length, {order}
      LIMIT 32
    )
    SELECT COUNT(*) AS candidate_count,
      (SELECT COUNT(*) FROM matched) AS total_candidates,
      (SELECT COUNT(*) FROM matched) > 32 AS truncated,
      COALESCE(JSONB_AGG(JSONB_BUILD_OBJECT({object_args})
        ORDER BY relevance_rank, label_length, {order}), '[]'::jsonb) AS candidates
    FROM candidates"""


def retrieve(question: str, meddb_root: Path, config: Path, directory: Path, key: str, registry_path: Path | None = None) -> dict:
    terms = search_terms(question)
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path and registry_path.exists() else {}
    medications = medication_context(question, registry)
    plans = retrieval_plan(question, medications)
    context = {"type": "dictionary_metadata_candidates", "search_terms": terms,
               "not_a_validated_phenotype": True, "sources": [], "medication_classes": medications,
               "retrieval_plan": plans, "registry_version": registry.get("version")}
    if not plans and not medications:
        context["routing_note"] = "No supported concept/source route; no dictionary scan. This is not evidence that a clinical concept is absent."
    directory.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha256((str(directory.resolve()) + key).encode()).hexdigest()[:16]
    config_data = json.loads(config.read_text(encoding="utf-8"))
    export_root = Path(config_data["paths"]["exports"])
    if not export_root.is_absolute():
        export_root = meddb_root / export_root
    env = {**os.environ, "MEDDB_CODEX_PROFILE": "no-mcp", "MEDDB_CODEX_SAFE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    env = bounded_db_env(env)
    for index, plan in enumerate(plans):
        table, label, order, columns = DICTIONARIES[plan["dictionary_index"]]
        concept_terms = plan["terms"]
        sql = dictionary_sql(table, label, order, columns)
        sql_path, params_path = directory / f"{tag}_{index}.sql", directory / f"{tag}_{index}.params.json"
        sql_path.write_text(sql, encoding="utf-8")
        params_path.write_text(json.dumps({
            "terms": concept_terms, "prefixes": [f"{term}%" for term in concept_terms],
            "patterns": [f"%{term}%" for term in concept_terms],
        }), encoding="utf-8")
        filename = f"dictionary_{tag}_{index}.json"
        try:
            process = subprocess.run([
                str(meddb_root / "bin" / "meddb.cmd"), "--config", str(config), "query",
                "--sql-file", str(sql_path), "--params-file", str(params_path),
                "--yes", "--format", "json", "--output", filename,
            ], cwd=meddb_root, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40, creationflags=child_flags())
        except subprocess.TimeoutExpired:
            context["sources"].append({"concept": plan["concept"], "table": table, "status": "metadata_timeout"})
            continue
        if process.returncode:
            context["sources"].append({"concept": plan["concept"], "table": table, "status": "metadata_error", "diagnostic": (process.stdout + process.stderr)[-800:]})
            continue
        output = export_root / filename
        metadata = json.loads(output.read_text(encoding="utf-8"))
        rows = metadata.get("rows", [])
        record = rows[0] if rows else {}
        candidates = record.get("candidates", [])
        if isinstance(candidates, str):
            candidates = json.loads(candidates)
        for item in candidates:
            if isinstance(item.get("icd_code"), str):
                item["icd_code"] = item["icd_code"].strip()
        context["sources"].append({
            "concept": plan["concept"], "table": table, "status": "retrieved", "candidate_count": record.get("candidate_count"),
            "total_candidates": record.get("total_candidates"), "truncated": record.get("truncated"),
            "candidates": candidates, "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        })
    frozen = directory / f"{tag}.context.json"
    frozen.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    return context
