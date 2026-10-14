"""Builds each agent's instructions from the Part A files, unchanged.

The system prompt = a short harness preamble + CLAUDE.md (project rules shared by every agent)
+ the agent's own .claude/agents/<name>.md body + any skill it loads (.claude/skills/<skill>/SKILL.md).
That mirrors what Claude Code loads, so the agents behave as specified in Part A.
"""
from datetime import datetime, timezone
from pathlib import Path

from .permissions import POLICIES, TEAM

AGENT_SKILLS = {"risk-assessor": "risk-assessment", "planner": "audit-program"}

PRE_EXISTING_CODE = {
    "spend-analyst": "scripts/clean.py, scripts/analyze_spend.py and scripts/self_check_analytics.py already exist in this "
                     "workspace from the Part A build. Review them against your specification, run them, and change them only "
                     "if a run fails or the code contradicts the specification. Do not rewrite working code.",
    "findings-analyst": "scripts/verify_history.py already exists in this workspace and is your verifier.",
}


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip()
    return text


def preamble(name: str) -> str:
    policy = POLICIES[name]
    lines = [
        f"You are `{name}`, running inside the Procurement Audit Planner harness: a hosted web application that is your runtime "
        "in place of Claude Code. Your instructions below come from the project's CLAUDE.md and your own definition file.",
        "",
        "Harness environment:",
        "- Your working directory is the project root; use paths relative to it.",
        "- Tool mapping from your instructions: Read -> read_file, Write -> write_file, Edit -> edit_file, Glob -> list_files, "
        "Grep -> search_files, Bash -> run_command. `python` in run_command is the project virtual environment's Python.",
        "- run_command runs one command with no shell: no pipes, redirects or `&&`. Only `python <permitted script> [args]`"
        + (" and `pdftotext`" if policy.pdftotext else "") + " are allowed. `python -c`, `pdfinfo` and other programs are not "
        "available; use read_file, json_query and search_files to inspect files" + (", or run_python for pandas inspection." if policy.inline_python else "."),
        f"- The harness enforces file ownership. You may write only: {', '.join(policy.writable)}; scratch space: outputs/.scratch/{name}/.",
        "- Tool output longer than about 12,000 characters is truncated; read long files in pages with offset/limit.",
        "- For JSON files (rules.json, analytics.json, history.json, audit-plan.json ...) use json_query to read exactly the part "
        "you need, e.g. `$` for the top-level keys, then `$.tests.split_purchases.overall`. It is one call instead of many page reads.",
        "- The human auditor uses the web interface, not this conversation. Do not ask questions and wait; if you are blocked, "
        "say so in your final report.",
        "- Write large files (over about 40 KB, such as rules.json or history.json) in parts from the start: the first part with "
        "write_file mode 'w', then the rest with mode 'a', each part well under 15,000 words. One response cannot hold a very large file.",
        f"- The current date and time is {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} (when this run's step began). "
        "Use it for any timestamp your instructions ask for.",
        "- When your work is complete, give your final report as plain text with no tool call. That ends your turn.",
    ]
    if policy.inline_python:
        lines.append("- run_python executes a short inspection snippet (pandas available). Print summaries only, never full datasets.")
    if name == "qa-reviewer":
        lines.append("- Until the challenger sends CHALLENGER DONE, challenge rows that are still open are expected. If the trace "
                     "check fails only because of open challenge rows, tell the planner in one line (no full failure report) and "
                     "run the full checks again on the next PACK READY. The run after PLANNER DONE and CHALLENGER DONE decides.")
    if name in PRE_EXISTING_CODE:
        lines.append(f"- {PRE_EXISTING_CODE[name]}")
    if policy.team:
        others = [t for t in TEAM if t != name]
        lines += [
            f"- You are a teammate in an agent team with `{others[0]}` and `{others[1]}`. The lead is the harness orchestrator.",
            "- Use send_message to talk to teammates or the lead. Messages sent to you arrive as your next turn. "
            "End your turn (reply without a tool call) whenever you are waiting for a message.",
        ]
    return "\n".join(lines)


def system_prompt(workspace: Path, name: str) -> str:
    claude_md = (workspace / "CLAUDE.md").read_text(encoding="utf-8")
    parts = [preamble(name), "", "=" * 20 + " CLAUDE.md (project instructions) " + "=" * 20, claude_md]
    if name == "extract-rules":
        skill = (workspace / ".claude/skills/extract-rules/SKILL.md").read_text(encoding="utf-8")
        parts += ["=" * 20 + " Skill: extract-rules (invoked by the human with /extract-rules) " + "=" * 20, strip_frontmatter(skill)]
        return "\n".join(parts)
    agent_md = (workspace / ".claude/agents" / f"{name}.md").read_text(encoding="utf-8")
    parts += ["=" * 20 + f" Your agent definition: {name} " + "=" * 20, strip_frontmatter(agent_md)]
    skill = AGENT_SKILLS.get(name)
    if skill:
        text = (workspace / ".claude/skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        parts += ["=" * 20 + f" Skill loaded: {skill} " + "=" * 20, strip_frontmatter(text)]
    return "\n".join(parts)
