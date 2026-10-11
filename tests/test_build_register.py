import importlib.util
import json
from pathlib import Path


BUILDER = Path(__file__).resolve().parents[1] / ".claude/skills/risk-assessment/build_register.py"
spec = importlib.util.spec_from_file_location("build_register", BUILDER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_one_valid_and_one_broken_risk(tmp_path, capsys):
    source = Path(__file__).resolve().parents[1] / "outputs"
    for name in ("rules.json", "analytics.json", "history.json"):
        (tmp_path / name).write_bytes((source / name).read_bytes())
    risks = [
        {"id": "R-01", "risk": "Split purchase indicators", "likelihood": {"base": 2, "justification": "{{$.cleaning.included.rows}} analysed rows [metric:$.cleaning.included.rows]."}, "impact": {"score": 2, "justification": "The rule requires review [rule:CPR-001]."}, "evidence": "[metric:$.cleaning.included.rows]", "proposed_focus": "Review records."},
        {"id": "R-02", "risk": "Broken citation", "likelihood": {"base": 2, "justification": "No evidence [metric:$.missing.count]."}, "impact": {"score": 2, "justification": "Rule [rule:CPR-001]."}, "evidence": "", "proposed_focus": "Review."},
    ]
    (tmp_path / "risk-ratings.json").write_text(json.dumps({"risks": risks}), encoding="utf-8")
    assert module.build(tmp_path) == (1, 1)
    register = (tmp_path / "risk-register.md").read_text(encoding="utf-8")
    assert "R-01" in register and "R-02" not in register
    assert "8526" in register and "{{" not in register
    assert register.rstrip().endswith("| R-01 |  |  |")
    assert "EXCLUDED R-02" in capsys.readouterr().err
    risks[1]["likelihood"]["justification"] = "[metric:$.cleaning.included.rows]"
    (tmp_path / "risk-ratings.json").write_text(json.dumps({"risks": risks}), encoding="utf-8")
    assert module.build(tmp_path) == (2, 0)
    assert "R-02" in (tmp_path / "auditor-comments.md").read_text(encoding="utf-8")


def test_unrequested_revision_fails(tmp_path):
    source = Path(__file__).resolve().parents[1] / "outputs"
    for name in ("rules.json", "analytics.json", "history.json"):
        (tmp_path / name).write_bytes((source / name).read_bytes())
    risk = {"id": "R-01", "risk": "Indicator", "likelihood": {"base": 2, "justification": "[metric:$.cleaning.included.rows]"}, "impact": {"score": 2, "justification": "[rule:CPR-001]"}, "evidence": "", "proposed_focus": "Review."}
    (tmp_path / "risk-ratings.json").write_text(json.dumps({"risks": [risk]}), encoding="utf-8")
    module.build(tmp_path)
    comments = tmp_path / "auditor-comments.md"
    comments.write_text(comments.read_text(encoding="utf-8").replace("| R-01 |  |  |", "| R-01 | Approve |  |"), encoding="utf-8")
    risk["impact"]["score"] = 3
    (tmp_path / "risk-ratings.json").write_text(json.dumps({"risks": [risk], "revision": {"changes": []}}), encoding="utf-8")
    try:
        module.build(tmp_path)
    except ValueError as exc:
        assert "unrequested change" in str(exc)
    else:
        raise AssertionError("unrequested revision was accepted")
