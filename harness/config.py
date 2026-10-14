"""Harness configuration. Everything tunable comes from environment variables."""
import os
from pathlib import Path

# The repository root: CLAUDE.md, .claude/, data/, scripts/ and the Part A outputs/ live here.
PROJECT_ROOT = Path(os.environ.get("HARNESS_PROJECT_ROOT", Path(__file__).resolve().parents[1]))

# Where each run gets its own isolated workspace (a copy of the project).
RUNS_DIR = Path(os.environ.get("HARNESS_RUNS_DIR", PROJECT_ROOT / "runs"))

# Part A outputs, used by "replay" mode so a stage can be reproduced without spending API credit.
BASELINE_OUTPUTS = PROJECT_ROOT / "outputs"

MODEL = os.environ.get("HARNESS_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = int(os.environ.get("HARNESS_MAX_TOKENS", "32000"))

# Per-agent overrides, e.g. HARNESS_MODEL_QA_REVIEWER=claude-haiku-5-5 or HARNESS_MAX_TOKENS_PLANNER=48000.
# extract-rules writes rules.json (often 40k+ tokens) in one go, and its context stays small, so it gets a
# larger output limit; if the model rejects it, the client falls back to MAX_TOKENS automatically.
AGENT_MAX_TOKENS_DEFAULTS = {"extract-rules": 64000}


def _agent_env(prefix: str, agent: str):
    return os.environ.get(f"{prefix}_{agent.upper().replace('-', '_')}")


def model_for(agent: str) -> str:
    return _agent_env("HARNESS_MODEL", agent) or MODEL


def max_tokens_for(agent: str) -> int:
    value = _agent_env("HARNESS_MAX_TOKENS", agent)
    return int(value) if value else AGENT_MAX_TOKENS_DEFAULTS.get(agent, MAX_TOKENS)

# Agent-loop limits: the harness, not the model, decides when enough is enough.
MAX_TOOL_ITERATIONS = int(os.environ.get("HARNESS_MAX_TOOL_ITERATIONS", "60"))
MAX_TEAM_TURNS = int(os.environ.get("HARNESS_MAX_TEAM_TURNS", "30"))
# The spend analysis takes ~7 min on Render's free plan, so commands get 15 minutes.
COMMAND_TIMEOUT_S = int(os.environ.get("HARNESS_COMMAND_TIMEOUT_S", "900"))

# Context management.
TOOL_OUTPUT_LIMIT = int(os.environ.get("HARNESS_TOOL_OUTPUT_LIMIT", "12000"))  # characters per tool result
# Cached history is cheap to keep ($0.10/M) but trimming it forces a full cache re-write ($2.50/M), so trim late.
COMPACT_AFTER_INPUT_TOKENS = int(os.environ.get("HARNESS_COMPACT_AFTER", "150000"))
KEEP_RECENT_TOOL_RESULTS = int(os.environ.get("HARNESS_KEEP_RECENT_RESULTS", "8"))

# Mutating API calls need this code (blank = open, for local development).
ACCESS_CODE = os.environ.get("APP_ACCESS_CODE", "")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Needed only for API keys that are not scoped to a workspace (the API then asks for this header).
ANTHROPIC_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
