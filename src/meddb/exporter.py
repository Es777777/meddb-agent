from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from meddb.errors import MedDBError
from meddb.models import QueryResult


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _shorten(value: Any, width: int = 40) -> str:
    text = _text(value).replace("\r", " ").replace("\n", " ")
    return text if len(text) <= width else text[: width - 3] + "..."


def render_table(result: QueryResult, max_width: int = 40) -> str:
    if not result.columns:
        return "(没有返回列)"
    values = [[_shorten(value, max_width) for value in row] for row in result.rows]
    widths = [len(str(column)) for column in result.columns]
    for row in values:
        for index, value in enumerate(row):
            widths[index] = min(max(widths[index], len(value)), max_width)

    def line(char: str = "-") -> str:
        return "+" + "+".join(char * (width + 2) for width in widths) + "+"

    def row_text(row: list[str]) -> str:
        cells = [
            f" {row[index]:<{widths[index]}} " for index in range(len(widths))
        ]
        return "|" + "|".join(cells) + "|"

    output = [line(), row_text([str(column) for column in result.columns]), line("=")]
    output.extend(row_text(row) for row in values)
    output.append(line())
    output.append(
        f"query_id={result.query_id} rows={result.returned_rows} "
        f"elapsed_ms={result.elapsed_ms} truncated={str(result.truncated).lower()}"
    )
    for warning in result.warnings:
        output.append(f"警告: {warning}")
    return "\n".join(output)


def _delimited_text(result: QueryResult, delimiter: str) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
    writer.writerow(result.columns)
    writer.writerows(result.rows)
    return stream.getvalue()


def result_as_text(result: QueryResult, output_format: str) -> str:
    if output_format == "table":
        return render_table(result)
    if output_format == "csv":
        return _delimited_text(result, ",")
    if output_format == "tsv":
        return _delimited_text(result, "\t")
    if output_format == "json":
        payload = {
            "query_id": result.query_id,
            "elapsed_ms": result.elapsed_ms,
            "returned_rows": result.returned_rows,
            "truncated": result.truncated,
            "columns": result.columns,
            "rows": [dict(zip(result.columns, row)) for row in result.rows],
            "warnings": result.warnings,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    raise MedDBError(f"不支持的输出格式: {output_format}")


def resolve_output_path(export_root: Path, requested: str, output_format: str) -> Path:
    export_root = export_root.resolve()
    requested_path = Path(requested)
    candidate = (
        requested_path.resolve()
        if requested_path.is_absolute()
        else (export_root / requested_path).resolve()
    )
    try:
        candidate.relative_to(export_root)
    except ValueError as exc:
        raise MedDBError(f"导出文件必须位于目录内: {export_root}") from exc
    suffix = f".{output_format}" if output_format != "table" else ".txt"
    if not candidate.suffix:
        candidate = candidate.with_suffix(suffix)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def write_output(
    result: QueryResult,
    export_root: Path,
    requested: str,
    output_format: str,
) -> Path:
    path = resolve_output_path(export_root, requested, output_format)
    text = result_as_text(result, output_format)
    encoding = "utf-8-sig" if output_format in {"csv", "tsv"} else "utf-8"
    path.write_text(text, encoding=encoding, newline="")
    return path


def copy_to_clipboard(result: QueryResult) -> None:
    text = _delimited_text(result, "\t")
    if shutil.which("clip.exe"):
        command = ["clip.exe"]
    elif shutil.which("pbcopy"):
        command = ["pbcopy"]
    elif shutil.which("wl-copy"):
        command = ["wl-copy"]
    elif shutil.which("xclip"):
        command = ["xclip", "-selection", "clipboard"]
    else:
        raise MedDBError("没有找到系统剪贴板命令")
    completed = subprocess.run(
        command,
        input=text,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise MedDBError("复制到剪贴板失败")

