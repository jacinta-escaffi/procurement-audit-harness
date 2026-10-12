"""Run storage: one folder per run with an isolated workspace, a state file and an event log."""
import json
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config

STAGES = ["extract_rules", "analysis", "risk", "gate1", "team", "signoff"]

# Copied into every run's workspace. outputs/ starts empty apart from the findings-analyst's navigation aid.
WORKSPACE_ITEMS = ["CLAUDE.md", ".claude", "data", "scripts", "requirements.txt"]
SEED_OUTPUTS = ["committee-text/evidence-map.md"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_state(run_id: str, label: str) -> dict:
    stages = {s: {"status": "locked"} for s in STAGES}
    stages["extract_rules"]["status"] = "ready"
    return {"id": run_id, "label": label, "created": now(), "busy": False, "stages": stages,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            "gate1": {"round": 0, "decisions": {}, "awaiting_review": []},
            "team": {"flags": {}, "turns": 0}}


class Run:
    def __init__(self, folder: Path):
        self.folder = folder
        self.workspace = folder / "workspace"
        self.lock = threading.RLock()
        self.state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        self.events = []
        events_file = folder / "events.jsonl"
        if events_file.exists():
            self.events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    @property
    def id(self):
        return self.state["id"]

    def save(self):
        with self.lock:
            tmp = self.folder / "state.json.tmp"
            tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.folder / "state.json")

    def emit(self, event: dict):
        with self.lock:
            event = {"seq": len(self.events), "time": now(), **event}
            if event.get("type") == "usage":
                for k, v in event["usage"].items():
                    self.state["usage"][k] = self.state["usage"].get(k, 0) + v
                self.save()
            self.events.append(event)
            with (self.folder / "events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def stage(self, name: str) -> dict:
        return self.state["stages"][name]

    def set_stage(self, name: str, **fields):
        with self.lock:
            self.state["stages"][name].update(fields)
            self.save()
        if "status" in fields:
            self.emit({"type": "stage", "stage": name, "status": fields["status"], "text": fields.get("error") or ""})


class RunStore:
    def __init__(self, root: Path = None):
        self.root = Path(root or config.RUNS_DIR)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs = {}
        for folder in sorted(self.root.iterdir()):
            if (folder / "state.json").exists():
                run = Run(folder)
                # A server restart kills worker threads: anything mid-flight becomes retryable.
                changed = False
                for name, st in run.state["stages"].items():
                    if st["status"] == "running":
                        st.update(status="failed", error="interrupted by a server restart; start the stage again")
                        changed = True
                if run.state.get("busy"):
                    run.state["busy"] = False
                    changed = True
                if changed:
                    run.save()
                self.runs[run.id] = run

    def create(self, label: str = "") -> Run:
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        folder = self.root / run_id
        ws = folder / "workspace"
        ws.mkdir(parents=True)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
        for item in WORKSPACE_ITEMS:
            src = config.PROJECT_ROOT / item
            if src.is_dir():
                shutil.copytree(src, ws / item, ignore=ignore)
            elif src.exists():
                shutil.copy2(src, ws / item)
        for rel in SEED_OUTPUTS:
            src = config.BASELINE_OUTPUTS / rel
            if src.exists():
                (ws / "outputs" / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, ws / "outputs" / rel)
        (ws / "outputs").mkdir(exist_ok=True)
        (folder / "state.json").write_text(json.dumps(new_state(run_id, label or "Audit planning run"), indent=1), encoding="utf-8")
        run = Run(folder)
        self.runs[run_id] = run
        run.emit({"type": "info", "text": "Workspace created: a fresh copy of CLAUDE.md, .claude/ (agents + skills), data/ and scripts/."})
        return run

    def get(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if not run:
            raise KeyError(run_id)
        return run

    def list(self) -> list:
        return [{"id": r.id, "label": r.state["label"], "created": r.state["created"],
                 "stages": {k: v["status"] for k, v in r.state["stages"].items()}}
                for r in sorted(self.runs.values(), key=lambda r: r.state["created"], reverse=True)]
