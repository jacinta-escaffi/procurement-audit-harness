"""Verify source citations and structure in outputs/history.json."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any


TOP_LEVEL_FIELDS = {
    "schema_version",
    "sources",
    "audit_coverage",
    "follow_ups",
    "agreed_actions",
    "risk_themes",
    "risk_method",
    "gaps",
    "discrepancies",
    "verification",
}
RISK_METHOD_FIELDS = {
    "evaluation_levels",
    "impact_scale",
    "likelihood_scale",
    "matrix",
    "bands",
    "appetite",
}
REQUIRED_CITATION_FIELDS = {"source_file", "pdf_page", "quote"}
SOURCE_FIELDS = {"source_file", "pdf_pages", "plain_text_path", "layout_text_path"}


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\u00ad", "").replace("\u00a0", " ")
    return " ".join(value.split())


def pages_from_pdf(pdf: Path, layout: bool) -> list[str]:
    command = ["pdftotext", "-enc", "UTF-8"]
    if layout:
        command.append("-layout")
    command.extend([str(pdf), "-"])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    pages = result.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if not pages:
        raise ValueError(f"{pdf}: no extractable pages")
    return pages


def source_pages(committee_dir: Path) -> dict[str, dict[str, list[str]]]:
    result = {}
    for pdf in sorted(committee_dir.glob("*.pdf")):
        plain = pages_from_pdf(pdf, layout=False)
        layout = pages_from_pdf(pdf, layout=True)
        if len(plain) != len(layout):
            raise ValueError(f"{pdf}: plain/layout page counts differ")
        result[pdf.name] = {"plain": plain, "layout": layout}
    if not result:
        raise ValueError(f"{committee_dir}: no PDF files")
    return result


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_citation(
    item: Any,
    label: str,
    pages: dict[str, dict[str, list[str]]],
    errors: list[str],
) -> None:
    if not isinstance(item, dict):
        errors.append(f"{label}: expected object")
        return
    missing = REQUIRED_CITATION_FIELDS - set(item)
    if missing:
        errors.append(f"{label}: missing citation fields {', '.join(sorted(missing))}")
        return
    source = item.get("source_file")
    page = item.get("pdf_page")
    quote = item.get("quote")
    if not nonempty(source) or source not in pages:
        errors.append(f"{label}.source_file: unknown source {source!r}")
        return
    page_count = len(pages[source]["plain"])
    if type(page) is not int or not 1 <= page <= page_count:
        errors.append(f"{label}.pdf_page: expected 1-{page_count}")
        return
    if not nonempty(quote):
        errors.append(f"{label}.quote: expected nonempty copy-exact text")
        return
    needle = normalized(quote)
    plain = normalized(pages[source]["plain"][page - 1])
    layout = normalized(pages[source]["layout"][page - 1])
    if needle not in plain and needle not in layout:
        errors.append(f"{label}.quote: not found on {source} PDF page {page}")


def validate_scoring_fields(method: Any, errors: list[str]) -> None:
    """The register builder reads these exact fields, so a scoring method that is present must use them.
    An empty scale is allowed: the strategy may genuinely lack a usable method (CLAUDE.md fallback)."""
    if not isinstance(method, dict):
        return
    for name in ("likelihood_scale", "impact_scale"):
        rows = method.get(name)
        if not isinstance(rows, list) or not rows:
            continue
        ratings = []
        for index, row in enumerate(rows):
            label = f"risk_method.{name}[{index}]"
            if not isinstance(row, dict):
                continue
            rating = row.get("rating")
            if type(rating) is not int or not 1 <= rating <= 5:
                errors.append(f"{label}.rating: expected an integer 1-5 under the key 'rating' "
                              "(the risk register builder reads row['rating'] and row['label'])")
            else:
                ratings.append(rating)
            if not nonempty(row.get("label")):
                errors.append(f"{label}.label: expected the printed level name, e.g. 'Likely' or 'Major'")
        if ratings and sorted(ratings) != [1, 2, 3, 4, 5]:
            errors.append(f"risk_method.{name}: expected exactly one row for each rating 1-5, found {sorted(ratings)}")
    matrix = method.get("matrix")
    if isinstance(matrix, list) and matrix:
        cells = set()
        for index, row in enumerate(matrix):
            label = f"risk_method.matrix[{index}]"
            if not isinstance(row, dict):
                continue
            likelihood, impact, score = row.get("likelihood"), row.get("impact"), row.get("score")
            if not all(type(v) is int for v in (likelihood, impact, score)):
                errors.append(f"{label}: expected integer 'likelihood', 'impact' and 'score'")
                continue
            if not nonempty(row.get("band")):
                errors.append(f"{label}.band: expected the printed band name")
            cells.add((likelihood, impact))
        if len(cells) != 25:
            errors.append(f"risk_method.matrix: expected 25 distinct (likelihood, impact) cells, found {len(cells)}")


def validate(data: Any, pages: dict[str, dict[str, list[str]]]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root: expected object"]
    if set(data) != TOP_LEVEL_FIELDS:
        errors.append("root: expected exactly " + ", ".join(sorted(TOP_LEVEL_FIELDS)))
    if data.get("schema_version") != 1:
        errors.append("schema_version: expected 1")

    sources = data.get("sources")
    if not isinstance(sources, list):
        errors.append("sources: expected array")
        sources = []
    declared = set()
    for index, item in enumerate(sources):
        label = f"sources[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        if set(item) != SOURCE_FIELDS:
            errors.append(f"{label}: expected exactly {', '.join(sorted(SOURCE_FIELDS))}")
        source = item.get("source_file")
        if not nonempty(source) or source not in pages:
            errors.append(f"{label}.source_file: unknown source {source!r}")
            continue
        declared.add(source)
        expected_pages = len(pages[source]["plain"])
        if item.get("pdf_pages") != expected_pages:
            errors.append(f"{label}.pdf_pages: expected {expected_pages}")
    if declared != set(pages):
        errors.append("sources: must declare every and only PDF in the committee directory")

    for key in ("audit_coverage", "follow_ups", "agreed_actions", "risk_themes", "gaps"):
        collection = data.get(key)
        if not isinstance(collection, list):
            errors.append(f"{key}: expected array")
            continue
        for index, item in enumerate(collection):
            validate_citation(item, f"{key}[{index}]", pages, errors)

    method = data.get("risk_method")
    if not isinstance(method, dict) or set(method) != RISK_METHOD_FIELDS:
        errors.append("risk_method: expected exactly " + ", ".join(sorted(RISK_METHOD_FIELDS)))
        method = {}
    for key in RISK_METHOD_FIELDS:
        collection = method.get(key)
        if not isinstance(collection, list):
            errors.append(f"risk_method.{key}: expected array")
            continue
        for index, item in enumerate(collection):
            validate_citation(item, f"risk_method.{key}[{index}]", pages, errors)

    discrepancies = data.get("discrepancies")
    if not isinstance(discrepancies, list):
        errors.append("discrepancies: expected array")
    else:
        for index, item in enumerate(discrepancies):
            label = f"discrepancies[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{label}: expected object")
                continue
            if not nonempty(item.get("issue")):
                errors.append(f"{label}.issue: expected nonempty description")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or len(evidence) < 2:
                errors.append(f"{label}.evidence: expected at least two cited items")
                continue
            for evidence_index, cited in enumerate(evidence):
                validate_citation(cited, f"{label}.evidence[{evidence_index}]", pages, errors)

    validate_scoring_fields(data.get("risk_method"), errors)

    verification = data.get("verification")
    if not isinstance(verification, dict) or verification.get("status") not in {"pending", "passed", "failed"}:
        errors.append("verification.status: expected pending, passed, or failed")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history_json", type=Path)
    parser.add_argument("committee_dir", type=Path, nargs="?", default=Path("data/committee"))
    args = parser.parse_args()
    try:
        data = json.loads(args.history_json.read_text(encoding="utf-8"))
        pages = source_pages(args.committee_dir)
        errors = validate(data, pages)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"verification could not run: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(f"FAIL: {len(errors)} issue(s)", file=sys.stderr)
        return 1
    cited_count = sum(
        len(data[key]) for key in ("audit_coverage", "follow_ups", "agreed_actions", "risk_themes", "gaps")
    ) + sum(len(data["risk_method"][key]) for key in RISK_METHOD_FIELDS)
    print(f"PASS: {cited_count} cited items, {len(data['discrepancies'])} discrepancies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
