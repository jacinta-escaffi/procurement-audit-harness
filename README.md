# Procurement Audit Planning Agent

A project that prepares a procurement audit planning pack for West Berkshire Council using public contract rules, spend data, contract notices, and committee papers. Claude reads the evidence and makes judgments; Python calculates figures, selects samples, and verifies the outputs. Findings are risk indicators for investigation, not allegations of wrongdoing.

`CLAUDE.md` is the project guide and full specification. Source files in `data/` are read-only.

## Skills

The three skills live in `.claude/skills/`:

| Skill | Purpose |
| --- | --- |
| `extract-rules` | Human-invoked with `/extract-rules`. Extracts testable procurement rules, source quotes, exclusions, and ambiguities into `outputs/rules.json`, then verifies them against the PDF. |
| `risk-assessment` | Used by the risk assessor to rate evidence-supported risks using the council's scoring method. Its Python builder produces the risk register and auditor decision template. |
| `audit-program` | Used by the planner to turn approved risks into controls, test steps, targeted samples, and evidence requests. Its Python builder checks the approval gate and renders the planning pack. |

## Agents

The six agents live in `.claude/agents/`:

| Agent | Responsibility |
| --- | --- |
| `spend-analyst` | Cleans the spend population and runs reproducible procurement risk tests, writing summaries and full indicator lists. |
| `findings-analyst` | Extracts cited audit history, follow-up results, risk themes, and the council's risk method from committee papers. |
| `risk-assessor` | Combines rules, spend indicators, and history into risk recommendations; applies the auditor's requested revisions. |
| `planner` | Builds the approved planning pack and responds to challenges and QA failures. |
| `challenger` | Reviews the pack for weak controls, tests, samples, and scope decisions; records how each challenge is addressed. |
| `qa-reviewer` | Checks quotes, figures, traceability, and sampled transactions; records the verdict and auditor sign-off. |

## How they work together

1. The auditor invokes `/extract-rules` to establish the criteria.
2. The spend analyst and findings analyst produce `analytics.json` and `history.json`.
3. The risk assessor prepares `risk-register.md`. The auditor records approval, rejection, or requested changes in `auditor-comments.md`; the risk assessor then builds the revision.
4. After the approval gate passes, the planner, challenger, and QA reviewer work as an agent team. They communicate directly, revise the pack, and complete the checks before presenting it for sign-off.
5. The auditor signs `outputs/review.md`.

Agent teams are enabled in `.claude/settings.json`. The final pack contains `planning-memo.md`, `risk-control-matrix.md`, `audit-program.md`, transaction samples, `challenges.md`, and `review.md` under `outputs/`.

## Running the project

Use Claude Code from the repository root, a Python virtual environment with `requirements.txt` installed, and Poppler's `pdftotext` available on the command path. The scripts use portable Python paths and can be set up on macOS.

With the virtual environment active, check the approval gate and pack with:

```sh
python .claude/skills/audit-program/build_pack.py --gate
python scripts/checks/run_checks.py
```

## Completion

The steps outlined in the guide have been completed: rules, analytics, history, the revised risk register, auditor decisions, the planning pack, and challenge responses are present. All five risks are approved, all recorded challenges are resolved, and all four pack checks pass.