---
name: risk-assessment
description: Rate procurement risk indicators against the council matrix, verify evidence, and prepare the auditor decision gate.
---

# Risk assessment

Read `CLAUDE.md`, `outputs/rules.json`, `outputs/analytics.json`, and `outputs/history.json` before judging. Write judgments only to `outputs/risk-ratings.json`; run `python .claude/skills/risk-assessment/build_register.py` from the repository root to produce the register and decision template. Use the project virtual environment's Python on either macOS or Windows. Stop if any source is absent or malformed.

## Evidence and ratings

Use existing `rule_id` values as `[rule:CPR-001]`. Cite analytics scalar values by exact JSON path, such as `[metric:$.tests.split_purchases.count]`; inspect the JSON before choosing a path. Audit history has no source IDs: cite zero-based array positions as `[history:audit_coverage:0]` or `[history:follow_ups:1]`. The builder validates those positions and displays the cited audit title, opinion, document, and page. Cite a source in **each** likelihood and impact justification. Use `{{$.tests.split_purchases.count}}` for every numerical figure in prose; the builder inserts the value. Do not type figures, including amounts and percentages, into narrative text. The rating fields themselves are integers because the builder needs them for the matrix.

Use current or **Actual** risk, based on observed indicators and existing controls only. An indicator is not an established breach. For likelihood, use the council's twelve-month scale (Rare, Unlikely, Likely, Almost Certain, Certain). Apply this operational rubric consistently: 1 = no current indicator and strong contrary evidence; 2 = isolated indicator or credible exposure without corroboration; 3 = repeated or multi-supplier indicator, or corroborated control weakness; 4 = pervasive indicator across the assessed population with corroborated weakness; 5 = occurrence is established for the defined risk event within the period. These are decision anchors, not invented probability estimates. If evidence cannot distinguish adjacent levels, choose the lower and state the uncertainty. A same-theme repeat finding with an adverse audit opinion raises the **base** likelihood by one, capped at 5; cite that audit in `repeat_finding.history` and explain the thematic link. Adverse means Limited Assurance or Weak, not Reasonable/Substantial or an advisory report without an opinion.

Use the council impact scale in `history.json` across financial, people, assets, reputation, and compliance; choose the highest supported dimension and name it. Do not treat flagged spend as a proven loss. The source's Medium financial percentage overlaps Major; use the printed GBP boundary at £500k for the financial dimension and disclose this conflict. The matrix's 25 cells control score and band. Its `High` label differs from the response table's `Medium-High` for scores 8–12; show both in the register header, use matrix `High`, and do not silently rename it. The source also disagrees on the Corporate Risk Register threshold; do not infer escalation from the rating.

## JSON contract

Write an object with `risks`, and on a post-gate run also `revision`. Each risk has unique stable `id`, `risk`, `likelihood` (base integer 1–5 and `justification`), `impact` (integer 1–5 and `justification`), `evidence` (text with citations), and `proposed_focus` (text). Optional `repeat_finding` has `history` (one history tag) and `reason` (same-theme explanation). Example:

```json
{"risks":[{"id":"R-01","risk":"Procurement threshold indicators warrant review","likelihood":{"base":2,"justification":"The indicator appears in {{$.tests.split_purchases.count}} windows [metric:$.tests.split_purchases.count]."},"impact":{"score":2,"justification":"The relevant approval rule creates a compliance exposure [rule:CPR-001]."},"evidence":"[rule:CPR-001] [metric:$.tests.split_purchases.count]","proposed_focus":"Inspect the flagged transactions and approval records."}]}
```

`likelihood.base` is pre-uplift; the builder computes the final likelihood. On a post-gate run, set `revision` to `{"changes":[{"id":"R-01","fields":["likelihood.base"],"reason":"Auditor requested a different likelihood"}]}`. List every changed risk field (dot paths). The builder compares this declaration to the saved pre-gate snapshot and auditor decisions. A rejection removes the risk; an approval leaves it unchanged; a Change decision requires a comment describing the requested change and an exact matching revision entry. If an auditor decision is unapplied, an unrequested field changes, or a risk is added, the builder fails. Preserve the auditor's own decisions and comments in `outputs/auditor-comments.md`.

## Gate

First run produces `outputs/risk-register.md` and an `outputs/auditor-comments.md` table with blank Decision and Comment cells. The auditor enters `Approve`, `Reject`, or `Change` and a comment for Change. Re-run the risk assessor after the auditor responds; the builder validates the revision and writes a header naming each requested change and any override. The register ends with **Auditor decisions**. A missing or unresolved citation in either justification excludes that risk and is reported. Fix excluded risks in the judgment JSON and rebuild before presenting the gate.
