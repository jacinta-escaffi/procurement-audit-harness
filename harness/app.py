"""Web API and UI for the harness. Start locally with:  uvicorn harness.app:app --reload"""
import io
import zipfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from . import config
from .orchestrator import Orchestrator, StageError
from .permissions import POLICIES

STATIC = Path(__file__).parent / "static"
VIEWABLE = (".md", ".json", ".csv", ".txt")

app = FastAPI(title="Procurement Audit Planner harness")
orch = Orchestrator()
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def require_code(code: str):
    if config.ACCESS_CODE and code != config.ACCESS_CODE:
        raise HTTPException(401, "wrong or missing access code")


def get_run(run_id: str):
    try:
        return orch.store.get(run_id)
    except KeyError:
        raise HTTPException(404, "run not found")


@app.exception_handler(StageError)
async def stage_error(_: Request, exc: StageError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


class NewRun(BaseModel):
    label: str = ""


class StageStart(BaseModel):
    mode: str = "agent"


class Gate1(BaseModel):
    decisions: dict


class Signoff(BaseModel):
    decision: str
    name: str
    comments: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    return {"model": config.MODEL, "access_code_required": bool(config.ACCESS_CODE),
            "api_key_configured": bool(config.ANTHROPIC_API_KEY),
            "agents": {name: {"writes": list(p.writable), "scripts": list(p.scripts), "team": p.team}
                       for name, p in POLICIES.items()}}


@app.post("/api/check-code")
def check_code(x_access_code: str = Header("")):
    require_code(x_access_code)
    return {"ok": True}


@app.get("/api/runs")
def list_runs():
    return orch.store.list()


@app.post("/api/runs")
def create_run(body: NewRun, x_access_code: str = Header("")):
    require_code(x_access_code)
    return {"id": orch.store.create(body.label).id}


@app.get("/api/runs/{run_id}")
def run_state(run_id: str, after: int = -1):
    run = get_run(run_id)
    events = [e for e in run.events[after + 1:]]
    outputs = run.workspace / "outputs"
    files = sorted(p.relative_to(run.workspace).as_posix() for p in outputs.rglob("*")
                   if p.is_file() and p.suffix in VIEWABLE and ".scratch" not in p.parts
                   and "analytics-full" not in p.parts and "committee-text" not in p.parts)
    return {"state": run.state, "events": events, "files": files}


@app.get("/api/runs/{run_id}/risks")
def risks(run_id: str):
    get_run(run_id)
    return orch.risks(run_id)


@app.post("/api/runs/{run_id}/stages/{stage}")
def start_stage(run_id: str, stage: str, body: StageStart, x_access_code: str = Header("")):
    require_code(x_access_code)
    get_run(run_id)
    if body.mode == "agent" and not config.ANTHROPIC_API_KEY and orch._llm_factory is None:
        raise HTTPException(400, "ANTHROPIC_API_KEY is not configured on the server; use replay mode or set the key")
    orch.start_stage(run_id, stage, body.mode)
    return {"ok": True}


@app.post("/api/runs/{run_id}/stop")
def stop(run_id: str, x_access_code: str = Header("")):
    require_code(x_access_code)
    get_run(run_id)
    orch.stop(run_id)
    return {"ok": True}


@app.post("/api/runs/{run_id}/gate1")
def gate1(run_id: str, body: Gate1, x_access_code: str = Header("")):
    require_code(x_access_code)
    get_run(run_id)
    orch.submit_gate1(run_id, body.decisions)
    return {"ok": True}


@app.post("/api/runs/{run_id}/signoff")
def signoff(run_id: str, body: Signoff, x_access_code: str = Header("")):
    require_code(x_access_code)
    get_run(run_id)
    orch.signoff(run_id, body.decision, body.name, body.comments)
    return {"ok": True}


@app.get("/api/runs/{run_id}/file")
def read_file(run_id: str, path: str):
    run = get_run(run_id)
    target = (run.workspace / path).resolve()
    outputs = (run.workspace / "outputs").resolve()
    if outputs not in target.parents or not target.is_file() or target.suffix not in VIEWABLE:
        raise HTTPException(404, "file not available")
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > 400_000:
        text = text[:400_000] + "\n\n[truncated for display; download the pack for the full file]"
    return PlainTextResponse(text)


@app.get("/api/runs/{run_id}/transcript/{agent}")
def transcript(run_id: str, agent: str):
    run = get_run(run_id)
    folder = run.folder / "transcripts"
    matches = sorted(folder.glob(f"{Path(agent).name}*.json")) if folder.exists() else []
    if not matches:
        raise HTTPException(404, "no transcript yet")
    return FileResponse(matches[-1], media_type="application/json")


@app.get("/api/runs/{run_id}/download")
def download(run_id: str):
    run = get_run(run_id)
    buffer = io.BytesIO()
    outputs = run.workspace / "outputs"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(outputs.rglob("*")):
            if p.is_file() and ".scratch" not in p.parts and "analytics-full" not in p.parts:
                zf.write(p, p.relative_to(run.workspace).as_posix())
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="audit-pack-{run_id}.zip"'})
