#!/usr/bin/env python3
"""Validate the auditor gate and render a source-checked planning pack."""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CITE = re.compile(r"\[(rule|metric):([^\]]+)\]")
FIGURE = re.compile(r"\{\{(\$\.[^}]+)\}\}")
RISK_HEADING = re.compile(r"^## (R-[\w-]+):", re.M)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def scalar(data, path):
    if not path.startswith("$."):
        raise ValueError(f"invalid figure path: {path}")
    value = data
    for part in path[2:].split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"missing figure or metric: {path}")
        value = value[part]
    if value is None or isinstance(value, (list, dict)):
        raise ValueError(f"non-scalar figure or metric: {path}")
    return value


def decisions(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and re.fullmatch(r"R-[\w-]+", cells[0]):
            if cells[0] in result:
                raise ValueError(f"duplicate auditor decision: {cells[0]}")
            result[cells[0]] = (cells[1].lower(), cells[2])
    return result


def gate(out):
    register = (out / "risk-register.md").read_text(encoding="utf-8")
    ratings = read_json(out / "risk-ratings.json")
    revision = ratings.get("revision")
    if "## Revision after auditor gate" not in register or not isinstance(revision, dict):
        raise ValueError("register is not a post-auditor revision")
    risks = ratings["risks"]
    ids = [r["id"] for r in risks]
    headings = RISK_HEADING.findall(register)
    table = decisions(out / "auditor-comments.md")
    if len(ids) != len(set(ids)) or set(headings) != set(ids) or not set(ids).issubset(table):
        raise ValueError("missing or extra risk between register, ratings, and auditor decisions")
    approved = []
    for risk in risks:
        decision, comment = table[risk["id"]]
        if decision not in {"approve", "reject"}:
            raise ValueError(f"risk undecided: {risk['id']} ({decision or 'blank'})")
        if decision == "approve":
            approved.append(risk)
    return approved


def csv_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def citations(text, rules, analytics):
    refs = CITE.findall(text)
    for kind, ref in refs:
        if kind == "rule" and ref not in rules:
            raise ValueError(f"unknown rule citation: {ref}")
        if kind == "metric":
            scalar(analytics, ref)
    if re.search(r"\[(?:rule|metric):", CITE.sub("", text)):
        raise ValueError("malformed citation")
    return {ref for kind, ref in refs if kind == "rule"}


def format_figure(value):
    """Display a computed figure: money/decimals to 2 places, large numbers with thousands separators."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    number = int(value)
    return f"{number:,}" if abs(number) >= 10000 else str(number)


def render(text, figures):
    rendered = FIGURE.sub(lambda m: format_figure(scalar(figures, m[1])), text)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("malformed figure placeholder")
    return rendered


def sample_transactions(rows, source, spend, related):
    by_id = {row["row_id"]: row for row in spend}
    by_supplier = {}
    for row in spend:
        by_supplier.setdefault(row["supplier_normalized"], []).append(row)
    by_window = {}
    for row in related:
        by_window.setdefault(row["window_id"], []).append(row["row_id"])
    selected = []
    for row in rows:
        ids = []
        if "row_id" in row:
            ids = [row["row_id"]]
        elif "row_id_1" in row and "row_id_2" in row:
            ids = [row["row_id_1"], row["row_id_2"]]
        elif source == "split_purchase_windows.csv":
            ids = by_window.get(row["window_id"], [])
        elif "supplier_normalized" in row:
            supplier_rows = by_supplier.get(row["supplier_normalized"], [])
            ids = [item["row_id"] for item in sorted(supplier_rows, key=lambda x: (-abs(float(x["net_amount"])), x["row_id"]))[:1]]
        else:
            raise ValueError(f"sample source has no transaction link: {source}")
        if not ids:
            raise ValueError(f"sample indicator has no spend transaction: {row}")
        for row_id in ids:
            if row_id not in by_id:
                raise ValueError(f"sample transaction absent from spend-clean.csv: {row_id}")
            selected.append({"indicator_file": source, "indicator_id": row.get("pair_id") or row.get("window_id") or row.get("supplier_normalized") or row_id, **by_id[row_id]})
    return list({row["row_id"]: row for row in selected}.values())


def build(out):
    approved = gate(out)
    plan = read_json(out / "audit-plan.json")
    rules = {item["rule_id"] for item in read_json(out / "rules.json")["rules"]}
    analytics = read_json(out / "analytics.json")
    spend = csv_rows(out / "analytics-full" / "spend-clean.csv")
    if not spend or not {"row_id", "supplier_normalized", "net_amount"}.issubset(spend[0]):
        raise ValueError("analytics-full/spend-clean.csv is empty or lacks transaction columns")
    if len({r["row_id"] for r in spend}) != len(spend):
        raise ValueError("duplicate spend row_id")
    planned = plan["risks"]
    if {r["risk_id"] for r in planned} != {r["id"] for r in approved} or len(planned) != len(approved):
        raise ValueError("audit plan has a missing, duplicate, or unapproved risk")
    risk_map = {r["id"]: r for r in approved}
    cited = set()
    for risk in approved:
        for field in ("risk", "evidence", "proposed_focus"):
            cited |= citations(risk[field], rules, analytics)
        for field in ("likelihood", "impact"):
            cited |= citations(risk[field]["justification"], rules, analytics)
    figures = {"pack": {"risks_in_scope": len(approved), "risk_scores": {}, "samples": {}}}
    figures.update(analytics)
    matrix = ["# Risk-control matrix", "", "Ratings and tests address indicators for auditor investigation.", "", "| Risk | Expected control | Test |", "| --- | --- | --- |"]
    program = ["# Audit program", "", "Indicators require evidence and auditor judgment; they do not establish non-compliance.", ""]
    samples = {}
    pbc = []
    test_ids = set()
    for item in planned:
        risk_id = item["risk_id"]
        risk = risk_map[risk_id]
        controls = {control["id"]: control for control in item["controls"]}
        if not controls or len(controls) != len(item["controls"]):
            raise ValueError(f"missing or duplicate control: {risk_id}")
        for control in controls.values():
            if not control.get("rules"):
                raise ValueError(f"uncited control: {control['id']}")
            for rule in control["rules"]:
                if rule not in rules:
                    raise ValueError(f"unknown control rule: {rule}")
                cited.add(rule)
            cited |= citations(control["text"], rules, analytics)
        covered = set()
        score = min(5, risk["likelihood"]["base"] + bool(risk.get("repeat_finding"))) * risk["impact"]["score"]
        figures["pack"]["risk_scores"][risk_id] = score
        minimum = 15 if score >= 8 else 10 if score >= 4 else 5
        program.extend([f"## {risk_id}: {risk['risk']}", ""])
        for test in item["tests"]:
            test_id = test["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", test_id) or test_id in test_ids:
                raise ValueError(f"invalid or duplicate test ID: {test_id}")
            test_ids.add(test_id)
            links = set(test["control_ids"])
            if not links or not links <= controls.keys():
                raise ValueError(f"test has unknown or missing control: {test_id}")
            covered |= links
            refs = test.get("citations", [])
            if not refs or not any(CITE.fullmatch(ref) for ref in refs):
                raise ValueError(f"uncited test: {test_id}")
            for ref in refs:
                if not CITE.fullmatch(ref):
                    raise ValueError(f"invalid test citation: {ref}")
                cited |= citations(ref, rules, analytics)
            cited |= citations(test["step"], rules, analytics)
            recipe = test["sample"]
            name = recipe["file"]
            if Path(name).name != name or not name.endswith(".csv"):
                raise ValueError(f"invalid sample file: {name}")
            candidates = csv_rows(out / "analytics-full" / name)
            if not candidates:
                raise ValueError(f"empty sample source: {name}")
            columns = set(candidates[0])
            filters = recipe["filter"]
            sorts = recipe["sort"]
            if not isinstance(filters, dict) or not isinstance(sorts, list) or not set(filters) <= columns or any(s.lstrip("-") not in columns for s in sorts):
                raise ValueError(f"invalid sample filter or sort: {test_id}")
            chosen = [r for r in candidates if all(r[k] == str(v) for k, v in filters.items())]
            for key in reversed(sorts):
                descending = key.startswith("-")
                col = key.lstrip("-")
                def sort_value(row):
                    try:
                        return (0, float(row[col]))
                    except ValueError:
                        return (1, row[col])
                chosen.sort(key=sort_value, reverse=descending)
            if not sorts:
                chosen.sort(key=lambda r: tuple(r.values()))
            requested = recipe["count"]
            if type(requested) is not int or requested < 1 or not chosen:
                raise ValueError(f"invalid or empty sample: {test_id}")
            chosen = chosen[:max(requested, minimum)]
            related = csv_rows(out / "analytics-full" / "split_purchase_payments.csv") if name == "split_purchase_windows.csv" else []
            selected = sample_transactions(chosen, name, spend, related)
            samples[test_id] = selected
            figures["pack"]["samples"][test_id] = {"indicators": len(chosen), "transactions": len(selected)}
            program.extend([f"### {test_id}", "", f"Control: {', '.join(sorted(links))}. {test['step']} {' '.join(refs)}", "", f"Sample: {len(chosen)} indicators, {len(selected)} verified transactions from `{name}`. See `samples/{test_id}.csv`.", ""])
            for need in test["pbc"]:
                if need not in pbc:
                    pbc.append(need)
            for control_id in sorted(links):
                control = controls[control_id]
                matrix.append(f"| {risk_id} | {control_id}: {control['text']} {' '.join('[rule:' + r + ']' for r in control['rules'])} | {test_id}: {test['step']} |")
        if covered != controls.keys():
            raise ValueError(f"control without test: {risk_id}: {sorted(controls.keys() - covered)}")
    not_tested = plan.get("not_tested_rules", [])
    omitted = set()
    for entry in not_tested:
        rule = entry["rule_id"]
        if rule not in rules or not entry.get("reason", "").strip() or rule in omitted:
            raise ValueError(f"invalid not-tested rule: {rule}")
        omitted.add(rule)
    if rules - cited - omitted:
        raise ValueError(f"unaccounted rule: {sorted(rules - cited - omitted)[0]}")
    memo = render(plan["memo"], figures)
    citations(memo, rules, analytics)
    program.extend(["## PBC list", "", *(f"- {item}" for item in pbc), ""])
    figures.pop("tests", None)
    figures.pop("run", None)
    figures.pop("cleaning", None)
    # Keep only builder-produced figures in the ledger; analytics remain the cited source.
    ledger = {"pack": figures["pack"]}
    sample_dir = out / "samples"
    sample_dir.mkdir(exist_ok=True)
    for test_id, rows in samples.items():
        with (sample_dir / f"{test_id}.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    for name, content in (("planning-memo.md", "# Planning memo\n\n" + memo + "\n"), ("risk-control-matrix.md", "\n".join(matrix) + "\n"), ("audit-program.md", "\n".join(program) + "\n")):
        (out / name).write_text(content, encoding="utf-8")
    (out / "pack-figures.json").write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return len(approved), len(samples)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", type=Path, default=ROOT / "outputs")
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()
    try:
        if args.gate:
            print(f"Gate passed: {len(gate(args.outputs))} approved risks")
        else:
            count, tests = build(args.outputs)
            print(f"Built pack: {count} approved risks, {tests} tests")
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Audit program failed: {exc}\n")


if __name__ == "__main__":
    main()
