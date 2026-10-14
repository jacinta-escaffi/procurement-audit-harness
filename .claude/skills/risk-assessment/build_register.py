#!/usr/bin/env python3
"""Validate risk judgments against source JSON and render the auditor gate."""

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CITE = re.compile(r"\[(rule|metric|history):([^\]]+)\]")
FIGURE = re.compile(r"\{\{(\$\.[^}]+)\}\}")
NUMBER = re.compile(r"(?<![\w$])(?:£|\$)?\d[\d,]*(?:\.\d+)?%?(?![\w])")
FIELDS = ("risk", "likelihood.base", "likelihood.justification", "impact.score", "impact.justification", "evidence", "proposed_focus", "repeat_finding")


def load(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def path_value(data, path):
    if not path.startswith("$."):
        raise ValueError(f"invalid metric path {path}")
    value = data
    for part in path[2:].split("."):
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", part)
        if not match or not isinstance(value, dict) or match[1] not in value:
            raise ValueError(f"missing metric path {path}")
        value = value[match[1]]
        if match[2] is not None:
            value = value[int(match[2])]
    if isinstance(value, (dict, list)) or value is None:
        raise ValueError(f"metric path is not a scalar: {path}")
    return value


def history_item(history, reference):
    match = re.fullmatch(r"(audit_coverage|follow_ups):(\d+)", reference)
    if not match:
        raise ValueError(f"invalid history ID {reference}")
    try:
        return history[match[1]][int(match[2])]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"missing history ID {reference}") from exc


def resolve(text, rules, analytics, history):
    found = CITE.findall(text)
    for kind, reference in found:
        if kind == "rule" and reference not in rules:
            raise ValueError(f"missing rule ID {reference}")
        if kind == "metric":
            path_value(analytics, reference)
        if kind == "history":
            history_item(history, reference)
    for path in FIGURE.findall(text):
        path_value(analytics, path)
    if "{{" in FIGURE.sub("", text) or "}}" in FIGURE.sub("", text):
        raise ValueError("malformed figure placeholder")
    return found


def format_figure(value):
    """Display a computed figure: money/decimals to 2 places, large numbers with thousands separators."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    number = int(value)
    return f"{number:,}" if abs(number) >= 10000 else str(number)


def render_text(value, analytics):
    return FIGURE.sub(lambda match: format_figure(path_value(analytics, match[1])), value).replace("|", "\\|").replace("\n", " ")


def flatten(risk, field):
    value = risk
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def decisions_file(path, risks):
    previous = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[0] not in ("Risk ID", "---") and not cells[0].startswith("-"):
                previous[cells[0]] = cells[1:]
    lines = ["# Auditor decisions", "", "Enter Approve, Reject, or Change. Describe the requested fields in Comment for Change.", "", "| Risk ID | Decision | Comment |", "| --- | --- | --- |"]
    for risk in risks:
        decision, comment = previous.get(risk["id"], ("", ""))
        lines.append(f'| {risk["id"]} | {decision} | {comment} |')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {risk["id"]: previous.get(risk["id"], ("", "")) for risk in risks}


def validate_revision(current, snapshot, decisions, revision):
    old = {risk["id"]: risk for risk in snapshot}
    new = {risk["id"]: risk for risk in current}
    declared = {change["id"]: change for change in revision.get("changes", [])}
    if len(declared) != len(revision.get("changes", [])):
        raise ValueError("duplicate revision entry")
    if set(new) - set(old):
        raise ValueError("new risk was not requested by auditor")
    notes = []
    for risk_id, original in old.items():
        decision, comment = decisions.get(risk_id, ("", ""))
        decision = decision.lower()
        if decision not in ("approve", "reject", "change"):
            raise ValueError(f"auditor decision missing or invalid for {risk_id}")
        exists = risk_id in new
        changed = {field for field in FIELDS if exists and flatten(original, field) != flatten(new[risk_id], field)}
        entry = declared.get(risk_id)
        if decision == "approve" and (not exists or changed or entry):
            raise ValueError(f"unrequested change to approved {risk_id}")
        if decision == "reject" and (exists or entry):
            raise ValueError(f"rejection unapplied for {risk_id}")
        if decision == "change":
            if not comment or not exists or not changed or not entry or set(entry.get("fields", [])) != changed or not entry.get("reason"):
                raise ValueError(f"auditor change unapplied or undeclared for {risk_id}")
            notes.append(f"{risk_id}: {', '.join(sorted(changed))} — {entry['reason']} (auditor override: {comment})")
        elif decision == "reject":
            notes.append(f"{risk_id}: removed at auditor request")
    if set(declared) - {risk_id for risk_id, (decision, _) in decisions.items() if decision.lower() == "change"}:
        raise ValueError("revision contains unrequested change")
    return notes


def scale_labels(rows, axis):
    """Map rating -> label. Accepts 'rating' (the documented key) or the axis name ('likelihood'/'impact')."""
    labels = {}
    for row in rows:
        rating = row.get("rating", row.get(axis))
        label = row.get("label") or row.get("name")
        if type(rating) is int and label:
            labels[rating] = label
    return labels


def build(directory):
    rules_doc = load(directory / "rules.json")
    rules = {item["rule_id"] for item in rules_doc["rules"]}
    analytics = load(directory / "analytics.json")
    history = load(directory / "history.json")
    ratings = load(directory / "risk-ratings.json")
    risks = ratings["risks"]
    if len({risk["id"] for risk in risks}) != len(risks):
        raise ValueError("duplicate risk ID")
    matrix = {(row["likelihood"], row["impact"]): row for row in history["risk_method"]["matrix"]}
    likelihood_labels = scale_labels(history["risk_method"]["likelihood_scale"], "likelihood")
    impact_labels = scale_labels(history["risk_method"]["impact_scale"], "impact")
    if len(matrix) != 25 or len(likelihood_labels) != 5 or len(impact_labels) != 5:
        raise ValueError("incomplete council scoring method")
    snapshot_path = directory / "risk-ratings.gate.json"
    comments_path = directory / "auditor-comments.md"
    pre_gate = not snapshot_path.exists()
    if snapshot_path.exists():
        prior = load(snapshot_path)["risks"]
        decisions = decisions_file(comments_path, prior)
        pre_gate = all(not decision.strip() and not comment.strip() for decision, comment in decisions.values())
        if pre_gate:
            if "revision" in ratings:
                raise ValueError("revision supplied before auditor gate")
            notes = []
        else:
            notes = validate_revision(risks, prior, decisions, ratings.get("revision", {}))
    else:
        if "revision" in ratings:
            raise ValueError("revision supplied before auditor gate")
        decisions = {}
        notes = []
    lines = ["# Procurement risk register", "", "Ratings describe risk indicators for auditor investigation, not established non-compliance.", "", "Scoring: current (Actual) likelihood × impact. The 25-cell council matrix in `history.json` supplies the score and displayed band. Its High label is used for cells where the response table says Medium-High (scores 8–12); the response-table wording is retained here as a source conflict. The strategy also conflicts on the Corporate Risk Register threshold (Extreme 15–25 versus score 9 or above), so this register makes no escalation decision. For the overlapping Medium/Major financial impact ranges, the printed £500k boundary is used; Section 4.3's Medium (2) conflicts with Table 1's Medium (3), and Table 1 controls the impact label.", ""]
    if not pre_gate:
        lines.extend(["## Revision after auditor gate", "", *(f"- {note}" for note in notes), ""])
    kept = []
    for risk in risks:
        risk_id = risk["id"]
        try:
            likelihood = risk["likelihood"]
            impact = risk["impact"]
            for field in (likelihood["justification"], impact["justification"]):
                if not resolve(field, rules, analytics, history):
                    raise ValueError("justification has no citation")
            for field in (risk["risk"], risk["evidence"], risk["proposed_focus"]):
                resolve(field, rules, analytics, history)
            base = likelihood["base"]
            impact_score = impact["score"]
            uplift = risk.get("repeat_finding")
            if uplift:
                citation = uplift["history"]
                match = CITE.fullmatch(citation)
                if not match or match[1] != "history":
                    raise ValueError("repeat finding needs a history citation")
                audit = history_item(history, match[2])
                opinion = audit.get("overall_opinion", audit.get("report_opinion", "")).lower()
                if "limited" not in opinion and "weak" not in opinion:
                    raise ValueError("repeat finding audit has no adverse opinion")
                if not uplift.get("reason"):
                    raise ValueError("repeat finding needs same-theme reason")
            score_l = min(5, base + bool(uplift))
            if type(base) is not int or type(impact_score) is not int or base not in range(1, 6) or impact_score not in range(1, 6):
                raise ValueError("ratings must be integers from 1 to 5")
            matrix_cell = matrix[(score_l, impact_score)]
            for field in (risk["risk"], likelihood["justification"], impact["justification"], risk["evidence"], risk["proposed_focus"]):
                stripped = FIGURE.sub("", CITE.sub("", field))
                if NUMBER.search(stripped):
                    print(f"WARNING {risk_id}: typed figure in prose", file=sys.stderr)
            lines.extend([f"## {risk_id}: {render_text(risk['risk'], analytics)}", "", f"- **Likelihood:** {score_l} {likelihood_labels[score_l]} (base {base}{'; repeat-finding uplift +1' if uplift else ''}). {render_text(likelihood['justification'], analytics)}", f"- **Impact:** {impact_score} {impact_labels[impact_score]}. {render_text(impact['justification'], analytics)}", f"- **Score and band:** {matrix_cell['score']} {matrix_cell['band']}.", f"- **Evidence:** {render_text(risk['evidence'], analytics)}", f"- **Proposed focus:** {render_text(risk['proposed_focus'], analytics)}", ""])
            if uplift:
                audit = history_item(history, CITE.fullmatch(uplift["history"])[2])
                lines.extend([f"Repeat finding: {uplift['history']} — {audit['audit_title']} ({audit.get('overall_opinion', audit.get('report_opinion'))}), {audit['source_file']} p. {audit['pdf_page']}. {uplift['reason']}", ""])
            kept.append(risk)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            print(f"EXCLUDED {risk_id}: {exc}", file=sys.stderr)
    if pre_gate:
        decisions = decisions_file(comments_path, kept)
    lines.extend(["## Auditor decisions", "", "Record Approve, Reject, or Change and any requested change in `outputs/auditor-comments.md`.", "", "| Risk ID | Decision | Comment |", "| --- | --- | --- |"])
    for risk in kept:
        decision, comment = decisions.get(risk["id"], ("", ""))
        lines.append(f"| {risk['id']} | {decision} | {comment} |")
    (directory / "risk-register.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if pre_gate:
        snapshot_path.write_text(json.dumps({"risks": kept}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(kept), len(risks) - len(kept)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", type=Path, default=ROOT / "outputs")
    args = parser.parse_args()
    try:
        kept, excluded = build(args.outputs)
        print(f"Rendered {kept} risks; excluded {excluded}")
    except (KeyError, IndexError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Risk register failed: {exc}\n")


if __name__ == "__main__":
    main()
