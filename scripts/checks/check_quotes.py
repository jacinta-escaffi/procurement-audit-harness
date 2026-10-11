import importlib.util
import re
import subprocess

from common import json_file, main


def check(root):
    path = root / ".claude/skills/extract-rules/verify_quotes.py"
    spec = importlib.util.spec_from_file_location("extract_verify_quotes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rules_doc = json_file(root / "outputs/rules.json")
    try:
        pages = module.pages_from_pdf(root / "data/contract-rules.pdf")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        return [f"extract-rules verifier could not read PDF: {exc}"]
    issues = [f"extract-rules: {item}" for item in module.validate(rules_doc, pages)]
    rules = {rule["rule_id"]: rule["verbatim_quote"] for rule in rules_doc["rules"]}
    register = (root / "outputs/risk-register.md").read_text(encoding="utf-8")
    for number, line in enumerate(register.splitlines(), 1):
        for match in re.finditer(r"[“\"]([^”\"]+)[”\"]\s*\[rule:(CPR-\d+)\]", line):
            excerpt, rule_id = match.groups()
            if rule_id not in rules:
                issues.append(f"risk-register.md:{number}: unknown rule {rule_id}")
            elif module.normalized(excerpt) not in module.normalized(rules[rule_id]):
                issues.append(f"risk-register.md:{number}: quote excerpt differs from {rule_id}")
    return issues


if __name__ == "__main__":
    raise SystemExit(main(check))
