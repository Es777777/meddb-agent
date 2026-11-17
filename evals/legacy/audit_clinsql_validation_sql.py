from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import sqlglot
from sqlglot import exp


SCHEMA_REWRITES = {
    "physionet-data.mimiciv_3_1_hosp": "mimiciv_hosp",
    "physionet-data.mimiciv_3_1_icu": "mimiciv_icu",
    "physionet-data.mimiciv_3_1_derived": "mimiciv_derived",
}

# Tokens are counted to scope the deterministic converter. This is an audit,
# not a task-specific rewrite table.
FEATURE_PATTERNS = {
    "approx_quantiles": re.compile(r"\bAPPROX_QUANTILES\s*\(", re.I),
    "offset_index": re.compile(r"\[\s*OFFSET\s*\(", re.I),
    "safe_divide": re.compile(r"\bSAFE_DIVIDE\s*\(", re.I),
    "date_diff": re.compile(r"\bDATE_DIFF\s*\(", re.I),
    "timestamp_diff": re.compile(r"\bTIMESTAMP_DIFF\s*\(", re.I),
    "date_add_sub": re.compile(r"\bDATE_(?:ADD|SUB)\s*\(", re.I),
    "timestamp_add_sub": re.compile(r"\bTIMESTAMP_(?:ADD|SUB)\s*\(", re.I),
    "int64": re.compile(r"\bINT64\b", re.I),
    "qualify": re.compile(r"\bQUALIFY\b", re.I),
    "unnest": re.compile(r"\bUNNEST\s*\(", re.I),
    "regexp": re.compile(r"\bREGEXP_\w+\s*\(", re.I),
}


def rewrite_catalog_names(sql: str) -> str:
    rewritten = sql
    for source, target in SCHEMA_REWRITES.items():
        rewritten = rewritten.replace(f"`{source}.", f"{target}.")
    rewritten = rewritten.replace("`", "")
    rewritten = re.sub(r"\bSAFE\.SQRT\s*\(", "SQRT(", rewritten, flags=re.I)
    return rewritten


def build_clinsql_postgres(mimic_code_src: Path):
    sys.path.insert(0, str(mimic_code_src.resolve()))
    from mimic_utils.sqlglot_dialects.postgres import MimicPostgres

    def approx_quantiles_sql(generator, expression: exp.ApproxQuantiles) -> str:
        buckets = expression.expression
        if not isinstance(buckets, exp.Literal) or buckets.is_string:
            raise ValueError("APPROX_QUANTILES bucket count must be an integer literal")
        bucket_count = int(buckets.this)
        if bucket_count < 1 or bucket_count > 1000:
            raise ValueError(f"Unsupported APPROX_QUANTILES bucket count: {bucket_count}")
        fractions = ", ".join(format(index / bucket_count, ".12g") for index in range(bucket_count + 1))
        value = generator.sql(expression, "this")
        # BigQuery returns observed approximate boundaries. PostgreSQL has no
        # identical aggregate; PERCENTILE_DISC preserves observed-value/type
        # semantics and is the predeclared validation candidate.
        return f"(PERCENTILE_DISC(ARRAY[{fractions}]) WITHIN GROUP (ORDER BY {value}))"

    def timestamp_add_sub_sql(generator, expression: exp.Expression, operator: str) -> str:
        value = generator.sql(expression, "this")
        quantity = expression.expression
        unit = expression.args.get("unit")
        unit_name = unit.name.upper() if unit is not None else "DAY"
        if isinstance(quantity, exp.Literal):
            return f"{value} {operator} INTERVAL '{quantity.name}' {unit_name}"
        return f"{value} {operator} {generator.sql(quantity)} * INTERVAL '1' {unit_name}"

    def timestamp_diff_sql(generator, expression: exp.Expression) -> str:
        end = generator.sql(expression, "this")
        start = generator.sql(expression, "expression")
        unit = expression.args.get("unit")
        unit_name = unit.name.upper() if unit is not None else "DAY"
        if unit_name == "DAY":
            return f"(CAST({end} AS DATE) - CAST({start} AS DATE))"
        if unit_name == "YEAR":
            return f"CAST(EXTRACT(YEAR FROM {end}) - EXTRACT(YEAR FROM {start}) AS BIGINT)"
        seconds = {"SECOND": 1, "MINUTE": 60, "HOUR": 3600}[unit_name]
        lower = unit_name.lower()
        return (
            f"CAST(EXTRACT(EPOCH FROM DATE_TRUNC('{lower}', {end}) "
            f"- DATE_TRUNC('{lower}', {start})) / {seconds} AS BIGINT)"
        )

    def split_sql(generator, expression: exp.Split) -> str:
        return (
            f"(STRING_TO_ARRAY({generator.sql(expression, 'this')}, "
            f"{generator.sql(expression, 'expression')}))"
        )

    class ClinSQLPostgres(MimicPostgres):
        class Generator(MimicPostgres.Generator):
            TRANSFORMS = {
                **MimicPostgres.Generator.TRANSFORMS,
                exp.ApproxQuantiles: approx_quantiles_sql,
                exp.TimestampAdd: lambda generator, expression: timestamp_add_sub_sql(generator, expression, "+"),
                exp.TimestampSub: lambda generator, expression: timestamp_add_sub_sql(generator, expression, "-"),
                exp.TimestampDiff: timestamp_diff_sql,
                exp.Split: split_sql,
            }

    return ClinSQLPostgres


def normalize_bigquery_only_semantics(expression: exp.Expression) -> Counter[str]:
    changes: Counter[str] = Counter()

    def bare_null_alias(item: exp.Expression) -> exp.Alias | None:
        return item if isinstance(item, exp.Alias) and isinstance(item.this, exp.Null) else None

    def is_numeric_expression(item: exp.Expression) -> bool:
        value = item.this if isinstance(item, exp.Alias) else item
        if isinstance(value, exp.Literal):
            return not value.is_string
        if isinstance(value, (exp.AggFunc, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)):
            return True
        if isinstance(value, exp.Cast):
            return value.to.is_type(
                exp.DataType.Type.TINYINT,
                exp.DataType.Type.SMALLINT,
                exp.DataType.Type.INT,
                exp.DataType.Type.BIGINT,
                exp.DataType.Type.FLOAT,
                exp.DataType.Type.DOUBLE,
                exp.DataType.Type.DECIMAL,
            )
        return False

    def cast_null_alias(item: exp.Expression) -> bool:
        alias = bare_null_alias(item)
        if alias is None:
            return False
        alias.set(
            "this",
            exp.Cast(
                this=exp.Null(),
                to=exp.DataType.build("DOUBLE PRECISION", dialect="postgres"),
            ),
        )
        return True

    def flatten_union(node: exp.Expression) -> list[exp.Select]:
        if isinstance(node, exp.Union):
            return flatten_union(node.this) + flatten_union(node.expression)
        return [node] if isinstance(node, exp.Select) else []

    def struct_fields(struct: exp.Struct) -> tuple[list[str], list[exp.Expression]]:
        names: list[str] = []
        values: list[exp.Expression] = []
        for index, field in enumerate(struct.expressions, start=1):
            if isinstance(field, exp.PropertyEQ):
                names.append(field.this.name)
                values.append(field.expression.copy())
            elif isinstance(field, exp.Alias):
                names.append(field.alias)
                values.append(field.this.copy())
            else:
                name = field.output_name or f"field_{index}"
                names.append(name)
                values.append(field.copy())
        return names, values

    # BigQuery ARRAY<STRUCT> constants used as row sources become PostgreSQL
    # lateral VALUES relations with explicit field names. This also supports
    # expressions correlated to a table on the left side of a CROSS JOIN.
    struct_source_index = 0
    for unnest in list(expression.find_all(exp.Unnest)):
        if len(unnest.expressions) != 1 or not isinstance(unnest.expressions[0], exp.Array):
            continue
        array = unnest.expressions[0]
        if not array.expressions or not all(isinstance(item, exp.Struct) for item in array.expressions):
            continue
        first_names, _ = struct_fields(array.expressions[0])
        rows = []
        for struct in array.expressions:
            names, values = struct_fields(struct)
            if len(values) != len(first_names):
                raise ValueError("ARRAY<STRUCT> rows do not share the same field count")
            rows.append(exp.Tuple(expressions=values))
        struct_source_index += 1
        original_alias = unnest.args.get("alias")
        alias_name = None
        if original_alias is not None:
            if original_alias.this:
                alias_name = original_alias.this.name
            elif original_alias.columns:
                alias_name = original_alias.columns[0].name
        alias_name = alias_name or f"_clinsql_struct_{struct_source_index}"
        values = exp.Values(expressions=rows)
        lateral = exp.Lateral(
            this=exp.Subquery(this=values),
            alias=exp.TableAlias(
                this=exp.to_identifier(alias_name),
                columns=[exp.to_identifier(name) for name in first_names],
            ),
        )
        unnest.replace(lateral)
        changes["array_struct_unnest_to_lateral_values"] += 1

    # A result-valued ARRAY_AGG(STRUCT(...)) is represented as JSONB. The
    # comparison layer will canonicalize both BigQuery's list-of-records CSV
    # text and PostgreSQL JSONB before scoring.
    for array_agg in list(expression.find_all(exp.ArrayAgg)):
        if not isinstance(array_agg.this, exp.Struct):
            continue
        names, values = struct_fields(array_agg.this)
        object_args: list[exp.Expression] = []
        for name, value in zip(names, values, strict=True):
            object_args.extend([exp.Literal.string(name), value])
        json_object = exp.Anonymous(this="JSONB_BUILD_OBJECT", expressions=object_args)
        array_agg.replace(exp.Anonymous(this="JSONB_AGG", expressions=[json_object]))
        changes["array_agg_struct_to_jsonb"] += 1

    # PostgreSQL may resolve an early chain of UNION NULL literals as TEXT,
    # while BigQuery waits for later numeric branches. Infer only when a
    # sibling branch at the same position is unambiguously numeric.
    for union in list(expression.find_all(exp.Union)):
        if isinstance(union.parent, exp.Union):
            continue
        leaves = flatten_union(union)
        if not leaves or len({len(leaf.expressions) for leaf in leaves}) != 1:
            continue
        width = len(leaves[0].expressions)
        for position in range(width):
            items = [leaf.expressions[position] for leaf in leaves]
            if any(is_numeric_expression(item) for item in items):
                for item in items:
                    if cast_null_alias(item):
                        changes["union_numeric_null_cast"] += 1

    # The same inference is needed when UNION branches use SELECT * from two
    # CTEs whose corresponding output columns are explicit in their CTE body.
    cte_selects: dict[str, exp.Select] = {}
    for cte in expression.find_all(exp.CTE):
        if isinstance(cte.this, exp.Select) and cte.alias_or_name:
            cte_selects[cte.alias_or_name.lower()] = cte.this
    for union in list(expression.find_all(exp.Union)):
        if isinstance(union.parent, exp.Union):
            continue
        leaves = flatten_union(union)
        source_selects: list[exp.Select] = []
        for leaf in leaves:
            if len(leaf.expressions) != 1 or not isinstance(leaf.expressions[0], exp.Star):
                source_selects = []
                break
            source = leaf.args.get("from_")
            table = source.this if source is not None else None
            if not isinstance(table, exp.Table) or table.name.lower() not in cte_selects:
                source_selects = []
                break
            source_selects.append(cte_selects[table.name.lower()])
        if not source_selects or len({len(item.expressions) for item in source_selects}) != 1:
            continue
        for position in range(len(source_selects[0].expressions)):
            items = [item.expressions[position] for item in source_selects]
            if any(is_numeric_expression(item) for item in items):
                for item in items:
                    if cast_null_alias(item):
                        changes["union_star_cte_numeric_null_cast"] += 1

    # CLINSQL's three analytic PERCENTILE_CONT uses all have an empty OVER().
    # PostgreSQL implements the same whole-input percentile as an ordered-set
    # aggregate and forbids OVER on it.
    for select in expression.find_all(exp.Select):
        for window in list(select.find_all(exp.Window)):
            if window.find_ancestor(exp.Select) is not select:
                continue
            if not isinstance(window.this, exp.PercentileCont):
                continue
            if any(window.args.get(key) for key in ("partition_by", "order", "spec")):
                raise ValueError("Partitioned/windowed PERCENTILE_CONT is not supported by the validation port")
            if select.args.get("group") or select.args.get("having"):
                raise ValueError("Grouped empty-OVER PERCENTILE_CONT requires an explicit derived relation")
            percentile_select = exp.Select(expressions=[window.this.copy()])
            for key in ("from_", "joins", "where"):
                if select.args.get(key) is not None:
                    percentile_select.set(key, select.args[key].copy())
            window.replace(exp.Subquery(this=percentile_select))
            changes["empty_over_percentile_scalar_subquery"] += 1

    # BigQuery permits SELECT aliases in HAVING; PostgreSQL does not. Expand
    # the alias expression within the same SELECT scope.
    for select in expression.find_all(exp.Select):
        aliases = {
            item.alias.lower(): item.this
            for item in select.expressions
            if isinstance(item, exp.Alias) and item.alias
        }
        having = select.args.get("having")
        if having is None:
            continue
        for column in list(having.find_all(exp.Column)):
            if not column.table and column.name.lower() in aliases:
                column.replace(aliases[column.name.lower()].copy())
                changes["having_alias_expanded"] += 1
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit CLINSQL validation gold SQL for a deterministic PostgreSQL port.")
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mimic-code-src", type=Path, required=True)
    args = parser.parse_args()

    validation_root = args.validation_root.resolve()
    if validation_root.name.lower() != "validation":
        raise ValueError("This tool intentionally accepts only the validation split")
    target_dialect = build_clinsql_postgres(args.mimic_code_src)

    records = []
    feature_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    semantic_rewrite_counts: Counter[str] = Counter()
    sql_files = sorted(validation_root.rglob("sql_*.sql"))
    if len(sql_files) != 190:
        raise ValueError(f"Expected 190 validation SQL files, found {len(sql_files)}")

    for sql_path in sql_files:
        rel = sql_path.relative_to(validation_root)
        output_path = args.output_root.resolve() / rel
        raw = sql_path.read_text(encoding="utf-8-sig")
        features = sorted(name for name, pattern in FEATURE_PATTERNS.items() if pattern.search(raw))
        feature_counts.update(features)
        rewritten = rewrite_catalog_names(raw)
        error = None
        translated = None
        try:
            expressions = sqlglot.parse(rewritten, read="bigquery")
            for expression in expressions:
                semantic_rewrite_counts.update(normalize_bigquery_only_semantics(expression))
            translated = ";\n\n".join(expr.sql(dialect=target_dialect, pretty=True) for expr in expressions) + ";\n"
            status = "transpiled"
        except Exception as exc:  # noqa: BLE001 - diagnostic artifact captures parser failures
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            if output_path.exists():
                output_path.unlink()
        status_counts[status] += 1

        if translated is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(translated, encoding="utf-8")
        records.append({
            "task_key": "validation/" + rel.parent.as_posix().replace("_level_queries", ""),
            "source_relative_path": rel.as_posix(),
            "output_relative_path": rel.as_posix() if translated is not None else None,
            "features": features,
            "status": status,
            "error": error,
        })

    report = {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "CLINSQL validation gold SQL only",
        "test_gold_opened": False,
        "converter": {
            "parser": "sqlglot + MIT-LCP mimic-code MimicPostgres",
            "source_dialect": "bigquery",
            "target_dialect": "postgres",
            "task_specific_rules": 0,
            "catalog_rewrites": SCHEMA_REWRITES,
            "approx_quantiles_candidate": "PERCENTILE_DISC over all N+1 fractions; validation-only candidate",
        },
        "summary": {
            "total": len(records),
            "status_counts": dict(status_counts),
            "feature_counts": dict(sorted(feature_counts.items())),
            "semantic_rewrite_counts": dict(sorted(semantic_rewrite_counts.items())),
        },
        "tasks": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
