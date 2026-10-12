"""The tools the harness gives an agent, and the code that executes them safely.

These replace Claude Code's built-in tools:
  Read -> read_file, Write -> write_file, Edit -> edit_file, Glob -> list_files,
  Grep -> search_files, Bash -> run_command (allow-listed) / run_python (inspection only),
  agent-team messaging -> send_message.
Every call is checked against the agent's Policy before it runs.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .permissions import Policy, TEAM

TEXT_SUFFIXES = {".md", ".json", ".txt", ".csv", ".py", ".yaml", ".yml", ".jsonl", ""}
BINARY_HINT = {".pdf": "PDFs are binary: use run_command with `pdftotext -layout <file> <out.txt>` and then read_file.",
               ".xlsx": "Excel workbooks are binary: inspect them with pandas (run_python) or the project scripts."}


class StoppedByUser(Exception):
    """The user pressed Stop: abandon the current stage."""


def run_stoppable(argv, cwd, timeout, should_stop=None) -> subprocess.CompletedProcess:
    """Run a command, checking twice a second whether the user pressed Stop; kill it if so."""
    proc = subprocess.Popen(argv, cwd=cwd, env=scrubbed_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.time()
    while True:
        try:
            out, err = proc.communicate(timeout=0.5)
            return subprocess.CompletedProcess(argv, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            if should_stop and should_stop():
                proc.kill()
                proc.communicate()
                raise StoppedByUser("stopped by the user")
            if time.time() - started > timeout:
                proc.kill()
                proc.communicate()
                raise subprocess.TimeoutExpired(argv, timeout)


class ToolError(Exception):
    """A tool call the harness refused or could not complete. Returned to the model as an error result."""


def truncate(text: str, limit: int = None) -> str:
    limit = limit or config.TOOL_OUTPUT_LIMIT
    if len(text) <= limit:
        return text
    head = text[: limit * 3 // 4]
    tail = text[-limit // 4:]
    return f"{head}\n\n[... harness truncated {len(text) - limit} characters; read the file in pages or narrow the query ...]\n\n{tail}"


def scrubbed_env() -> dict:
    """Subprocesses never see the API key or the access code."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "APP_ACCESS_CODE")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["MPLBACKEND"] = "Agg"
    return env


def tool_schemas(policy: Policy) -> list:
    tools = [
        {"name": "list_files", "description": "List files under a directory of the workspace (recursive). Use a glob pattern to narrow it.",
         "input_schema": {"type": "object", "properties": {
             "path": {"type": "string", "description": "Directory relative to the project root, e.g. 'outputs'. Default '.'"},
             "pattern": {"type": "string", "description": "Optional glob, e.g. '*.json'"}}}},
        {"name": "read_file", "description": "Read a text file with line numbers. Long files are read in pages: pass offset (1-based line) and limit (lines).",
         "input_schema": {"type": "object", "properties": {
             "path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["path"]}},
        {"name": "search_files", "description": "Regex search across text files (like grep). Returns matching lines with file and line number.",
         "input_schema": {"type": "object", "properties": {
             "pattern": {"type": "string"}, "path": {"type": "string", "description": "File or directory. Default '.'"},
             "max_results": {"type": "integer"}}, "required": ["pattern"]}},
        {"name": "write_file", "description": "Create or overwrite a file you own. For very long content, write the first part with mode 'w' and later parts with mode 'a' (append).",
         "input_schema": {"type": "object", "properties": {
             "path": {"type": "string"}, "content": {"type": "string"},
             "mode": {"type": "string", "enum": ["w", "a"]}}, "required": ["path", "content"]}},
        {"name": "edit_file", "description": "Replace an exact, unique string in a file you own.",
         "input_schema": {"type": "object", "properties": {
             "path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}},
             "required": ["path", "old_string", "new_string"]}},
        {"name": "run_command", "description": "Run an allow-listed command from the project root: `python <script> [args]` for your permitted scripts"
                                               + (", or `pdftotext [-layout] [-enc UTF-8] <pdf> <out.txt|->`" if policy.pdftotext else "")
                                               + f". Your permitted scripts: {', '.join(policy.scripts) or 'none'}.",
         "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    ]
    if policy.inline_python:
        tools.append({"name": "run_python", "description": "Run a short Python snippet from the project root to INSPECT data (pandas .head()/.info()/aggregates). Print only small summaries; never print full datasets.",
                      "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}})
    if policy.team:
        tools.append({"name": "send_message", "description": "Send a message to a teammate (planner, challenger, qa-reviewer) or to the lead. Delivered on their next turn.",
                      "input_schema": {"type": "object", "properties": {
                          "to": {"type": "string", "enum": [*TEAM, "lead"]}, "message": {"type": "string"}}, "required": ["to", "message"]}})
    return tools


class Toolbox:
    def __init__(self, workspace: Path, agent: str, policy: Policy, outbox=None, should_stop=None):
        self.should_stop = should_stop
        self.root = workspace.resolve()
        self.agent = agent
        self.policy = policy
        self.outbox = outbox  # callable(sender, to, message) for team messaging

    # ---------- path safety ----------
    def _resolve(self, path: str) -> tuple:
        if not path:
            path = "."
        candidate = (self.root / path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ToolError(f"path escapes the workspace: {path}")
        rel = candidate.relative_to(self.root).as_posix()
        return candidate, ("." if rel == "" else rel)

    def _check_read(self, rel: str):
        if not self.policy.can_read(rel):
            raise ToolError(f"permission denied: {self.agent} may not read {rel}")

    def _check_write(self, rel: str):
        if not self.policy.can_write(rel, self.agent):
            raise ToolError(f"permission denied: {self.agent} may not write {rel}. You own only: "
                            f"{', '.join(self.policy.writable)} (plus outputs/.scratch/{self.agent}/)")

    # ---------- dispatch ----------
    def execute(self, name: str, args: dict) -> str:
        handler = getattr(self, f"t_{name}", None)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")
        return truncate(handler(**args))

    def t_list_files(self, path: str = ".", pattern: str = None) -> str:
        base, rel = self._resolve(path)
        if not base.is_dir():
            raise ToolError(f"not a directory: {rel}")
        found = []
        for p in sorted(base.rglob(pattern or "*")):
            r = p.relative_to(self.root).as_posix()
            if p.is_file() and not any(part in (".git", "__pycache__", ".venv") for part in p.parts) and self.policy.can_read(r):
                found.append(f"{r}  ({p.stat().st_size} bytes)")
        if len(found) > 400:
            found = found[:400] + [f"... {len(found) - 400} more"]
        return "\n".join(found) or "(no files)"

    def t_read_file(self, path: str, offset: int = 1, limit: int = 400) -> str:
        p, rel = self._resolve(path)
        self._check_read(rel)
        if not p.is_file():
            raise ToolError(f"file not found: {rel}")
        if p.suffix.lower() in BINARY_HINT:
            raise ToolError(BINARY_HINT[p.suffix.lower()])
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        offset = max(1, offset or 1)
        limit = max(1, min(limit or 400, 2000))
        chunk = lines[offset - 1: offset - 1 + limit]
        body = "\n".join(f"{i:>6}\t{line[:2000]}" for i, line in enumerate(chunk, start=offset))
        more = f"\n[lines {offset}-{offset + len(chunk) - 1} of {len(lines)}]" if len(lines) > len(chunk) else ""
        return body + more

    def t_search_files(self, pattern: str, path: str = ".", max_results: int = 100) -> str:
        base, _ = self._resolve(path)
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"bad regex: {exc}")
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        hits = []
        for f in files:
            r = f.relative_to(self.root).as_posix()
            if not f.is_file() or f.suffix.lower() not in TEXT_SUFFIXES or not self.policy.can_read(r):
                continue
            if any(part in (".git", "__pycache__", ".venv") for part in f.parts) or f.stat().st_size > 5_000_000:
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{r}:{i}: {line[:300]}")
                    if len(hits) >= (max_results or 100):
                        return "\n".join(hits) + "\n[result limit reached]"
        return "\n".join(hits) or "(no matches)"

    def t_write_file(self, path: str, content: str, mode: str = "w") -> str:
        p, rel = self._resolve(path)
        self._check_write(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a" if mode == "a" else "w", encoding="utf-8") as fh:
            fh.write(content)
        return f"{'appended to' if mode == 'a' else 'wrote'} {rel} ({p.stat().st_size} bytes total)"

    def t_edit_file(self, path: str, old_string: str, new_string: str) -> str:
        p, rel = self._resolve(path)
        self._check_write(rel)
        if not p.is_file():
            raise ToolError(f"file not found: {rel}")
        text = p.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count != 1:
            raise ToolError(f"old_string must occur exactly once in {rel}; found {count}")
        p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"edited {rel}"

    def _run(self, argv: list, timeout: int = None) -> str:
        try:
            proc = run_stoppable(argv, self.root, timeout or config.COMMAND_TIMEOUT_S, self.should_stop)
        except subprocess.TimeoutExpired:
            raise ToolError(f"command timed out after {timeout or config.COMMAND_TIMEOUT_S}s")
        out = f"exit code: {proc.returncode}\n"
        if proc.stdout:
            out += f"--- stdout ---\n{proc.stdout}"
        if proc.stderr:
            out += f"\n--- stderr ---\n{proc.stderr}"
        return out

    def t_run_command(self, command: str) -> str:
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ToolError(f"could not parse command: {exc}")
        if not argv:
            raise ToolError("empty command")
        if any(tok in ("|", ";", "&&", "||", ">", "<", "&") for tok in argv):
            raise ToolError("pipes, redirects and command chaining are not available; run one command at a time")
        prog = Path(argv[0]).name
        if prog in ("python", "python3") or prog.startswith("python3."):
            if len(argv) < 2 or argv[1].startswith("-"):
                raise ToolError("run a script file: `python <script> [args]`")
            _, script = self._resolve(argv[1])
            if script not in self.policy.scripts:
                raise ToolError(f"permission denied: {self.agent} may run only: {', '.join(self.policy.scripts) or 'no scripts'}")
            return self._run([sys.executable, script, *argv[2:]])
        if prog == "pdftotext":
            if not self.policy.pdftotext:
                raise ToolError(f"permission denied: {self.agent} may not run pdftotext")
            files = [a for a in argv[1:] if not a.startswith("-") and a != "UTF-8"]
            if not files:
                raise ToolError("pdftotext needs an input PDF")
            src, src_rel = self._resolve(files[0])
            self._check_read(src_rel)
            if len(files) > 1 and files[1] != "-":
                _, out_rel = self._resolve(files[1])
                self._check_write(out_rel)
                (self.root / out_rel).parent.mkdir(parents=True, exist_ok=True)
            return self._run(["pdftotext", *argv[1:]], timeout=120)
        raise ToolError("only `python <permitted script>` and `pdftotext` are allowed in this harness")

    def t_run_python(self, code: str) -> str:
        if not self.policy.inline_python:
            raise ToolError(f"permission denied: {self.agent} may not run inline Python")
        scratch = self.root / "outputs" / ".scratch" / self.agent
        scratch.mkdir(parents=True, exist_ok=True)
        snippet = scratch / "snippet.py"
        snippet.write_text(code, encoding="utf-8")
        return self._run([sys.executable, str(snippet.relative_to(self.root))], timeout=180)

    def t_send_message(self, to: str, message: str) -> str:
        if self.outbox is None:
            raise ToolError("messaging is only available inside the agent team")
        if to == self.agent:
            raise ToolError("cannot message yourself")
        self.outbox(self.agent, to, message)
        return f"message queued for {to}"


def summarize_args(name: str, args: dict) -> str:
    """Short, human-readable description of a tool call for the activity feed."""
    if name in ("write_file", "edit_file"):
        return f"{args.get('path')}" + (" (append)" if args.get("mode") == "a" else "")
    if name == "run_python":
        first = (args.get("code") or "").strip().splitlines()
        return first[0][:120] if first else ""
    if name == "send_message":
        return f"→ {args.get('to')}: {(args.get('message') or '')[:160]}"
    return json.dumps(args, ensure_ascii=False)[:200]
