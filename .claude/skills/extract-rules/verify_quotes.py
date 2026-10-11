"""Validate an extract-rules output against its source PDF."""

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


PARAMETERS = {
    "goods_services_threshold",
    "works_concessions_threshold",
    "light_touch_threshold",
}
RULE_TYPES = {
    "threshold", "procedure", "approval", "record", "prohibition",
    "anti-avoidance", "exception", "definition",
}
BASIS = {"contract_value", "annual_value", "payment_value", "other"}
APPENDIX_CLAUSES = {
    "App A:row1", "App A:row2", "App A:row3", "App A:row4",
    "App B:A", "App B:B1", "App B:B2", "App B:C", "App B:D",
    "App B:footnotes", "App C:A", "App C:B", "App C:C",
    "App C:D", "App C:E", "App C:F", "App C:final",
}
CLAUSE_PATTERN = re.compile(r"(?m)^\s*(\d+(?:\.\d+)+)\s+")
RULE_ID_PATTERN = re.compile(r"CPR-(\d{3})\Z")


def normalized(value):
    return " ".join(unicodedata.normalize("NFC", value).split())


def pages_from_pdf(pdf):
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    pages = result.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if not pages:
        raise ValueError("PDF has no extractable text")
    return pages


def nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def printed_gbp(printed):
    match = re.search(r"£(\d+(?:\.\d+)?)\s*million\b", printed, re.I)
    if match:
        return int(float(match.group(1)) * 1_000_000)
    match = re.search(r"£([\d,]+)(?![\d,.])", printed)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def validate(data, pages):
    errors = []
    if not isinstance(data, dict):
        return ["root: expected object"]
    if set(data) != {"parameters", "rules", "excluded", "notes"}:
        errors.append("root: expected exactly parameters, rules, excluded, notes")
    parameters = data.get("parameters")
    if not isinstance(parameters, dict) or set(parameters) != PARAMETERS:
        errors.append("parameters: expected exactly the three declared Threshold categories")
        parameters = {}
    for key in PARAMETERS:
        if key in parameters and parameters[key] is not None:
            errors.append(f"parameters.{key}: value must remain null until supplied by an authoritative source")

    rules = data.get("rules")
    excluded = data.get("excluded")
    notes = data.get("notes")
    if not isinstance(rules, list):
        errors.append("rules: expected array")
        rules = []
    if not isinstance(excluded, list):
        errors.append("excluded: expected array")
        excluded = []
    if not isinstance(notes, list):
        errors.append("notes: expected array")
        notes = []

    covered = set()
    ids = []
    rule_fields = {"rule_id", "clause", "page", "type", "scope", "thresholds", "required_action", "approver", "evidence_source", "verbatim_quote"}
    for index, rule in enumerate(rules, 1):
        label = f"rules[{index - 1}]"
        if not isinstance(rule, dict):
            errors.append(f"{label}: expected object")
            continue
        if set(rule) != rule_fields:
            errors.append(f"{label}: expected fields {', '.join(sorted(rule_fields))}")
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not RULE_ID_PATTERN.fullmatch(rule_id):
            errors.append(f"{label}.rule_id: expected CPR-001 format")
        else:
            ids.append(rule_id)
            if rule_id != f"CPR-{index:03d}":
                errors.append(f"{label}.rule_id: expected CPR-{index:03d}")
        clause = rule.get("clause")
        if not nonempty_string(clause):
            errors.append(f"{label}.clause: required string")
        else:
            covered.add(clause)
        page = rule.get("page")
        valid_page = type(page) is int and 1 <= page <= len(pages)
        if not valid_page:
            errors.append(f"{label}.page: expected PDF page 1–{len(pages)}")
        if rule.get("type") not in RULE_TYPES:
            errors.append(f"{label}.type: unknown rule type")
        for field in ("scope", "required_action"):
            if not nonempty_string(rule.get(field)):
                errors.append(f"{label}.{field}: required string")
        for field in ("approver", "evidence_source"):
            if rule.get(field) is not None and not nonempty_string(rule[field]):
                errors.append(f"{label}.{field}: expected string or null")
        quote = rule.get("verbatim_quote")
        if not nonempty_string(quote):
            errors.append(f"{label}.verbatim_quote: required string")
        elif valid_page and normalized(quote) not in normalized(pages[page - 1]):
            errors.append(f"{label}.verbatim_quote: not found on PDF page {page}")
        bands = rule.get("thresholds")
        if not isinstance(bands, list):
            errors.append(f"{label}.thresholds: expected array")
            continue
        for band_index, band in enumerate(bands):
            band_label = f"{label}.thresholds[{band_index}]"
            if not isinstance(band, dict) or set(band) != {"basis", "low", "high"}:
                errors.append(f"{band_label}: expected basis, low, high")
                continue
            if band["basis"] not in BASIS:
                errors.append(f"{band_label}.basis: unknown basis")
            for side in ("low", "high"):
                bound = band[side]
                bound_label = f"{band_label}.{side}"
                if not isinstance(bound, dict) or set(bound) != {"printed", "gbp", "inclusive", "parameter"}:
                    errors.append(f"{bound_label}: expected printed, gbp, inclusive, parameter")
                    continue
                printed, gbp, inclusive, parameter = (bound[k] for k in ("printed", "gbp", "inclusive", "parameter"))
                if printed is not None and not nonempty_string(printed):
                    errors.append(f"{bound_label}.printed: expected nonempty string or null")
                if gbp is not None and (type(gbp) is not int or gbp < 0):
                    errors.append(f"{bound_label}.gbp: expected nonnegative integer or null")
                if inclusive is not None and type(inclusive) is not bool:
                    errors.append(f"{bound_label}.inclusive: expected boolean or null")
                if parameter is not None and parameter not in PARAMETERS:
                    errors.append(f"{bound_label}.parameter: undeclared parameter {parameter!r}")
                if parameter is not None and gbp is not None:
                    errors.append(f"{bound_label}: parameter value must remain null")
                if printed is None and any(x is not None for x in (gbp, inclusive, parameter)):
                    errors.append(f"{bound_label}: unbounded side must have four null fields")
                if printed is not None and inclusive is None:
                    errors.append(f"{bound_label}.inclusive: required for printed bound")
                if printed is not None and parameter is None and gbp is None and "£" not in printed:
                    errors.append(f"{bound_label}: unresolved named threshold needs a declared parameter")
                if printed is not None and gbp is not None:
                    amount = printed_gbp(printed)
                    if amount is None or amount != gbp:
                        errors.append(f"{bound_label}.gbp: does not match printed GBP amount")
                if printed is not None and "£" in printed and "." in printed and "million" not in printed.lower() and gbp is not None:
                    errors.append(f"{bound_label}.gbp: printed penny amount cannot be an integer GBP value")

    if len(ids) != len(set(ids)):
        errors.append("rules: duplicate rule IDs")
    for index, item in enumerate(excluded):
        label = f"excluded[{index}]"
        if not isinstance(item, dict) or set(item) != {"clause", "page", "reason"}:
            errors.append(f"{label}: expected clause, page, reason")
            continue
        if not nonempty_string(item["clause"]):
            errors.append(f"{label}.clause: required string")
        else:
            covered.add(item["clause"])
        if type(item["page"]) is not int or not 1 <= item["page"] <= len(pages):
            errors.append(f"{label}.page: invalid PDF page")
        if not nonempty_string(item["reason"]) or "\n" in item["reason"]:
            errors.append(f"{label}.reason: expected one-line reason")
    for index, item in enumerate(notes):
        label = f"notes[{index}]"
        if not isinstance(item, dict) or set(item) != {"clauses", "pages", "issue"}:
            errors.append(f"{label}: expected clauses, pages, issue")
            continue
        if not isinstance(item["clauses"], list) or not item["clauses"] or not all(nonempty_string(c) for c in item["clauses"]):
            errors.append(f"{label}.clauses: expected nonempty string array")
        if not isinstance(item["pages"], list) or not item["pages"] or not all(type(p) is int and 1 <= p <= len(pages) for p in item["pages"]):
            errors.append(f"{label}.pages: expected valid PDF page array")
        if not nonempty_string(item["issue"]):
            errors.append(f"{label}.issue: required string")

    source_clauses = set().union(*(set(CLAUSE_PATTERN.findall(page)) for page in pages))
    expected = source_clauses | APPENDIX_CLAUSES
    for clause in sorted(expected - covered):
        errors.append(f"coverage: {clause} missing from rules or excluded")
    for clause in sorted(covered - expected):
        errors.append(f"coverage: {clause} not found in source clause ledger")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rules_json", type=Path)
    parser.add_argument("source_pdf", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.rules_json.read_text(encoding="utf-8"))
        pages = pages_from_pdf(args.source_pdf)
        errors = validate(data, pages)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"verification could not run: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(f"FAIL: {len(errors)} issue(s)", file=sys.stderr)
        return 1
    print(f"PASS: {len(data['rules'])} rules, {len(data['excluded'])} exclusions, {len(pages)} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
