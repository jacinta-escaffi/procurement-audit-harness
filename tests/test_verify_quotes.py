import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "extract-rules" / "verify_quotes.py"
spec = importlib.util.spec_from_file_location("verify_quotes", SCRIPT)
verify_quotes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_quotes)


def bound(printed=None, gbp=None, inclusive=None, parameter=None):
    return {
        "printed": printed,
        "gbp": gbp,
        "inclusive": inclusive,
        "parameter": parameter,
    }


def fixture():
    return {
        "parameters": {key: None for key in verify_quotes.PARAMETERS},
        "rules": [{
            "rule_id": "CPR-001",
            "clause": "1.1",
            "page": 1,
            "type": "procedure",
            "scope": "contracts",
            "thresholds": [{
                "basis": "contract_value",
                "low": bound("Above £1,000", 1000, False),
                "high": bound(),
            }],
            "required_action": "Record the contract",
            "approver": None,
            "evidence_source": "contract file",
            "verbatim_quote": "Record the contract",
        }],
        "excluded": [],
        "notes": [],
    }


class VerifierFixtureTests(unittest.TestCase):
    def setUp(self):
        self.original_appendices = verify_quotes.APPENDIX_CLAUSES
        verify_quotes.APPENDIX_CLAUSES = set()

    def tearDown(self):
        verify_quotes.APPENDIX_CLAUSES = self.original_appendices

    def test_valid_fixture(self):
        self.assertEqual(verify_quotes.validate(fixture(), ["1.1 Record the contract"]), [])

    def test_deliberate_errors_are_reported(self):
        data = fixture()
        data["parameters"]["goods_services_threshold"] = 100000
        rule = data["rules"][0]
        rule["rule_id"] = "CPR-003"
        rule["page"] = 2
        rule["thresholds"][0]["low"]["parameter"] = "invented_threshold"
        rule["thresholds"][0]["low"]["gbp"] = None
        rule["verbatim_quote"] = "Fabricated quotation"
        errors = verify_quotes.validate(data, ["1.1 Record the contract", "second page"])
        joined = "\n".join(errors)
        for expected in (
            "value must remain null",
            "expected CPR-001",
            "undeclared parameter",
            "not found on PDF page 2",
        ):
            self.assertIn(expected, joined)

    def test_missing_clause_is_reported(self):
        data = fixture()
        errors = verify_quotes.validate(data, ["1.1 Record the contract\n1.2 More text"])
        self.assertIn("coverage: 1.2 missing from rules or excluded", errors)

    def test_printed_million_and_penny_boundaries(self):
        self.assertEqual(verify_quotes.printed_gbp("less than £2.5million"), 2_500_000)
        data = fixture()
        data["rules"][0]["thresholds"][0]["low"] = bound("more than £24,999.99", 24999, False)
        errors = verify_quotes.validate(data, ["1.1 Record the contract"])
        self.assertTrue(any("printed penny amount" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
