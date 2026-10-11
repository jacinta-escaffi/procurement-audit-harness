# CLAUDE.md: Procurement Audit Planning Agent (West Berkshire Council)

An agentic system that plans an internal audit of procurement for West Berkshire
Council from its public data: it turns the council's purchasing rulebook into
testable rules, analyses the full spend population against them, folds in past
audit history, rates the risks, and produces a complete, cross-checked audit
planning pack for a human auditor to approve.

This file describes the ENTIRE target system. Where this file is silent,
ask before inventing.

---

## Problem statement

Planning a procurement audit is slow, manual and mostly reading-based.
Auditors read the Contract Procedure Rules, skim past audit reports, and pick a
small random sample of payments without first analysing the full spend data.
Consequences:

1. **Slow**: reading a ~50-page rulebook and years of committee papers takes days.
2. **Random sampling misses risk**: 25 random payments out of ~15,000 will almost
   certainly miss the handful of suspicious ones.
3. **Inconsistent**: two auditors produce different risk assessments from the same
   material; ratings are judgment with no fixed method.
4. **Patterns invisible one-at-a-time**: split purchases, threshold-hugging and
   off-contract spend only show up when the whole population is analysed together.

## Objectives (full system)

1. Extract every testable rule from the Contract Procedure Rules with clause
   references and verbatim quotes.
2. Analyse the full spend population against those rules to surface risk
   indicators (threshold clustering, split purchases, off-contract spend,
   duplicates), computed by code, never by the LLM.
3. Summarise past audit coverage, open actions and registered risks.
4. Rate risks on a fixed likelihood × impact scale where every rating cites
   evidence, producing a risk register a human auditor approves (or rejects).
5. For approved risks, produce an audit program (risk → control → test step →
   targeted sample) and a planning memo.
6. Cross-check the whole pack with an agent team and automated scripts before
   auditor sign-off: quotes exist in sources, numbers match the data, every rule
   traces to a risk/control/test, every sampled transaction exists.

## Data sources (all public; downloaded manually into `data/`, read-only)

| File(s) | Source | Role |
| --- | --- | --- |
| `data/contract-rules.pdf` | WBC Contract Procedure Rules (Part 8, Sept 2025), council constitution | The criteria: thresholds, quote/tender rules, approvals, anti-splitting rule |
| `data/spend/*.xlsx` | "Expenditure over £500" page, monthly workbooks, sheet "Data to publish" (Phase 1: 3 months, ~3–4k rows each; header row position varies) | Full-population transactions. Columns: Service, Expenditure category, Narrative, Date, Net amount, Supplier name. Dates are datetimes (M/D/YY if text) |
| `data/contracts.csv` | Contracts Finder export: quoted phrase "West Berkshire Council", Awarded contract stage, notice status Open AND Closed, publication date 2021→today, no value filter (~147 notices; under the site's 1,000-row export cap). A second export for quoted "West Berkshire" may be appended to catch the short buyer-name variant | RAW notice export, not a clean register: see parsing rules in spend-analyst. Basis of the off-contract test |
| `data/committee/*.pdf` | Governance Committee papers: internal audit completed-work appendices (2024/25 annual + 2025/26 Q3), Internal Audit Plan 2025–28, Risk Management Strategy 2024–2027. NOTE: the Strategic Risk Register is an exempt (confidential) report and is NOT public: the scoring method comes from the Risk Management Strategy instead | Audit history and the council's risk-rating scale |
| (optional later) `data/pcard/*.csv` | Corporate procurement card spend, quarterly | Extra split-purchase surface |

## How the system works (full flow)

```
                    [contract-rules.pdf]
                            |
             (1) /extract-rules  ← SKILL, invoked by the human, no agent
                            |
                       rules.json
                            |
        +-------------------+--------------------+
        |                                        |
 (2) spend-analyst  ← SUB-AGENT          findings-analyst ← SUB-AGENT
     spend xlsx + contracts.csv              committee PDFs
     writes+runs Python (scripts/)           reads and sources
        |                                        |
   analytics.json                           history.json
        +-------------------+--------------------+
                            |
 (3) risk-assessor ← SUB-AGENT reads rules.json + analytics.json + history.json
     loads risk-assessment ← SKILL; writes risk-ratings.json; runs
     build_register.py → risk-register.md + auditor-comments.md
     main agent launches it and relays recommendations to the auditor
                            |
                    risk-register.md
                            |
 (4) HUMAN GATE: auditor approves risks and scope, or rejects → back to (3)
                            |
 (5) AGENT TEAM (post-approval):
       planner   : planning-memo.md, risk-control-matrix.md, audit-program.md
                    (uses audit-program ← SKILL)
       challenger: challenges.md; messages planner about weak spots
                    (low rating vs strong data signal, repeat finding out of scope)
       qa-reviewer: runs scripts/checks/*.py; writes review.md; blocks sign-off
                    until all checks pass
                            |
 (6) HUMAN GATE: auditor signs off the pack
```

Design principles, non-negotiable at every phase:
- **LLM reads and judges; Python computes every number.** No agent ever does
  arithmetic over the data itself.
- **Everything traceable.** Verbatim quotes verifiable against the PDF; every
  metric named and reproducible from a script; every rating cites a rule, a
  metric, or a prior finding.
- **Indicators, not accusations.** Real council, real data: outputs describe
  "risk indicators to investigate", never wrongdoing by the council, a
  department, or a supplier.
- **Context discipline.** Agents inspect data via .head()/.info()/aggregates
  only; full datasets never enter any context window. Big result lists go to
  files; JSON summaries carry top-20s and counts.
- **Missing/malformed input → stop and report.** Never fabricate.

## Repo structure

```
audit-planner/
  CLAUDE.md
  data/                          # inputs, read-only
    contract-rules.pdf
    spend/*.xlsx
    contracts.csv
    committee/*.pdf
  .claude/skills/
    extract-rules/SKILL.md
    risk-assessment/SKILL.md
    audit-program/SKILL.md
  .claude/agents/
    spend-analyst.md
    findings-analyst.md
    risk-assessor.md              (risk ratings, register build, auditor gate)
    planner.md                    (agent team)
    challenger.md
    qa-reviewer.md
  .claude/agent-memory/           # per-agent memory (how-to lessons only; never evidence)
  scripts/                       # Python written by spend-analyst
    checks/                      # QA scripts run by qa-reviewer: run_checks.py,
                                 #   check_quotes.py, check_numbers.py, check_trace.py, check_samples.py
  outputs/
    rules.json
    analytics.json
    analytics-full/              # full flagged-transaction lists
    history.json
    risk-register.md
    auditor-comments.md          # decision template written by risk-assessor; auditor fills it in
    audit-plan.json              # planner's judgments; build_pack.py renders the three files below
    planning-memo.md
    risk-control-matrix.md
    audit-program.md
    samples/                     # T-*.csv: the transactions each test pulls, drawn by build_pack.py
    pack-figures.json            # every figure build_pack.py computed, for the QA number check
    challenges.md
    review.md                    # QA results, verdict and the auditor's sign-off block
```

---

## Component specs

### Skill: `extract-rules` (human-invoked; Phase 1)

Converts `data/contract-rules.pdf` → `outputs/rules.json`. Array of:

```json
{
  "rule_id": "CPR-05",
  "clause": "5.2",
  "condition": "goods/services purchase, total value £10,001–£100,000",
  "threshold_low": 10001,
  "threshold_high": 100000,
  "required_action": "obtain three written quotations",
  "approver": "head of service",
  "verbatim_quote": "exact sentence(s) from the PDF"
}
```

- One entry per TESTABLE rule (threshold, required action, or approver);
  background text excluded.
- `verbatim_quote` copy-exact (verified by QA later). Amounts as GBP integers;
  open-ended thresholds null.
- Anti-aggregation / no-splitting rule flagged `"type": "anti-avoidance"`.
- Ambiguous/untestable clauses go in a trailing `"excluded"` array with a
  one-line reason, never guessed.

### Skill: `risk-assessment` (agent-loaded; Phase 1)

Defines `risk-register.md`:
- Likelihood 1–5 × impact 1–5 using the council's own scoring method as
  extracted into history.json from the Risk Management Strategy 2024–2027;
  if history.json reports no usable method, fall back to 1–5 Very low…Very
  high and state the fallback in the register header.
- Per risk: Risk · Likelihood (score + one-line justification) · Impact (score
  + justification) · Evidence · Proposed focus.
- HARD RULE: every justification cites ≥1 of {rule_id, named analytics metric,
  history item}. No citation → risk excluded.
- A repeat finding on the same theme raises likelihood by one point, stated
  explicitly.
- Indicator language only (see design principles).

### Sub-agent: `spend-analyst` (Phase 1)

In: rules.json, data/spend/*.xlsx, data/contracts.csv.
Out: outputs/analytics.json + full lists in outputs/analytics-full/.
Writes Python to scripts/ and runs it.

Cleaning spend workbooks (scripts/clean.py):
- Read sheet "Data to publish" from each workbook; the header row position
  varies, so locate the row containing the expected column names rather than
  assuming row 0.
- Assert expected columns exist in every monthly workbook before concat;
  stop with a clear message on mismatch.
- Dates may load as datetimes; if text, parse as M/D/YY (month first).
  Never auto-detect formats.
- Normalise supplier names: uppercase, strip punctuation, drop
  LTD/LIMITED/PLC/LLP suffixes, collapse whitespace. Apply the SAME
  normalisation to supplier names parsed from contracts.csv.
- Exclude non-procurement rows (statutory/inter-authority payments, e.g. LGPS
  pension deficit contributions; internal transfers). Log exclusions.

Parsing contracts.csv (it is a raw Contracts Finder notice export):
- Keep only rows where Organisation Name is "West Berkshire Council" or
  "West Berkshire" (keyword search pulls in other buyers whose notices
  mention the phrase). Log how many rows were dropped.
- The supplier column is a packed string, one or more bracketed blocks:
  `[Name|Address|Ref type|Ref Number|Is SME|Is VCSE][Name|...]`.
  Split on `][`, strip brackets, take the FIRST pipe-delimited element of
  each block as the supplier name → one (contract, supplier) row per block.
  Keep the Companies House number (4th element) where Ref type is
  COMPANIES_HOUSE, as an exact-match join key.
- Dates in contracts.csv are D/M/Y (e.g. 30/09/2028); spend dates are M/D/Y.
  Parse both with EXPLICIT formats; never auto-detect.
- Keep contracts active during the spend window: Contract end date on or
  after the window start (blank end date = keep). Awarded Value of 0.00 is
  common (frameworks/multi-lot) and does NOT mean "no contract".
- If total kept rows or any year's row count looks implausibly low, warn:
  the source export may have hit the 1,000-row download cap.

Tests (thresholds parameterised from rules.json, never hardcoded):
1. threshold_clustering: payments in [0.9 × threshold, threshold) per threshold.
2. split_purchases: same normalised supplier, ≥2 payments in a 30-day window,
   combined total crossing a threshold each single payment stays under.
3. off_contract: supplier 3-month total annualised (×4, labelled "annualised
   estimate") exceeds the tender threshold with no match in contracts.csv;
   exact matches (name or Companies House number) and near-miss names
   reported separately. IMPORTANT: Contracts Finder only publishes contracts
   above ~£25k, so suppliers below that will legitimately never appear in
   contracts.csv; the test therefore only flags suppliers ABOVE the
   tender/publication threshold, and results must never be used to question
   smaller suppliers.
4. duplicates: same supplier, same amount, within 7 days.
5. expired_notice_spend: only when contracts.csv is present: suppliers
   paid in the spend window whose every matching notice ended before the
   window started. Indicator of spend after expiry without an approved
   extension (App C row D); a newer contract may exist with no notice.
6. spend_vs_award: only when contracts.csv is present: for suppliers
   matched to one active single-supplier notice with Awarded Value > 0,
   ratio = annualised estimate ÷ (Awarded Value ÷ contract years); flag
   ratio ≥ 1.25. Indicator of spend beyond the award without an approved
   variation (App C row E); award values may be estimates or maxima.

analytics.json per test: parameters, counts, £ totals, top 20; full lists to
analytics-full/.

### Sub-agent: `findings-analyst` (Phase 1)

In: data/committee/*.pdf. Out: outputs/history.json:
- areas audited recently + assurance opinions (from the completed-work
  appendices and, if present, their covering reports),
- open/overdue agreed actions (theme, due date, status),
- any risk themes referenced in the audit papers (the Strategic Risk
  Register itself is exempt/not public; do not expect it, and note its
  absence in history.json),
- the council's risk scoring method (likelihood/impact scales, matrix,
  appetite): extract it from the Risk Management Strategy 2024–2027;
  if the strategy does not contain a usable scoring method, say so
  explicitly in history.json rather than inventing one.
Every item names its source document and page/section. Not in the papers → omitted.

### Skill: `audit-program` (agent-loaded; Phase 3)

Row format per approved risk: risk → expected control → test step → sample
approach + the specific flagged transactions to pull (from analytics-full/).
PBC (request) list derived from the test steps.

### Agent team (Phase 3)

- `planner`: owns planning-memo.md, risk-control-matrix.md, audit-program.md;
  works only on auditor-approved risks; uses the audit-program skill.
- `challenger`: owns challenges.md; messages planner about weak spots (risk
  rated low despite strong data signals; repeat finding excluded from scope);
  iterates until resolved or auditor overrules.
- `qa-reviewer`: runs scripts/checks/*.py and writes review.md:
  quote check (every verbatim_quote exists in the PDF), number check (every
  figure in the memo matches analytics outputs), traceability check (rule →
  risk → control → test complete; every high risk in scope or exclusion
  justified), sample check (every sampled transaction exists in the spend data).
  Sign-off blocked until all pass.

How the lead (the main session) runs the team. Agent teams are enabled by
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `.claude/settings.json`; the
three teammates are spawned from their files in `.claude/agents/` and
coordinate by messages, not by the lead relaying.

1. Gate check first: `.venv/bin/python .claude/skills/audit-program/build_pack.py --gate`.
   If it fails (a risk undecided, or the register not yet a revision), report
   and stop; the auditor completes `auditor-comments.md` and the risk-assessor
   is re-run.
2. Spawn `planner`, `challenger` and `qa-reviewer` as teammates with those
   names, each told the other two names and that the gate is closed. The
   planner starts at once; the other two wait for its `PACK READY v1`.
3. The team runs itself: challenger → planner challenges (two rounds at
   most), qa-reviewer → planner failures, planner rebuilds and re-announces.
   The lead answers questions and passes on nothing it was not asked.
4. When the lead has `PLANNER DONE`, `CHALLENGER DONE` and `QA DONE`, shut
   the three teammates down.
5. Report under 300 words: risks in scope, the three documents and the sample
   count, the challenges marked *for the auditor*, the QA verdict, and the
   request to complete the sign-off block at the end of `outputs/review.md`.
   `BLOCKED` means the pack goes back to the team, never to the auditor.

---
