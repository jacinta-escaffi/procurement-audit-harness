import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_history.py"
spec = importlib.util.spec_from_file_location("verify_history", SCRIPT)
verify_history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_history)


SOURCE = "committee-paper.pdf"
PAGES = {
    SOURCE: {
        "plain": ["Audit Alpha Reasonable Assurance", "Risk score bands 1 to 3"],
        "layout": ["Audit Alpha     Reasonable Assurance", "Risk score bands     1 to 3"],
    }
}


def citation(quote="Reasonable Assurance", page=1, source=SOURCE, **fields):
    return {"source_file": source, "pdf_page": page, "quote": quote, **fields}


def fixture():
    method = {key: [] for key in verify_history.RISK_METHOD_FIELDS}
    method["bands"] = [citation("Risk score bands 1 to 3", page=2, band="Low")]
    return {
        "schema_version": 1,
        "sources": [{
            "source_file": SOURCE,
            "pdf_pages": 2,
            "plain_text_path": "outputs/committee-text/committee-paper.plain.txt",
            "layout_text_path": "outputs/committee-text/committee-paper.layout.txt",
        }],
        "audit_coverage": [citation(audit_title="Audit Alpha", opinion="Reasonable Assurance")],
        "follow_ups": [],
        "agreed_actions": [],
        "risk_themes": [],
        "risk_method": method,
        "gaps": [citation("Audit Alpha", gap="No action descriptions")],
        "discrepancies": [{
            "issue": "Two source statements conflict",
            "evidence": [citation("Reasonable Assurance"), citation("Risk score bands 1 to 3", page=2)],
        }],
        "verification": {"status": "pending"},
    }


class VerifyHistoryFixtureTests(unittest.TestCase):
    def test_valid_fixture(self):
        self.assertEqual(verify_history.validate(fixture(), PAGES), [])

    def test_planted_errors_are_reported(self):
        data = deepcopy(fixture())
        data["audit_coverage"][0]["quote"] = "Invented opinion"
        data["risk_method"]["bands"][0]["pdf_page"] = 3
        data["gaps"][0].pop("source_file")
        data["discrepancies"][0]["evidence"] = [citation()]
        errors = verify_history.validate(data, PAGES)
        joined = "\n".join(errors)
        for expected in (
            "not found on committee-paper.pdf PDF page 1",
            "expected 1-2",
            "missing citation fields source_file",
            "expected at least two cited items",
        ):
            self.assertIn(expected, joined)

    def test_layout_text_can_verify_a_table_cell(self):
        data = fixture()
        data["audit_coverage"][0]["quote"] = "Audit Alpha     Reasonable Assurance"
        self.assertEqual(verify_history.validate(data, PAGES), [])


if __name__ == "__main__":
    unittest.main()
