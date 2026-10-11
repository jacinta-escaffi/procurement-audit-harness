import re

from common import json_file, main

RISK = re.compile(r"^## (R-[\w-]+):", re.M)
RULE = re.compile(r"\[rule:(CPR-[\w-]+)\]")
EVIDENCE = re.compile(r"\[(?:rule|metric|history):[^\]]+\]")


def decisions(text):
    result = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and re.fullmatch(r"R-[\w-]+", cells[0]):
            result[cells[0]] = (cells[1].lower(), cells[2])
    return result


def check(root):
    out = root / "outputs"
    rules = {item["rule_id"] for item in json_file(out / "rules.json")["rules"]}
    ratings = json_file(out / "risk-ratings.json")["risks"]
    plan = json_file(out / "audit-plan.json")
    register = (out / "risk-register.md").read_text(encoding="utf-8")
    matrix = (out / "risk-control-matrix.md").read_text(encoding="utf-8")
    program = (out / "audit-program.md").read_text(encoding="utf-8")
    audit = decisions((out / "auditor-comments.md").read_text(encoding="utf-8"))
    issues = []
    rated = {r["id"]: r for r in ratings}
    register_ids = set(RISK.findall(register))
    planned = {r["risk_id"]: r for r in plan["risks"]}
    approved = {risk_id for risk_id, (decision, _) in audit.items() if decision == "approve"}
    if set(planned) != approved:
        issues.append(f"plan scope differs from approved risks: {sorted(set(planned) ^ approved)}")
    cited = set()
    for risk in ratings:
        cited |= set(RULE.findall(str(risk)))
    for risk_id in approved:
        if risk_id not in rated or risk_id not in register_ids or risk_id not in planned:
            issues.append(f"approved risk missing from ratings/register/plan: {risk_id}")
            continue
        item = planned[risk_id]
        controls = {c["id"]: c for c in item["controls"]}
        covered = set()
        if not controls or not item["tests"]:
            issues.append(f"{risk_id}: missing control or test")
        for control in controls.values():
            cited.update(control.get("rules", []))
            cited |= set(RULE.findall(control.get("text", "")))
            if not control.get("rules"):
                issues.append(f"{risk_id}: uncited control {control['id']}")
            if risk_id not in matrix or control["id"] not in matrix:
                issues.append(f"{risk_id}: control absent from matrix {control['id']}")
        for test in item["tests"]:
            covered.update(test["control_ids"])
            cited |= set(RULE.findall(" ".join(test.get("citations", [])) + test.get("step", "")))
            if risk_id not in program or test["id"] not in program:
                issues.append(f"{risk_id}: test absent from program {test['id']}")
            if not test.get("citations"):
                issues.append(f"{risk_id}: uncited test {test['id']}")
        if set(controls) - covered:
            issues.append(f"{risk_id}: control without test {sorted(set(controls) - covered)}")
        if risk_id not in matrix or risk_id not in program:
            issues.append(f"{risk_id}: missing from matrix or program")
    omitted = {}
    for item in plan.get("not_tested_rules", []):
        if item.get("reason", "").strip():
            omitted[item["rule_id"]] = item["reason"]
    if rules - cited - omitted.keys():
        issues.append(f"unaccounted rules: {sorted(rules - cited - omitted.keys())}")
    for risk_id, risk in rated.items():
        section = register.split(f"## {risk_id}:", 1)[-1].split("\n## ", 1)[0] if risk_id in register_ids else ""
        if re.search(r"Score and band:\*\*\s+\d+\s+High\b", section):
            if risk_id not in approved and not audit.get(risk_id, ("", ""))[1].strip():
                issues.append(f"High risk excluded without reason: {risk_id}")
    challenge_path = out / "challenges.md"
    if not challenge_path.exists():
        issues.append("challenges.md missing")
    else:
        lines = challenge_path.read_text(encoding="utf-8").splitlines()
        rows = 0
        for number, line in enumerate(lines, 1):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 9 and re.fullmatch(r"\d+", cells[0]):
                rows += 1
                if not EVIDENCE.search(cells[4]):
                    issues.append(f"challenges.md:{number}: challenge lacks evidence citation")
                if cells[7].lower() == "open":
                    issues.append(f"challenges.md:{number}: challenge open")
                elif cells[7].lower() not in {"resolved", "declined", "for the auditor"}:
                    issues.append(f"challenges.md:{number}: invalid status")
        if not rows and "no challenge" not in "\n".join(lines).lower():
            issues.append("challenges.md has no rows or no-challenge record")
    return issues


if __name__ == "__main__":
    raise SystemExit(main(check))
