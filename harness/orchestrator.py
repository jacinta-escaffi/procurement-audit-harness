"""The orchestrator: the "lead" of slide 13's outer box. It decides which agent runs when,
pauses at the two human gates, runs the agent team's message bus, and verifies each
stage's output with the project's own scripts before the next stage can start.

Stages (each one produces files the next one reads):
  extract_rules -> analysis (spend-analyst || findings-analyst) -> risk -> gate1 (human)
  -> team (planner, challenger, qa-reviewer) -> signoff (human)

Every stage runs in one of two modes:
  agent  : the LLM agents do the work through the agent loop (the real workflow)
  replay : the Part A outputs are restored and the deterministic scripts re-run (no API cost;
           useful for demos and for jumping straight to a human gate)
"""
import json
import re
import shutil
import subprocess
import sys
import threading
from datetime import date

from . import config
from .agent_loop import Agent
from .permissions import POLICIES, TEAM
from .prompts import system_prompt
from .store import Run, RunStore
from .tools import StoppedByUser, Toolbox, run_stoppable, scrubbed_env

PREREQS = {
    "extract_rules": [],
    "analysis": ["outputs/rules.json"],
    "risk": ["outputs/rules.json", "outputs/analytics.json", "outputs/history.json"],
    "team": ["outputs/risk-register.md", "outputs/auditor-comments.md"],
}
NEXT = {"extract_rules": "analysis", "analysis": "risk", "risk": "gate1", "gate1": "team", "team": "signoff"}
DECISIONS = ("Approve", "Reject", "Change")


class StageError(Exception):
    pass


class Orchestrator:
    def __init__(self, store: RunStore = None, llm_factory=None):
        self.store = store or RunStore()
        self._llm_factory = llm_factory

    # ------------------------------------------------------------------ helpers
    def llm(self):
        if self._llm_factory:
            return self._llm_factory()
        from .llm import AnthropicLLM
        return AnthropicLLM()

    def script(self, run: Run, *args, check=True, label=None) -> subprocess.CompletedProcess:
        """Run one of the project's Python scripts in the run's workspace (a harness hook, not an agent action)."""
        self.check_stop(run)
        proc = run_stoppable([sys.executable, *args], run.workspace, 900, lambda: run.state.get("stop_requested", False))
        output = (proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else "")).strip()
        run.emit({"type": "hook", "text": f"{label or 'harness'} ran `python {' '.join(args)}` → exit {proc.returncode}",
                  "detail": output[-1500:]})
        if check and proc.returncode != 0:
            raise StageError(f"`{' '.join(args)}` failed: {output[-800:]}")
        return proc

    def check_stop(self, run: Run):
        if run.state.get("stop_requested"):
            raise StoppedByUser("stopped by the user")

    def agent(self, run: Run, name: str, outbox=None, suffix: str = "") -> Agent:
        toolbox = Toolbox(run.workspace, name, POLICIES[name], outbox=outbox,
                          should_stop=lambda: run.state.get("stop_requested", False))
        return Agent(name, system_prompt(run.workspace, name), toolbox, self.llm(), run.emit,
                     transcript_path=run.folder / "transcripts" / f"{name}{suffix}.json",
                     should_stop=lambda: run.state.get("stop_requested", False))

    def restore(self, run: Run, *rels):
        """Replay mode: copy Part A outputs into the run's workspace."""
        self.check_stop(run)
        for rel in rels:
            src = config.BASELINE_OUTPUTS / rel
            dst = run.workspace / "outputs" / rel
            if not src.exists():
                raise StageError(f"replay unavailable: Part A output {rel} is missing")
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        run.emit({"type": "info", "text": f"replay: restored Part A output(s) {', '.join(rels)}"})

    def file(self, run: Run, rel: str) -> str:
        p = run.workspace / rel
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _require(self, run: Run, stage: str):
        missing = [rel for rel in PREREQS.get(stage, []) if not (run.workspace / rel).exists()]
        if missing:
            raise StageError(f"cannot start {stage}: missing {', '.join(missing)}")

    def _launch(self, run: Run, stage: str, work, *args):
        """Run a stage in a background thread; mark it done/failed; unlock the next stage."""
        with run.lock:
            if run.state["busy"]:
                raise StageError("another step is still running for this run")
            run.state["busy"] = True
            run.state["stop_requested"] = False
            run.save()

        def worker():
            try:
                outcome = work(run, *args) or {}
                if outcome.get("status"):
                    run.set_stage(stage, finished_at=_now(), **outcome)
                else:
                    run.set_stage(stage, status="done", finished_at=_now(), **outcome)
                    nxt = NEXT.get(stage)
                    if nxt and run.stage(nxt)["status"] in ("locked", "failed", "done", "ready"):
                        run.set_stage(nxt, status="waiting" if nxt in ("gate1", "signoff") else "ready")
            except StoppedByUser:
                run.emit({"type": "warning", "text": "Stopped. Run this step again when you are ready."})
                run.set_stage(stage, status="stopped", error=None, finished_at=_now())
            except Exception as exc:  # report every failure in the UI instead of crashing the server
                run.emit({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
                run.set_stage(stage, status="failed", error=str(exc)[:1500], finished_at=_now())
            finally:
                with run.lock:
                    run.state["busy"] = False
                    run.save()

        threading.Thread(target=worker, daemon=True, name=f"{run.id}-{stage}").start()

    def _reset_downstream(self, run: Run, stage: str):
        """Re-running a stage invalidates everything after it."""
        nxt = NEXT.get(stage)
        while nxt:
            run.state["stages"][nxt] = {"status": "locked"}
            nxt = NEXT.get(nxt)
        if stage in ("extract_rules", "analysis", "risk"):
            run.state["gate1"] = {"round": 0, "decisions": {}, "awaiting_review": []}
            for rel in ("auditor-comments.md", "risk-ratings.gate.json"):
                (run.workspace / "outputs" / rel).unlink(missing_ok=True)
        run.state["team"] = {"flags": {}, "turns": 0}
        run.save()

    # ------------------------------------------------------------------ public API
    def stop(self, run_id: str):
        """Ask the running agents to stop before their next LLM call (no further API spend)."""
        run = self.store.get(run_id)
        if not run.state["busy"]:
            raise StageError("nothing is running")
        with run.lock:
            run.state["stop_requested"] = True
            run.save()
        run.emit({"type": "warning", "text": "Stop requested: running scripts are ended now; agents stop before their next model call."})

    def start_stage(self, run_id: str, stage: str, mode: str = "agent"):
        run = self.store.get(run_id)
        if stage not in ("extract_rules", "analysis", "risk", "team"):
            raise StageError(f"{stage} is not an automated stage")
        if mode not in ("agent", "replay"):
            raise StageError("mode must be 'agent' or 'replay'")
        if run.stage(stage)["status"] == "locked":
            raise StageError(f"{stage} is locked: finish the previous stage first")
        self._require(run, stage)
        self._reset_downstream(run, stage)
        run.set_stage(stage, status="running", mode=mode, started_at=_now(), error=None, report=None)
        self._launch(run, stage, getattr(self, f"_stage_{stage}"), mode)

    # ------------------------------------------------------------------ stage 1: /extract-rules (skill)
    def _stage_extract_rules(self, run: Run, mode: str):
        if mode == "replay":
            self.restore(run, "rules.json")
            report = "Restored Part A rules.json."
        else:
            run.emit({"type": "info", "text": "Human invoked /extract-rules: running the extract-rules skill (no sub-agent)."})
            report = self.agent(run, "extract-rules").run("/extract-rules")
        proc = self.script(run, ".claude/skills/extract-rules/verify_quotes.py", "outputs/rules.json", "data/contract-rules.pdf",
                           label="verification hook")
        return {"report": report, "verification": proc.stdout.strip()[-500:]}

    # ------------------------------------------------------------------ stage 2: two sub-agents in parallel
    def _stage_analysis(self, run: Run, mode: str):
        if mode == "replay":
            self.restore(run, "history.json", "committee-text")
            run.emit({"type": "info", "text": "replay: re-running the spend-analyst's Python (Python computes every number)"})
            self.script(run, "scripts/analyze_spend.py", label="replay")
            self.script(run, "scripts/self_check_analytics.py", label="replay")
            reports = {"spend-analyst": "Re-ran scripts/analyze_spend.py and self-check.", "findings-analyst": "Restored Part A history.json."}
        else:
            reports, errors = {}, {}

            def work(name, brief):
                try:
                    reports[name] = self.agent(run, name).run(brief)
                except StoppedByUser:
                    errors[name] = "stopped"
                except Exception as exc:
                    errors[name] = f"{type(exc).__name__}: {exc}"

            briefs = {
                "spend-analyst": "Lead: outputs/rules.json is ready. Carry out your full specification and report.",
                "findings-analyst": "Lead: carry out your full specification on data/committee/*.pdf and report.",
            }
            threads = [threading.Thread(target=work, args=(n, b)) for n, b in briefs.items()]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.check_stop(run)
            if errors:
                raise StageError("; ".join(f"{k}: {v}" for k, v in errors.items()))
        # Verification hooks: the stage is done only if both outputs pass their own checkers.
        analytics = json.loads(self.file(run, "outputs/analytics.json") or "{}")
        if (analytics.get("self_check") or {}).get("status") != "passed":
            raise StageError("analytics.json self_check has not passed")
        if not (run.workspace / "outputs/analytics-full/spend-clean.csv").exists():
            self.script(run, "scripts/analyze_spend.py", label="harness hook (spend-clean.csv missing)")
            self.script(run, "scripts/self_check_analytics.py", label="harness hook")
        self.script(run, "scripts/verify_history.py", "outputs/history.json", "data/committee", label="verification hook")
        return {"report": "\n\n".join(f"**{k}**\n{v}" for k, v in reports.items())}

    # ------------------------------------------------------------------ stage 3: risk-assessor
    def _stage_risk(self, run: Run, mode: str):
        if mode == "replay":
            src = config.BASELINE_OUTPUTS / "risk-ratings.gate.json"  # Part A's pre-gate ratings
            if not src.exists():
                raise StageError("replay unavailable: Part A risk-ratings.gate.json is missing")
            shutil.copy2(src, run.workspace / "outputs/risk-ratings.json")
            run.emit({"type": "info", "text": "replay: restored Part A pre-gate risk ratings"})
            self.script(run, ".claude/skills/risk-assessment/build_register.py", label="replay")
            report = "Restored Part A pre-gate ratings and rebuilt the register."
        else:
            report = self.agent(run, "risk-assessor").run(
                "Lead: rules.json, analytics.json and history.json are ready. This is the pre-gate run: rate the risks, "
                "build the register and the decision template, and report.")
        self._check_register(run, pre_gate=True)
        run.state["gate1"] = {"round": 0, "decisions": {}, "awaiting_review": []}
        run.save()
        return {"report": report}

    def _check_register(self, run: Run, pre_gate: bool):
        register = self.file(run, "outputs/risk-register.md")
        comments = self.file(run, "outputs/auditor-comments.md")
        if not register or not comments:
            raise StageError("risk-register.md or auditor-comments.md was not produced")
        if pre_gate and "## Revision after auditor gate" in register:
            raise StageError("register unexpectedly contains a post-gate revision")
        if not pre_gate and "## Revision after auditor gate" not in register:
            raise StageError("register was not rebuilt as a post-auditor revision")

    def risks(self, run_id: str) -> list:
        """Risks for the gate-1 form: id, title, score, from ratings + the rendered register."""
        run = self.store.get(run_id)
        ratings = json.loads(self.file(run, "outputs/risk-ratings.json") or '{"risks": []}')
        register = self.file(run, "outputs/risk-register.md")
        out = []
        for r in ratings.get("risks", []):
            m = re.search(rf"## {re.escape(r['id'])}: ([^\n]+)\n(.*?)(?=\n## |\Z)", register, re.S)
            score = re.search(r"\*\*Score and band:\*\* (.+?)\.", m.group(2)) if m else None
            out.append({"id": r["id"], "title": m.group(1) if m else r.get("risk", ""),
                        "score": score.group(1) if score else "", "section": m.group(2).strip() if m else ""})
        return out

    # ------------------------------------------------------------------ stage 4: human gate 1
    def submit_gate1(self, run_id: str, decisions: dict):
        """decisions: {risk_id: {"decision": Approve|Reject|Change, "comment": str}}"""
        run = self.store.get(run_id)
        st = run.stage("gate1")
        if st["status"] not in ("waiting", "failed", "stopped"):
            raise StageError("the auditor gate is not open")
        ids = [r["id"] for r in self.risks(run_id)]
        clean = {}
        for rid in ids:
            d = decisions.get(rid) or {}
            decision = (d.get("decision") or "").strip().capitalize()
            comment = " ".join((d.get("comment") or "").split()).replace("|", "/")
            if decision not in DECISIONS:
                raise StageError(f"{rid}: choose Approve, Reject or Change")
            if decision == "Change" and not comment:
                raise StageError(f"{rid}: describe the requested change in the comment")
            clean[rid] = {"decision": decision, "comment": comment}
        ratings = json.loads(self.file(run, "outputs/risk-ratings.json") or "{}")
        if (ratings.get("revision") or {}).get("changes") and any(d["decision"] == "Change" for d in clean.values()):
            raise StageError("This is the review round for the revised register: approve or reject each risk. "
                             "(The register builder validates one round of changes against the pre-gate snapshot.)")
        for later in ("team", "signoff"):
            run.state["stages"][later] = {"status": "locked"}
        gate = run.state["gate1"]
        gate["round"] += 1
        gate["decisions"] = clean
        run.save()
        run.set_stage("gate1", status="running", error=None)
        run.emit({"type": "gate", "text": "Auditor decisions: " + ", ".join(f"{k} {v['decision']}" for k, v in clean.items())})
        self._launch(run, "gate1", self._apply_gate1, clean)

    def _write_comments(self, run: Run, rows: dict):
        lines = ["# Auditor decisions", "", "Enter Approve, Reject, or Change. Describe the requested fields in Comment for Change.", "",
                 "| Risk ID | Decision | Comment |", "| --- | --- | --- |"]
        lines += [f"| {rid} | {d['decision']} | {d['comment']} |" for rid, d in rows.items()]
        (run.workspace / "outputs/auditor-comments.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _apply_gate1(self, run: Run, decisions: dict):
        gate = run.state["gate1"]
        changes = [rid for rid, d in decisions.items() if d["decision"] == "Change"]
        ratings_path = run.workspace / "outputs/risk-ratings.json"
        ratings = json.loads(ratings_path.read_text(encoding="utf-8"))
        revised_before = bool((ratings.get("revision") or {}).get("changes"))

        if changes:
            # Judgment is needed: the risk-assessor applies the requested changes (its post-gate run).
            self._write_comments(run, decisions)
            report = self.agent(run, "risk-assessor", suffix=f"-revision{gate['round']}").run(
                "Lead: this is a post-gate run. The auditor has recorded decisions in outputs/auditor-comments.md:\n\n"
                + self.file(run, "outputs/auditor-comments.md")
                + "\nApply them exactly as your definition and skill require, rebuild the register, and report.")
            self._check_register(run, pre_gate=False)
            gate["awaiting_review"] = changes
            run.save()
            run.emit({"type": "gate", "text": f"Revised register ready. The auditor must review {', '.join(changes)} and approve or reject."})
            return {"status": "waiting", "report": report}

        if revised_before:
            # Second pass after a Change round: the auditor accepts or rejects the revised risks. As in
            # Part A, the final decision is recorded alongside the original Change request.
            prior = {}
            for line in self.file(run, "outputs/auditor-comments.md").splitlines():
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) == 3 and cells[1].lower() == "change":
                    prior[cells[0]] = cells[2]
            final = {rid: {"decision": d["decision"],
                           "comment": (f"Change: {prior[rid]} Revised rating reviewed. {d['comment']}".strip() if rid in prior else d["comment"])}
                     for rid, d in decisions.items()}
            self._write_comments(run, final)
        else:
            # No judgment needed: approvals and rejections are applied mechanically, then the
            # skill's own builder validates them and renders the post-gate revision.
            self._write_comments(run, decisions)
            ratings["risks"] = [r for r in ratings["risks"] if decisions[r["id"]]["decision"] != "Reject"]
            ratings["revision"] = {"changes": []}
            ratings_path.write_text(json.dumps(ratings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            run.emit({"type": "info", "text": "No changes requested: the harness applied approvals/rejections and rebuilt the register."})
            self.script(run, ".claude/skills/risk-assessment/build_register.py", label="gate hook")
            self._check_register(run, pre_gate=False)
        gate["awaiting_review"] = []
        run.save()
        proc = self.script(run, ".claude/skills/audit-program/build_pack.py", "--gate", label="gate check")
        return {"report": proc.stdout.strip()}

    # ------------------------------------------------------------------ stage 5: agent team
    def _stage_team(self, run: Run, mode: str):
        gate = self.script(run, ".claude/skills/audit-program/build_pack.py", "--gate", label="gate check")
        for rel in ("review.md", "challenges.md"):
            (run.workspace / "outputs" / rel).unlink(missing_ok=True)
        if mode == "replay":
            self.restore(run, "audit-plan.json", "challenges.md")
            self.script(run, ".claude/skills/audit-program/build_pack.py", label="replay (planner's builder)")
            checks = self.script(run, "scripts/checks/run_checks.py", check=False, label="replay (qa-reviewer's checks)")
            (run.workspace / "outputs/review.md").write_text(
                checks.stdout.strip() + "\n\n## Auditor sign-off\n\nDecision:\n\nName:\n\nDate:\n\nComments:\n", encoding="utf-8")
            report = "Restored Part A audit plan and challenge log; rebuilt the pack and re-ran QA."
        else:
            report = self._run_team(run, gate.stdout.strip())
        # Independent verification hook: the harness re-runs QA itself rather than trusting the team's word.
        checks = self.script(run, "scripts/checks/run_checks.py", check=False, label="harness QA re-check")
        review = self.file(run, "outputs/review.md")
        if "READY FOR SIGN-OFF" in review and checks.returncode == 0:
            return {"report": report, "verdict": "READY FOR SIGN-OFF"}
        run.emit({"type": "warning", "text": "Pack is BLOCKED: it goes back to the team, never to the auditor."})
        return {"status": "blocked", "report": report, "verdict": "BLOCKED"}

    def _run_team(self, run: Run, gate_text: str) -> str:
        inbox = {name: [] for name in TEAM}
        lead_log = []
        flags = run.state["team"]["flags"]

        def outbox(sender, to, message):
            run.emit({"type": "message", "from": sender, "to": to, "text": message})
            if to == "lead":
                lead_log.append(f"[{sender}] {message}")
                for token, key in (("PLANNER DONE", "planner"), ("CHALLENGER DONE", "challenger"), ("QA DONE", "qa-reviewer")):
                    if token in message:
                        flags[key] = True
                run.save()
            else:
                inbox[to].append((sender, message))

        agents = {name: self.agent(run, name, outbox=outbox) for name in TEAM}

        def kickoff(name):
            others = " and ".join(f"`{o}`" for o in TEAM if o != name)
            return (f"Lead: the gate is closed ({gate_text}). Your teammates are {others}. Follow your definition. "
                    "The planner starts now; the challenger and qa-reviewer wait for PACK READY v1.")
        started = set()
        inbox["planner"].append(("lead", "Start building the pack."))
        nudged = set()
        last_turn = {}
        turns = 0
        while not all(flags.get(n) for n in TEAM):
            ready = [n for n in TEAM if inbox[n]]
            if not ready:
                pending = [n for n in TEAM if not flags.get(n) and n not in nudged]
                if not pending:
                    raise StageError("the team stalled with no pending messages. Lead log: " + " | ".join(lead_log[-5:]))
                for n in pending:
                    inbox[n].append(("lead", "No messages are pending for you. Continue your protocol if you can. If you are "
                                             "finished, send your DONE message to the lead; if you are blocked, tell the lead why."))
                    nudged.add(n)
                continue
            if turns >= config.MAX_TEAM_TURNS:
                raise StageError(f"team exceeded {config.MAX_TEAM_TURNS} turns without finishing")
            # Fair turn-taking: of the teammates with messages waiting, the one idle longest goes next.
            name = min(ready, key=lambda n: (last_turn.get(n, -1), TEAM.index(n)))
            last_turn[name] = turns
            batch, inbox[name] = inbox[name], []
            text = "\n\n".join(f"Message from {sender}:\n{msg}" for sender, msg in batch)
            if name not in started:
                text = kickoff(name) + "\n\n" + text
                started.add(name)
            turns += 1
            run.state["team"]["turns"] = turns
            run.save()
            run.emit({"type": "turn", "agent": name, "text": f"team turn {turns}: {name}"})
            agents[name].run(text)
        return "Team finished. Lead log:\n" + "\n".join(lead_log)

    # ------------------------------------------------------------------ stage 6: human gate 2
    def signoff(self, run_id: str, decision: str, name: str, comments: str = ""):
        run = self.store.get(run_id)
        if run.stage("signoff")["status"] not in ("waiting",):
            raise StageError("sign-off is not open: the pack must be READY FOR SIGN-OFF first")
        if decision not in ("Sign off", "Return to team"):
            raise StageError("decision must be 'Sign off' or 'Return to team'")
        if not name.strip():
            raise StageError("enter the auditor's name")
        path = run.workspace / "outputs/review.md"
        review = path.read_text(encoding="utf-8")
        body = review.split("## Auditor sign-off")[0].rstrip()
        block = (f"\n\n## Auditor sign-off\n\nDecision: {decision}\n\nName: {name.strip()}\n\n"
                 f"Date: {date.today().isoformat()}\n\nComments: {' '.join(comments.split())}\n")
        path.write_text(body + block, encoding="utf-8")
        run.emit({"type": "gate", "text": f"Auditor {name.strip()}: {decision}"})
        if decision == "Sign off":
            run.set_stage("signoff", status="done", finished_at=_now(), signed_by=name.strip())
        else:
            run.set_stage("signoff", status="locked")
            run.set_stage("team", status="ready", verdict=None)


def _now():
    from .store import now
    return now()
