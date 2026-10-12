"""Per-agent permissions: the harness enforces in code what the agent .md files ask for in prose.

Each agent's definition says which files it owns ("You own only outputs/history.json").
In Claude Code that is an instruction the model is trusted to follow. Here the harness
refuses any write outside the agent's own files, and runs only the scripts that agent
is meant to run.
"""
from dataclasses import dataclass, field
from fnmatch import fnmatch


@dataclass(frozen=True)
class Policy:
    writable: tuple = ()          # glob patterns, relative to the workspace root
    scripts: tuple = ()           # python scripts this agent may run
    pdftotext: bool = False       # may call pdftotext
    inline_python: bool = False   # may run short pandas snippets (inspection only)
    unreadable: tuple = ()        # glob patterns the agent may not read
    team: bool = False            # gets the send_message tool

    def can_write(self, rel: str, agent: str) -> bool:
        if rel.startswith(f"outputs/.scratch/{agent}/"):
            return True
        return any(fnmatch(rel, pattern) for pattern in self.writable)

    def can_read(self, rel: str) -> bool:
        return not any(fnmatch(rel, pattern) for pattern in self.unreadable)


SPEND_SCRIPTS = ("scripts/clean.py", "scripts/analyze_spend.py", "scripts/self_check_analytics.py", "scripts/profile_inputs.py")
CHECK_SCRIPTS = ("scripts/checks/run_checks.py", "scripts/checks/check_quotes.py", "scripts/checks/check_numbers.py",
                 "scripts/checks/check_trace.py", "scripts/checks/check_samples.py")

POLICIES = {
    # Skill run directly at the human's request (/extract-rules).
    "extract-rules": Policy(
        writable=("outputs/rules.json",),
        scripts=(".claude/skills/extract-rules/verify_quotes.py",),
        pdftotext=True,
    ),
    "spend-analyst": Policy(
        writable=("scripts/clean.py", "scripts/analyze_spend.py", "scripts/self_check_analytics.py",
                  "outputs/analytics.json", "outputs/analytics-full/*"),
        scripts=SPEND_SCRIPTS,
        inline_python=True,
    ),
    "findings-analyst": Policy(
        writable=("outputs/history.json", "outputs/committee-text/*"),
        scripts=("scripts/verify_history.py",),
        pdftotext=True,
    ),
    "risk-assessor": Policy(
        writable=("outputs/risk-ratings.json",),
        scripts=(".claude/skills/risk-assessment/build_register.py",),
    ),
    "planner": Policy(
        writable=("outputs/audit-plan.json",),
        scripts=(".claude/skills/audit-program/build_pack.py",),
        team=True,
    ),
    "challenger": Policy(
        writable=("outputs/challenges.md",),
        unreadable=("data/*", "outputs/analytics-full/*"),
        team=True,
    ),
    "qa-reviewer": Policy(
        writable=("outputs/review.md",),
        scripts=CHECK_SCRIPTS,
        team=True,
    ),
}

TEAM = ("planner", "challenger", "qa-reviewer")
