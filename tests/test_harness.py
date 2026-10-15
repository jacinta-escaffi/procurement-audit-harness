"""End-to-end tests for the Part B harness, using a scripted fake LLM (no API calls).

Run:  python -m pytest tests/test_harness.py -q
"""
import json
import re
import shutil
import time
from pathlib import Path

import pytest

from harness import config
from harness.llm import LLMResponse
from harness.orchestrator import Orchestrator, StageError
from harness.permissions import POLICIES
from harness.store import RunStore
from harness.tools import Toolbox, ToolError

ROOT = Path(__file__).resolve().parents[1]


class ScriptedLLM:
    """Plays back agent turns. scripts[agent] is either a list of turns or a callable(user_text) -> turn.
    A turn is a list of steps; a step is a list of (tool_name, input) calls, or a final text string."""

    def __init__(self, scripts):
        self.scripts = {k: (v if callable(v) else list(v)) for k, v in scripts.items()}
        self.position = {}

    def create(self, system, tools, messages, max_tokens=None):
        agent = re.search(r"You are `([^`]+)`", system).group(1)
        last = messages[-1]
        if isinstance(last["content"], str):  # a new turn starts
            source = self.scripts[agent]
            turn = source(last["content"]) if callable(source) else source.pop(0)
            self.position[agent] = [turn, 0]
        turn, i = self.position[agent]
        step = turn[i]
        self.position[agent][1] += 1
        usage = {"input_tokens": 100, "output_tokens": 10}
        if isinstance(step, str):
            return LLMResponse([{"type": "text", "text": step}], "end_turn", usage)
        blocks = [{"type": "tool_use", "id": f"t{time.time_ns()}{n}", "name": name, "input": args}
                  for n, (name, args) in enumerate(step)]
        return LLMResponse(blocks, "tool_use", usage)


def wait(run, stage, statuses=("done", "failed", "waiting", "blocked"), timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        if run.stage(stage)["status"] in statuses and not run.state["busy"]:
            return run.stage(stage)["status"]
        time.sleep(0.3)
    raise TimeoutError(f"{stage} still {run.stage(stage)['status']}")


@pytest.fixture
def orch(tmp_path):
    def make(scripts=None):
        llm = ScriptedLLM(scripts or {})
        return Orchestrator(RunStore(tmp_path / "runs"), llm_factory=lambda: llm)
    return make


def replay_to_gate(o):
    run = o.store.create()
    for stage in ("extract_rules", "analysis", "risk"):
        o.start_stage(run.id, stage, "replay")
        assert wait(run, stage) in ("done", "waiting"), run.stage(stage)
    assert run.stage("gate1")["status"] == "waiting"
    return run


def test_replay_end_to_end_with_rejection_and_signoff(orch):
    o = orch()
    run = replay_to_gate(o)
    ids = [r["id"] for r in o.risks(run.id)]
    assert ids == ["R-01", "R-02", "R-03", "R-04", "R-05"]
    with pytest.raises(StageError):  # a Change needs a comment
        o.submit_gate1(run.id, {i: {"decision": "Change"} for i in ids})
    o.submit_gate1(run.id, {i: {"decision": "Approve", "comment": ""} for i in ids})
    assert wait(run, "gate1") == "done", run.stage("gate1")
    assert "Gate passed: 5 approved risks" in run.stage("gate1")["report"]
    o.start_stage(run.id, "team", "replay")
    assert wait(run, "team") == "done", run.stage("team")
    assert run.stage("signoff")["status"] == "waiting"
    o.signoff(run.id, "Sign off", "Test Auditor", "fine")
    review = (run.workspace / "outputs/review.md").read_text()
    assert "READY FOR SIGN-OFF" in review and "Name: Test Auditor" in review
    assert run.stage("signoff")["status"] == "done"


def test_permissions_are_enforced(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(ROOT / "data", ws / "data")
    (ws / "outputs").mkdir()
    challenger = Toolbox(ws, "challenger", POLICIES["challenger"])
    with pytest.raises(ToolError, match="may not read"):
        challenger.execute("read_file", {"path": "data/contracts.csv"})
    with pytest.raises(ToolError, match="may not write"):
        challenger.execute("write_file", {"path": "outputs/review.md", "content": "x"})
    assert "wrote" in challenger.execute("write_file", {"path": "outputs/challenges.md", "content": "x"})
    planner = Toolbox(ws, "planner", POLICIES["planner"])
    with pytest.raises(ToolError, match="may run only"):
        planner.execute("run_command", {"command": "python scripts/checks/run_checks.py"})
    with pytest.raises(ToolError, match="pipes"):
        planner.execute("run_command", {"command": "python .claude/skills/audit-program/build_pack.py | cat"})
    with pytest.raises(ToolError, match="only"):
        planner.execute("run_command", {"command": "rm -rf outputs"})
    with pytest.raises(ToolError, match="escapes"):
        planner.execute("read_file", {"path": "../../etc/passwd"})
    with pytest.raises(ToolError, match="inline Python"):
        planner.execute("run_python", {"code": "print(1)"})


def test_change_round_runs_risk_assessor_then_review(orch):
    # The fake risk-assessor edits R-02's impact justification, declares it, and rebuilds the register.
    ratings_src = json.loads((config.BASELINE_OUTPUTS / "risk-ratings.gate.json").read_text())
    r02 = next(r for r in ratings_src["risks"] if r["id"] == "R-02")
    revised = json.loads(json.dumps(ratings_src))
    for r in revised["risks"]:
        if r["id"] == "R-02":
            r["impact"]["justification"] = r02["impact"]["justification"] + " The auditor asked for this to be restated."
    revised["revision"] = {"changes": [{"id": "R-02", "fields": ["impact.justification"], "reason": "Auditor requested restatement"}]}
    scripts = {"risk-assessor": [[
        [("read_file", {"path": "outputs/auditor-comments.md"})],
        [("write_file", {"path": "outputs/risk-ratings.json", "content": json.dumps(revised, indent=2)})],
        [("run_command", {"command": "python .claude/skills/risk-assessment/build_register.py"})],
        "Revised R-02 as requested.",
    ]]}
    o = orch(scripts)
    run = replay_to_gate(o)
    ids = [r["id"] for r in o.risks(run.id)]
    decisions = {i: {"decision": "Approve", "comment": ""} for i in ids}
    decisions["R-02"] = {"decision": "Change", "comment": "Restate the impact justification"}
    o.submit_gate1(run.id, decisions)
    assert wait(run, "gate1") == "waiting", run.stage("gate1")
    assert run.state["gate1"]["awaiting_review"] == ["R-02"]
    assert "## Revision after auditor gate" in (run.workspace / "outputs/risk-register.md").read_text()
    with pytest.raises(StageError, match="review round"):
        o.submit_gate1(run.id, decisions)
    decisions["R-02"] = {"decision": "Approve", "comment": "Accepted."}
    o.submit_gate1(run.id, decisions)
    assert wait(run, "gate1") == "done", run.stage("gate1")
    comments = (run.workspace / "outputs/auditor-comments.md").read_text()
    assert "| R-02 | Approve | Change: Restate the impact justification" in comments
    # Writing outside its own files would have been refused:
    tb = Toolbox(run.workspace, "risk-assessor", POLICIES["risk-assessor"])
    with pytest.raises(ToolError):
        tb.execute("write_file", {"path": "outputs/auditor-comments.md", "content": "forged"})


def run_team(orch, challenge_text=None):
    plan = (config.BASELINE_OUTPUTS / "audit-plan.json").read_text()
    build = ("run_command", {"command": "python .claude/skills/audit-program/build_pack.py"})
    checks = ("run_command", {"command": "python scripts/checks/run_checks.py"})
    seen = {"planner": set(), "challenger": set(), "qa-reviewer": set()}
    version = {"n": 0}

    def remember(agent, text):
        for token in ("PACK READY", "CHALLENGE #1", "RESPONSE #1", "CHALLENGER DONE", "PLANNER DONE", "QA v1: PASS", "QA v2: PASS"):
            if token in text:
                seen[agent].add(token)

    def ready(to):
        version["n"] += 1
        return [("send_message", {"to": "challenger", "message": f"PACK READY v{version['n']}"}),
                ("send_message", {"to": "qa-reviewer", "message": f"PACK READY v{version['n']}"})]

    def planner(text):
        remember("planner", text)
        if "Start building" in text:
            return [[("run_command", {"command": "python .claude/skills/audit-program/build_pack.py --gate"})],
                    [("write_file", {"path": "outputs/audit-plan.json", "content": plan})], [build], ready(None), "v1 out."]
        steps = []
        if "CHALLENGE #1" in text:
            steps += [[("send_message", {"to": "challenger", "message": "RESPONSE #1: accepted. Clarified T-04."})], [build], ready(None)]
        if f"QA v{version['n']}: PASS" in text and "CHALLENGER DONE" in seen["planner"]:
            steps += [[("send_message", {"to": "lead", "message": f"PLANNER DONE v{version['n']}"}),
                       ("send_message", {"to": "qa-reviewer", "message": f"PLANNER DONE v{version['n']}"})]]
        return steps + ["Waiting."]

    # A well-formed challenge log (Part A's); the bad-log case is covered by the blocking test below.
    challenge_log = [challenge_text or (config.BASELINE_OUTPUTS / "challenges.md").read_text()]

    def challenger(text):
        remember("challenger", text)
        if "RESPONSE #1" in text:
            return [[("write_file", {"path": "outputs/challenges.md", "content": challenge_log[0]})],
                    [("send_message", {"to": "lead", "message": "CHALLENGER DONE"}),
                     ("send_message", {"to": "qa-reviewer", "message": "CHALLENGER DONE"}),
                     ("send_message", {"to": "planner", "message": "CHALLENGER DONE"})], "Done."]
        if "PACK READY v1" in text:
            return [[("write_file", {"path": "outputs/challenges.md", "content": "| # | Status |\n| 1 | open |\n"})],
                    [("send_message", {"to": "planner", "message": "CHALLENGE #1 [test] R-04: [rule:CPR-116]; require full ledger"})],
                    "Round one sent."]
        return ["Nothing to do."]

    def qa(text):
        remember("qa-reviewer", text)
        if "PLANNER DONE" in seen["qa-reviewer"] and "CHALLENGER DONE" in seen["qa-reviewer"]:
            return [[checks], [("write_file", {"path": "outputs/review.md", "content":
                    "| Check | Result | Items |\n| --- | --- | --- |\n| quotes | PASS | — |\n\nVerdict: READY FOR SIGN-OFF\n\n"
                    "## Auditor sign-off\n\nDecision:\n\nName:\n\nDate:\n\nComments:\n"})],
                    [("send_message", {"to": "lead", "message": f"QA DONE v{version['n']}"})], "Review written."]
        if "PACK READY" in text:
            return [[checks], [("send_message", {"to": "planner", "message": f"QA v{version['n']}: PASS"})], "Passed."]
        return ["Waiting."]

    o = orch({"planner": planner, "challenger": challenger, "qa-reviewer": qa})
    run = replay_to_gate(o)
    run.state["mode"] = "agent"  # as in the web app: the team then starts on its own once gate 1 passes
    o.submit_gate1(run.id, {r["id"]: {"decision": "Approve", "comment": ""} for r in o.risks(run.id)})
    assert wait(run, "team", statuses=("done", "blocked", "failed")) in ("done", "blocked", "failed")
    assert run.stage("gate1")["status"] == "done"
    assert any("starting Build and challenge the pack automatically" in e.get("text", "") for e in run.events)
    return run, run.stage("team")["status"]


def test_agent_team_message_bus(orch):
    run, status = run_team(orch)
    assert status == "done", (run.stage("team"), [e for e in run.events if e["type"] in ("error", "warning", "message")][-12:])
    assert run.state["team"]["flags"] == {"planner": True, "challenger": True, "qa-reviewer": True}
    routes = [(e["from"], e["to"]) for e in run.events if e["type"] == "message"]
    assert ("challenger", "planner") in routes and ("qa-reviewer", "lead") in routes
    assert run.stage("team")["verdict"] == "READY FOR SIGN-OFF"
    assert (run.folder / "transcripts" / "planner.json").exists()


def test_harness_recheck_blocks_a_bad_pack_even_if_qa_says_done(orch):
    # The challenger writes an empty log; the fake qa-reviewer still claims success.
    run, status = run_team(orch, challenge_text="| # | Status |\n")
    assert status == "blocked"
    assert run.stage("team")["verdict"] == "BLOCKED"
    assert run.stage("signoff")["status"] == "locked"  # never reaches the auditor


def test_conversation_cache_breakpoint_does_not_mutate_transcript():
    from harness.llm import with_cache_breakpoint
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}]
    out = with_cache_breakpoint(msgs)
    assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in msgs[-1]["content"][-1]  # the saved transcript stays clean
    assert with_cache_breakpoint([{"role": "user", "content": "go"}])[0]["content"][0]["cache_control"]


def test_stop_button_halts_agents_before_next_model_call(orch):
    import threading
    calls = {"n": 0}
    gate = threading.Event()

    def assessor(text):
        return [[("list_files", {"path": "outputs"})]] * 50 + ["never reached"]

    o = orch({"risk-assessor": assessor})
    original = o._llm_factory

    class Slow:
        def __init__(self, inner): self.inner = inner
        def create(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 3:
                gate.set()
            time.sleep(0.05)
            return self.inner.create(*a, **k)

    o._llm_factory = lambda: Slow(original())
    run = o.store.create()
    for stage in ("extract_rules", "analysis"):
        o.start_stage(run.id, stage, "replay")
        wait(run, stage)
    o.start_stage(run.id, "risk", "agent")
    gate.wait(30)
    o.stop(run.id)
    assert wait(run, "risk", statuses=("stopped", "failed", "done")) == "stopped"
    assert calls["n"] < 10


def test_conversation_cache_breakpoint_does_not_mutate_transcript():
    from harness.llm import with_cache_breakpoint
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}]
    out = with_cache_breakpoint(msgs)
    assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in msgs[-1]["content"][-1]  # the saved transcript stays clean
    assert with_cache_breakpoint([{"role": "user", "content": "go"}])[0]["content"][0]["cache_control"]


def test_stop_halts_agent_before_next_model_call(orch):
    import threading
    calls = {"n": 0}
    third_call = threading.Event()
    o = orch({"risk-assessor": lambda text: [[("list_files", {"path": "outputs"})]] * 50 + ["never reached"]})
    inner_factory = o._llm_factory

    class Counting:
        def __init__(self, inner):
            self.inner = inner

        def create(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 3:
                third_call.set()
            time.sleep(0.05)
            return self.inner.create(*a, **k)

    o._llm_factory = lambda: Counting(inner_factory())
    run = o.store.create()
    for stage in ("extract_rules", "analysis"):
        o.start_stage(run.id, stage, "replay")
        wait(run, stage)
    o.start_stage(run.id, "risk", "agent")
    assert third_call.wait(30)
    o.stop(run.id)
    assert wait(run, "risk", statuses=("stopped", "failed", "done")) == "stopped"
    assert calls["n"] < 10


def test_stop_ends_a_replay_script(orch):
    o = orch()
    run = o.store.create()
    o.start_stage(run.id, "extract_rules", "replay")
    wait(run, "extract_rules")
    o.start_stage(run.id, "analysis", "replay")   # runs analyze_spend.py (tens of seconds)
    time.sleep(3)
    started = time.time()
    o.stop(run.id)
    assert wait(run, "analysis", statuses=("stopped", "failed", "done")) == "stopped"
    assert time.time() - started < 5
    assert run.stage("risk")["status"] == "locked"
    o.start_stage(run.id, "analysis", "replay")   # a stopped step can be run again
    assert wait(run, "analysis") == "done"


def test_json_query_reads_one_part_of_a_json_file(tmp_path):
    ws = tmp_path / "ws"
    (ws / "outputs").mkdir(parents=True)
    (ws / "data").mkdir()
    doc = {"tests": {"dup": {"overall": {"pairs": 5, "gbp": 12.5}, "top_20": [{"id": i} for i in range(20)]}}}
    (ws / "outputs/a.json").write_text(json.dumps(doc))
    (ws / "data/d.json").write_text("{}")
    tb = Toolbox(ws, "planner", POLICIES["planner"])
    assert json.loads(tb.execute("json_query", {"path": "outputs/a.json", "query": "$.tests.dup.overall"})) == {"pairs": 5, "gbp": 12.5}
    top = json.loads(tb.execute("json_query", {"path": "outputs/a.json", "depth": 1}))
    assert top["tests"].startswith("{...1 keys")
    assert json.loads(tb.execute("json_query", {"path": "outputs/a.json", "query": "tests.dup.top_20[2:4]"})) == [{"id": 2}, {"id": 3}]
    with pytest.raises(ToolError, match="no such path"):
        tb.execute("json_query", {"path": "outputs/a.json", "query": "$.tests.missing"})
    with pytest.raises(ToolError, match="may not read"):
        Toolbox(ws, "challenger", POLICIES["challenger"]).execute("json_query", {"path": "data/d.json"})


def test_pdftotext_page_options_are_not_mistaken_for_files(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(ROOT / "data", ws / "data")
    (ws / "outputs").mkdir()
    tb = Toolbox(ws, "findings-analyst", POLICIES["findings-analyst"])
    out = tb.execute("run_command", {"command": "pdftotext -enc UTF-8 -layout -f 1 -l 1 "
                                                "data/committee/internal-audit-plan-2025-28.pdf outputs/.scratch/findings-analyst/p1.txt"})
    assert out.startswith("exit code: 0") and (ws / "outputs/.scratch/findings-analyst/p1.txt").exists()
    with pytest.raises(ToolError, match="may not write"):
        tb.execute("run_command", {"command": "pdftotext -f 1 -l 1 data/committee/internal-audit-plan-2025-28.pdf outputs/x.txt"})


def test_history_verifier_requires_the_fields_the_register_builder_reads(tmp_path):
    import subprocess, sys
    history = json.loads((config.BASELINE_OUTPUTS / "history.json").read_text())
    for row in history["risk_method"]["likelihood_scale"]:
        row["likelihood"] = row.pop("rating")       # the shape that broke the 2026-10-14 run
    bad = tmp_path / "history.json"
    bad.write_text(json.dumps(history))
    proc = subprocess.run([sys.executable, "scripts/verify_history.py", str(bad), "data/committee"],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 1 and "under the key 'rating'" in proc.stderr
    # ...and the builder now tolerates that shape anyway
    sys.path.insert(0, str(ROOT / ".claude/skills/risk-assessment"))
    import build_register
    labels = build_register.scale_labels(history["risk_method"]["likelihood_scale"], "likelihood")
    assert sorted(labels) == [1, 2, 3, 4, 5]


def test_agents_are_told_the_date_and_to_write_big_files_in_parts():
    from harness.prompts import preamble
    text = preamble("extract-rules")
    assert "current date and time is" in text and "in parts from the start" in text


def test_per_agent_model_and_output_limit(monkeypatch):
    import importlib
    monkeypatch.setenv("HARNESS_MODEL_QA_REVIEWER", "claude-haiku-5-5")
    monkeypatch.setenv("HARNESS_MAX_TOKENS_PLANNER", "48000")
    assert config.model_for("qa-reviewer") == "claude-haiku-5-5"
    assert config.model_for("planner") == config.MODEL
    assert config.max_tokens_for("planner") == 48000
    assert config.max_tokens_for("extract-rules") == 64000
    assert config.max_tokens_for("challenger") == config.MAX_TOKENS


def test_output_limit_falls_back_when_the_model_rejects_it(monkeypatch):
    from types import SimpleNamespace
    from harness.llm import AnthropicLLM
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test")
    llm = AnthropicLLM(max_tokens=64000)
    llm._bad_request = ValueError
    seen = []

    def fake_call(limit, *rest):
        seen.append(limit)
        if limit > config.MAX_TOKENS:
            raise ValueError("max_tokens: 64000 > the maximum allowed for this model")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    llm._call = fake_call
    out = llm.create("system", [], [{"role": "user", "content": "hi"}])
    assert out.content[0]["text"] == "ok" and seen == [64000, config.MAX_TOKENS]
    llm.create("system", [], [{"role": "user", "content": "again"}])
    assert seen[-1] == config.MAX_TOKENS  # remembered


def test_json_query_wildcard_and_private_scratch(tmp_path):
    ws = tmp_path / "ws"
    (ws / "outputs/.scratch/extract-rules").mkdir(parents=True)
    (ws / "outputs/.scratch/extract-rules/plain.txt").write_text("rulebook text")
    plan = {"risks": [{"id": "R-01", "tests": [{"id": "T-1", "sample": {"count": 5}}, {"id": "T-2", "sample": {"count": 7}}]},
                      {"id": "R-02", "tests": [{"id": "T-3", "sample": {"count": 9}}]}]}
    (ws / "outputs/audit-plan.json").write_text(json.dumps(plan))
    tb = Toolbox(ws, "challenger", POLICIES["challenger"])
    got = json.loads(tb.execute("json_query", {"path": "outputs/audit-plan.json", "query": "$.risks[*].tests[*].sample.count"}))
    assert got == [[5, 7], [9]]
    with pytest.raises(ToolError, match="may not read"):
        tb.execute("read_file", {"path": "outputs/.scratch/extract-rules/plain.txt"})
    assert "plain.txt" not in tb.execute("list_files", {"path": "outputs"})
    assert "(no matches)" in tb.execute("search_files", {"pattern": "rulebook", "path": "outputs"})
    own = Toolbox(ws, "extract-rules", POLICIES["extract-rules"])
    assert "rulebook text" in own.execute("read_file", {"path": "outputs/.scratch/extract-rules/plain.txt"})


def test_preamble_steers_away_from_wasted_calls():
    from harness.prompts import preamble
    assert "`python -c`" in preamble("challenger")
    assert "still open are expected" in preamble("qa-reviewer")
    assert "still open are expected" not in preamble("planner")


def test_finished_agent_step_starts_the_next_agent_step(orch):
    rules = (config.BASELINE_OUTPUTS / "rules.json").read_text()
    scripts = {"extract-rules": [[[("write_file", {"path": "outputs/rules.json", "content": rules})], "Rules written."]],
               "spend-analyst": [["Nothing done."]], "findings-analyst": [["Nothing done."]]}
    o = orch(scripts)
    run = o.store.create()
    o.start_stage(run.id, "extract_rules", "agent")
    assert wait(run, "analysis", statuses=("done", "failed", "stopped")) == "failed"  # the fake analysts wrote nothing
    assert run.stage("extract_rules")["status"] == "done"
    assert run.stage("analysis")["mode"] == "agent"       # started by the harness, not by a click
    assert run.stage("risk")["status"] == "locked"         # a failed step does not hand over


def test_replay_mode_is_not_offered_by_the_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from harness import app as app_module
    monkeypatch.setattr(app_module, "orch", Orchestrator(RunStore(tmp_path / "runs"), llm_factory=lambda: ScriptedLLM({})))
    monkeypatch.setattr(config, "ACCESS_CODE", "")
    client = TestClient(app_module.app)
    run_id = client.post("/api/runs", json={}).json()["id"]
    r = client.post(f"/api/runs/{run_id}/stages/extract_rules", json={"mode": "replay"})
    assert r.status_code == 400 and "agent" in r.json()["detail"]
