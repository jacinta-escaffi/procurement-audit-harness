import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from build_pack import ROOT, build, gate


class PackFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        (self.out / "analytics-full").mkdir()
        self.write("risk-register.md", "# Register\n\n## Revision after auditor gate\n\n## R-01: Indicator one\n\n## R-02: Indicator two\n")
        self.write("auditor-comments.md", "| Risk ID | Decision | Comment |\n| --- | --- | --- |\n| R-01 | Approve | Review |\n| R-02 | Approve | Review |\n")
        risks = [{"id": f"R-0{i}", "risk": f"Potential indicator {i}", "likelihood": {"base": 2, "justification": f"[rule:CPR-00{i}]"}, "impact": {"score": 2, "justification": f"[rule:CPR-00{i}]"}, "evidence": f"[rule:CPR-00{i}]", "proposed_focus": "Investigate"} for i in (1, 2)]
        self.json("risk-ratings.json", {"risks": risks, "revision": {"changes": [{"id": "R-01", "fields": ["impact.score"], "reason": "reassessed"}]}})
        self.json("rules.json", {"rules": [{"rule_id": f"CPR-00{i}"} for i in (1, 2, 3)]})
        self.json("analytics.json", {"tests": {"flag": {"count": 2}}})
        self.csv("analytics-full/spend-clean.csv", [{"row_id": "row-1", "supplier_normalized": "ONE", "net_amount": "100"}, {"row_id": "row-2", "supplier_normalized": "TWO", "net_amount": "200"}])
        self.csv("analytics-full/flags.csv", [{"row_id": "row-1", "severity": "2"}, {"row_id": "row-2", "severity": "1"}])
        planned = []
        for i in (1, 2):
            planned.append({"risk_id": f"R-0{i}", "controls": [{"id": f"C-0{i}", "text": "Expected approval", "rules": [f"CPR-00{i}"]}], "tests": [{"id": f"T-0{i}", "control_ids": [f"C-0{i}"], "step": "Inspect approval evidence", "citations": [f"[rule:CPR-00{i}]"], "sample": {"file": "flags.csv", "filter": {}, "sort": ["-severity"], "count": 1}, "pbc": ["Approval records"]}]})
        self.plan = {"memo": "Review {{$.tests.flag.count}} indicators across {{$.pack.risks_in_scope}} risks.", "risks": planned, "not_tested_rules": [{"rule_id": "CPR-003", "reason": "Outside approved scope"}]}
        self.json("audit-plan.json", self.plan)

    def write(self, name, value):
        (self.out / name).write_text(value, encoding="utf-8")

    def json(self, name, value):
        self.write(name, json.dumps(value))

    def csv(self, name, rows):
        with (self.out / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def test_two_risk_pack(self):
        self.assertEqual((2, 2), build(self.out))
        self.assertIn("Review 2 indicators across 2 risks", (self.out / "planning-memo.md").read_text())
        self.assertIn("## PBC list", (self.out / "audit-program.md").read_text())
        with (self.out / "samples/T-01.csv").open(newline="", encoding="utf-8") as stream:
            self.assertEqual(2, len(list(csv.DictReader(stream))))

    def test_missing_risk_fails(self):
        plan = copy.deepcopy(self.plan)
        plan["risks"].pop()
        self.json("audit-plan.json", plan)
        with self.assertRaisesRegex(ValueError, "missing"):
            build(self.out)

    def test_uncited_control_fails(self):
        plan = copy.deepcopy(self.plan)
        plan["risks"][0]["controls"][0]["rules"] = []
        self.json("audit-plan.json", plan)
        with self.assertRaisesRegex(ValueError, "uncited control"):
            build(self.out)

    def test_unaccounted_rule_fails(self):
        plan = copy.deepcopy(self.plan)
        plan["not_tested_rules"] = []
        self.json("audit-plan.json", plan)
        with self.assertRaisesRegex(ValueError, "unaccounted rule"):
            build(self.out)

    def test_undecided_gate_fails(self):
        self.write("auditor-comments.md", "| R-01 | Change | Review |\n| R-02 | Approve | Review |\n")
        with self.assertRaisesRegex(ValueError, "undecided"):
            gate(self.out)


if __name__ == "__main__":
    unittest.main()
