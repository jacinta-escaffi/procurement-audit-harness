import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_quotes import check as quotes
from check_numbers import check as numbers
from check_trace import check as trace
from check_samples import check as samples


class ChecksFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("outputs/samples", "outputs/analytics-full", "data/spend", ".claude/skills/extract-rules"):
            (self.root / folder).mkdir(parents=True)
        self.write(".claude/skills/extract-rules/verify_quotes.py", "def pages_from_pdf(path):\n    return ['source quote']\ndef validate(data, pages):\n    return []\ndef normalized(value):\n    return ' '.join(value.split())\n")
        self.write("data/contract-rules.pdf", "fixture")
        self.json("outputs/rules.json", {"rules": [{"rule_id": "CPR-001", "verbatim_quote": "source quote", "threshold": 500}]})
        self.json("outputs/analytics.json", {"tests": {"flag": {"count": 123}}})
        self.json("outputs/pack-figures.json", {"pack": {"risks_in_scope": 1, "samples": {"T-01": {"transactions": 1}}}})
        self.json("outputs/risk-ratings.json", {"risks": [{"id": "R-01", "risk": "Potential indicator", "likelihood": {"base": 2, "justification": "[rule:CPR-001]"}, "impact": {"score": 2, "justification": "[rule:CPR-001]"}, "evidence": "[rule:CPR-001]", "proposed_focus": "Investigate"}]})
        self.json("outputs/audit-plan.json", {"risks": [{"risk_id": "R-01", "controls": [{"id": "C-01", "text": "Approval", "rules": ["CPR-001"]}], "tests": [{"id": "T-01", "control_ids": ["C-01"], "step": "Inspect records", "citations": ["[rule:CPR-001]"]}]}], "not_tested_rules": []})
        self.write("outputs/risk-register.md", "## R-01: Potential indicator\n- **Score and band:** 4 Moderate.\n- Quote: “source quote” [rule:CPR-001]\n")
        self.write("outputs/auditor-comments.md", "| R-01 | Approve | Review |\n")
        self.write("outputs/planning-memo.md", "Potential 123 indicators.\n")
        self.write("outputs/risk-control-matrix.md", "| R-01 | C-01 Approval [rule:CPR-001] | T-01 Inspect records |\n")
        self.write("outputs/audit-program.md", "## R-01: Potential indicator\nT-01 Inspect records.\n")
        self.write("outputs/challenges.md", "No challenge justified in either round.\n")
        self.csv("outputs/analytics-full/spend-clean.csv", [{"row_id": "spend.xlsx|Data to publish|2", "source_file": "spend.xlsx", "source_sheet_row": "2", "supplier_normalized": "ACME", "Supplier name": "Acme", "net_amount": "100", "Net amount": "100"}])
        self.csv("outputs/samples/T-01.csv", [{"row_id": "spend.xlsx|Data to publish|2", "supplier_normalized": "ACME", "Supplier name": "Acme", "net_amount": "100"}])
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data to publish"
        sheet.append(["Supplier name", "Net amount"])
        sheet.append(["Acme", 100])
        workbook.save(self.root / "data/spend/spend.xlsx")

    def write(self, name, content):
        (self.root / name).write_text(content, encoding="utf-8")

    def json(self, name, content):
        self.write(name, json.dumps(content))

    def csv(self, name, rows):
        with (self.root / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def test_all_checks_pass_fixture(self):
        for check in (quotes, numbers, trace, samples):
            self.assertEqual([], check(self.root), check.__module__)

    def test_bad_figure_fails(self):
        self.write("outputs/planning-memo.md", "Potential 999 indicators.\n")
        self.assertTrue(any("999" in item for item in numbers(self.root)))

    def test_missing_sample_row_fails(self):
        self.csv("outputs/samples/T-01.csv", [{"row_id": "missing.xlsx|Data to publish|2", "supplier_normalized": "ACME", "Supplier name": "Acme", "net_amount": "100"}])
        self.assertTrue(any("missing from analytics-full/spend-clean" in item for item in samples(self.root)))

    def test_bad_quote_fails(self):
        self.write("outputs/risk-register.md", "## R-01: Potential indicator\n“wrong quote” [rule:CPR-001]\n")
        self.assertTrue(any("quote excerpt differs" in item for item in quotes(self.root)))


if __name__ == "__main__":
    unittest.main()
