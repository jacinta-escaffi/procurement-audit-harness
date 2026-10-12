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
    o.submit_gate1(run.id, {r["id"]: {"decision": "Approve", "comment": ""} for r in o.risks(run.id)})
    assert wait(run, "gate1") == "done"
    o.start_stage(run.id, "team", "agent")
    return run, wait(run, "team")


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
    assert wait(run, "risk") == "failed"
    assert "stopped by the user" in run.stage("risk")["error"]
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
    assert wait(run, "risk") == "failed"
    assert "stopped by the user" in run.stage("risk")["error"]
    assert calls["n"] < 10
