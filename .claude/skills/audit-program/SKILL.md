---
name: audit-program
description: Build the auditor-approved procurement planning pack from risk judgments, reproducible samples, and source citations.
---

# Audit program

Read `CLAUDE.md`, the risk register, auditor comments, ratings, rules, analytics summary, and the relevant `analytics-full` headers before writing the plan. Run `python .claude/skills/audit-program/build_pack.py --gate` from the repository root. Stop if the gate fails; the auditor must resolve every decision and the risk assessor must render a revision.

The planner writes judgments to `outputs/audit-plan.json`. Use only approved risk IDs. For each risk provide `controls` with stable IDs, expected control text and `rules` (rule IDs); `tests` with stable IDs, `control_ids`, test step text, `citations` (for example `[rule:CPR-038]` or `[metric:$.tests.split_purchases.overall.windows]`), `sample` (`file`, `filter`, `sort`, `count`), and `pbc` strings. Put `memo` text at the top level; every numerical figure in that text uses `{{$.path}}`, with paths rooted in `analytics.json` or builder figures under `$.pack`. Add `not_tested_rules` as objects with `rule_id` and a specific reason for every rule absent from a risk, control, or test. The builder rejects missing citations, unmatched controls, unaccounted rules, and invalid sample recipes.

`sample.file` names one CSV in `outputs/analytics-full`. `filter` is a mapping from column to exact value; use `{}` for no filter. `sort` is a list of column names, with `-` for descending. `count` is the requested maximum; the builder raises it to at least the risk score band minimum (High/Extreme: 15, Moderate: 10, other: 5), capped by available rows. Select indicators for investigation, never describe them as breaches. Sample identifiers and resulting transactions are checked against `outputs/analytics-full/spend-clean.csv`, then written to `outputs/samples/<test-id>.csv`. The clean spend file must be produced from the approved cleaning script before a full pack build.

Run `python .claude/skills/audit-program/build_pack.py` after the plan is complete. Review the three rendered documents and `pack-figures.json`; send the pack to the challenger and QA teammates only when the builder succeeds. The audit program ends with the PBC list. Human sign-off still follows QA.
