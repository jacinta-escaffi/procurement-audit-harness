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

---

# Part B: Independent agent harness

In Part A, Claude Code was the harness: it gave the agents their tools, ran the agent loop, enforced permissions and carried messages between teammates. Part B replaces Claude Code with our own harness, in `harness/`. It runs the same agents and skills as a web application that works without anyone's local machine.

**Deployed app:** https://procurement-audit-harness.onrender.com/

## What the harness provides

The harness implements each layer of the agent-harness model from class:

| Layer | Part A (Claude Code) | Part B (`harness/`) |
| --- | --- | --- |
| LLM | Claude inside Claude Code | Claude API, called directly (`llm.py`) |
| Tools | Read, Write, Edit, Glob, Grep, Bash | `list_files`, `read_file`, `json_query`, `write_file`, `edit_file`, `search_files`, `run_command`, `run_python`, `send_message` (`tools.py`). `json_query` reads one part of a JSON file in a single call, so agents don't page through large files |
| Agent loop | Built in | `agent_loop.py`: the LLM picks an action, the harness runs it, the result goes back, and this repeats until the LLM stops calling tools |
| Permissions | Approval prompts, plus "you own only X" in the prompts | Enforced in code (`permissions.py`): each agent can write only its own files and run only its own scripts. The challenger cannot read `data/` at all |
| Memory | Files in `outputs/` | The same files, in a separate workspace per run, plus a saved transcript for each agent |
| Context management | Built in | Long tool outputs are truncated, and older tool results are dropped once the context passes 150,000 tokens (late, because trimming forces the cache to be rebuilt). The system prompt, the tool list and the growing conversation are all prompt-cached, so each step re-reads earlier history at the cached rate |
| Hooks | Settings | After each stage, the harness re-runs the project's own verification scripts and records every tool call in the activity log |
| Orchestration (the lead) | The main Claude Code session | `orchestrator.py`: a state machine with two human gates and a message bus for the agent team |

### The agents and skills are reused unchanged

Each agent's instructions are built from the Part A files (`prompts.py`): a short harness preamble, then `CLAUDE.md`, then the agent's `.claude/agents/<name>.md`, then any skill it loads (`.claude/skills/<skill>/SKILL.md`). The Python builders and checkers in `.claude/skills/` and `scripts/` are the tools those agents run.

### The workflow

1. **Extract rules.** The auditor invokes `/extract-rules` (a skill, not a sub-agent). The harness then verifies every quote with `verify_quotes.py`.
2. **Analyse.** `spend-analyst` and `findings-analyst` run in parallel. Their outputs pass only if `self_check_analytics.py` and `verify_history.py` both pass.
3. **Rate risks.** `risk-assessor` loads the `risk-assessment` skill and builds the register.
4. **Human gate 1.** The auditor approves, rejects or requests a change for each risk in the web form, and the form writes `auditor-comments.md`.
   - **Change:** the risk-assessor runs again in post-gate mode. The auditor then reviews the revised risk.
   - **Approve or reject only:** no judgement is needed, so the harness applies the decisions and rebuilds the register with `build_register.py`.
   - **Either way:** `build_pack.py --gate` must pass before planning can start.
5. **Agent team.** `planner`, `challenger` and `qa-reviewer` coordinate through `send_message`; the orchestrator is the message bus and the lead.
   - It delivers each agent's messages on that agent's next turn and nudges an agent once if the team goes quiet.
   - It stops when it has `PLANNER DONE`, `CHALLENGER DONE` and `QA DONE`.
   - It then re-runs `run_checks.py` itself and doesn't take the QA agent's word for it. A `BLOCKED` pack goes back to the team and never reaches the auditor.
6. **Human gate 2.** The auditor signs off in the web form, which writes the sign-off block in `review.md`. The full pack can be downloaded as a zip.

Every automated stage can run in one of two modes:
- **Run with agents:** the full workflow, run by the LLM agents.
- **Replay Part A output:** restores this repo's Part A outputs and re-runs the deterministic Python, with no API cost. This is useful for demos or for jumping straight to a gate.

### Files

```
harness/
  app.py           FastAPI web API and static UI
  orchestrator.py  stages, human gates, agent-team message bus, verification hooks
  agent_loop.py    the agent loop and context management
  tools.py         tool schemas and their safe execution
  permissions.py   per-agent write, read and script permissions
  prompts.py       builds system prompts from CLAUDE.md + agent .md + SKILL.md
  llm.py           Claude API client (streaming, prompt caching)
  store.py         run workspaces, state.json, events.jsonl
  config.py        settings from environment variables
  static/          the single-page UI (plus bundled marked and DOMPurify)
tests/test_harness.py   end-to-end tests with a scripted fake LLM (no API calls)
Dockerfile, render.yaml  deployment
```

## Run locally

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # also needs poppler (pdftotext): brew install poppler
export ANTHROPIC_API_KEY=sk-ant-...      # optional; without it only replay mode works
export APP_ACCESS_CODE=choose-a-code     # optional locally
uvicorn harness.app:app --reload         # open http://localhost:8000
python -m pytest -q                      # 23 tests, including the harness tests
```

## Deploy on Render

1. Push this repo to GitHub.
2. In Render, choose **New → Web Service**, connect the repo, and set **Language/Runtime** to **Docker**. Render finds the `Dockerfile`. Alternatively, **New → Blueprint** reads `render.yaml`.
3. Add two environment variables:
   - `ANTHROPIC_API_KEY`: your API key.
   - `APP_ACCESS_CODE`: a code you share with graders. Starting runs and recording decisions require it, so strangers can't spend your API credit.
   - `ANTHROPIC_WORKSPACE_ID` (optional): only needed if your API key is not scoped to a workspace. The API then requires this header.
4. Set the health check path to `/healthz` and deploy.

**Hosting notes:**
- A full agent run takes tens of minutes. On Render's free plan, a service with no incoming traffic for 15 minutes spins down, and its local files are lost on spin-down or redeploy. The open page polls the server every few seconds, so keep the page open while a run is in progress.
- A paid instance avoids spin-down. Add a persistent disk and set `HARNESS_RUNS_DIR` to a path on it if you want runs to survive redeploys.

## Design notes and limits

- **Fixes from live runs on Render.**
  - **History field names.** The findings-analyst once named the likelihood and impact scale fields differently from what the register builder expects. Its definition now names the fields, the history verifier rejects any other names, and the builder accepts both.
  - **Time limit.** Commands get 15 minutes, because the spend analysis takes about 7 minutes on Render's free plan.
  - **Instructions.** Agents are told the date and told to write large files in parts.
  - **Turn-taking.** Agent-team turns go to whichever teammate has waited longest.

- **Cost control.** Each agent step resends that agent's history, so the harness caches the conversation as it grows. A **Stop** button ends a running stage before the agents' next model call. A first version without conversation caching cost about $10 for a run with the risk and team stages live.

- **Python computes, the LLM judges.** The harness never asks an agent for a number it could compute. Every figure comes from the project's scripts.
- **Existing Part A code.** The spend-analyst's scripts already exist in the repo, so the harness tells it to review and run them rather than rewrite working code.
- **Part A fix.** `scripts/analyze_spend.py` now also writes `outputs/analytics-full/spend-clean.csv`. Part A's `build_pack.py` and QA sample check require this file, but no committed script produced it. The regenerated file is identical to the Part A copy.
- **Inline Python.** `run_python` (spend-analyst only) runs inspection snippets in the run's workspace. The subprocess never receives the API key, but this is not a full sandbox. The access code is the main safeguard on a public deployment.