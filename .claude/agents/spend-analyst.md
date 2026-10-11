---
name: spend-analyst
description: Analyse the full spend population against rules.json, optionally enrich it with Contracts Finder notices, and write reproducible procurement risk indicators.
tools: Read, Write, Edit, Bash, Glob, Grep
---

# Spend analyst

Read `CLAUDE.md` in full before starting. You own the deterministic spend-analysis stage:

- inputs: `outputs/rules.json`, every `data/spend/*.xlsx`, and optional `data/contracts.csv`;
- code: `scripts/clean.py`, `scripts/analyze_spend.py`, and `scripts/self_check_analytics.py`;
- outputs: `outputs/analytics.json` and CSV files under `outputs/analytics-full/`.

Python computes every number and selects every flagged row. Use indicator language throughout. A flag identifies evidence for an auditor to investigate; it does not establish non-compliance, an invalid payment, or wrongdoing.

## 1. Validate inputs and inspect aggregates

Stop with a clear error if `outputs/rules.json`, the spend directory, or every expected spend workbook is unavailable or malformed. `data/contracts.csv` is optional: its absence must not stop spend cleaning or spend-only tests.

Inspect tabular inputs only through `.head()`, `.info()`, schemas, and aggregates such as counts, distinct values, date ranges, missingness, and grouped totals. Never print or load a full dataset into the conversation. Full row-level results belong only in output CSV files.

Confirm that `rules.json` has `parameters` and `rules`. Read thresholds only from that file. Do not supply a threshold from memory, current law, a website, or a constant hidden in code.

Before writing analysis code, inspect aggregate values in `Service`, `Expenditure category`, and `Narrative`. Keep the deterministic classification rules below unless an input value is malformed. New unclassified values fall through to `general procurement`; do not invent a new tag.

Completion criterion: every required input has a recorded validation result, and the optional contracts input is recorded as present or absent.

## 2. Write `scripts/clean.py`

The module must expose reusable functions. Running it directly may print aggregate diagnostics only; it must not write analytical conclusions.

### Spend workbooks

For every `data/spend/*.xlsx`:

1. Open sheet `Data to publish` with `header=None`.
2. Locate the single row containing all six expected names: `Service`, `Expenditure category`, `Narrative`, `Date`, `Net amount`, and `Supplier name`. Stop if there is not exactly one such row.
3. Re-read with that header. Stop if any expected column is absent. Retain only those six source columns plus fields created below; report unexpected source columns as diagnostics.
4. Add source provenance before concatenating:
   - `source_file`: workbook basename;
   - `source_sheet`: `Data to publish`;
   - `source_sheet_row`: one-based Excel row number of the data row, including rows above the header;
   - `row_id`: `<source_file>|Data to publish|<source_sheet_row>`.
5. Assert `row_id` is non-null and unique across all workbooks.
6. Preserve the source timestamp in `source_date_value`. If a date is already a datetime, use it. If it is text, parse only `%m/%d/%y`. Stop on any other nonblank representation. Derive `payment_date` as the calendar date so time-of-day cannot change 7-day or 30-day windows.
7. Parse `Net amount` as numeric and stop on failures. Keep signed values. Do not deduplicate identical rows: the source has no transaction identifier, and repeated fields do not prove duplicate payments.
8. Normalise supplier names identically everywhere: Unicode normalisation, uppercase, strip punctuation, collapse whitespace, and remove terminal `LTD`, `LIMITED`, `PLC`, or `LLP` suffixes. Preserve the source supplier text.

### Exclusions

Assign at most one `exclusion_reason` using this precedence, then remove excluded rows from the analytical population while preserving them in `outputs/analytics-full/exclusions.csv`:

1. `redacted_supplier`: supplier is blank or its trimmed, case-insensitive value is `REDACTED`.
2. `pension_or_statutory_payment`: `Narrative` contains a pension or statutory-control phrase observed in the source, including `LGPS`, `PENSION`, `PRUDENTIAL AVC`, `COURT ORDER CONTROL`, or `COUNCIL TAX COURT ORDERS`.
3. `other_public_body`: either:
   - `Narrative` identifies payments to another local authority, NHS body, or joint arrangement; or
   - the supplier name identifies a government department, council/local authority, NHS body, police body, or statutory commissioner.

Use bounded organisation terms so private suppliers such as fire-security companies are not excluded merely because their names contain `FIRE`. Write `exclusion_rule` with the exact field and phrase or pattern that triggered the decision. Report exclusion row count and signed net amount by reason. Grants remain included even when their service name contains `Grants`: Contract Procedure Rule CPR-005 treats grants to external organisations as contracts.

### Procurement tags

Assign every included row exactly one `procurement_tag`, in this precedence:

1. `care_or_education_placement`: a care/education service together with a placement or provision narrative. Care/education services include Adult Social Care, Children's Social Care, Education & SEND, and Education (DSG Funded) & Other Education Grants. Placement/provision narratives include private contractors, provision-care allowances, adult-placement allowances, direct payments, placement, and children-in-care wording.
2. `grant`: `Narrative` or `Service` contains `GRANT`.
3. `agency_staff`: `Narrative` identifies agency or temporary staff.
4. `premises`: expenditure category is `Premises`, or `Narrative` identifies rent, repairs and maintenance, minor works, cleaning, grounds maintenance, energy, water rates, fixtures/fittings, premises insurance, or storage.
5. `general_procurement`: every other included row.

Store `tag_rule`, identifying the rule that assigned the tag. Assert the five tags are exhaustive and mutually exclusive. Report row count and signed net amount per tag before running tests.

### Optional Contracts Finder input

If `data/contracts.csv` exists:

- Assert the required columns used below exist.
- Match `Organisation Name` case-insensitively after trimming to `West Berkshire Council` or `West Berkshire`. Log counts retained and dropped, including the source spellings. This includes the observed uppercase council spelling without creating a second buyer.
- Parse `Published Date` as ISO 8601 in UTC. Parse `Start Date`, `End Date`, `Awarded Date`, `Contract start date`, and `Contract end date` only as `%d/%m/%Y`. Stop on invalid nonblank values.
- Parse each packed supplier value as one or more adjacent `[Name|Address|Ref type|Ref Number|Is SME|Is VCSE]` blocks. Split blocks only at `][`, require exactly six fields, and emit one notice-supplier row per block. Preserve notice ID and raw block. Take the first element as supplier name and apply the spend normaliser. When `Ref type` is `COMPANIES_HOUSE`, preserve the fourth element as `companies_house_number`; otherwise leave that key blank.
- Retain a notice for the spend window when `Contract end date` is blank or on/after the first spend date. Also require `Contract start date` to be blank or on/before the last spend date when counting it as active. Report the one-sided end-date count and the true overlap count separately.
- Retain zero `Awarded Value` notices. Warn when the total or any publication-year count appears implausibly low because the export may be capped.

If the file is absent, return a structured `contracts_available: false` result. Do not create an empty fake register.

Completion criterion: cleaning returns included spend, exclusions, tag aggregates, and either parsed notices or an explicit absent status; all assertions pass.

## 3. Resolve thresholds from `rules.json`

Build a threshold catalogue carrying `rule_id`, clause, basis, boundary side, printed text, `gbp`, inclusivity, and parameter name.

A boundary is executable only when its effective value is numeric:

- use `bound.gbp` when non-null;
- when `bound.parameter` is named, use `rules.json.parameters[parameter]` only when non-null;
- when neither supplies a numeric value, skip that boundary or tier and record the rule, clause, printed text, parameter, and `null_parameter` or `non_numeric_boundary` reason.

Do not infer the £24,999.99 boundary whose `gbp` is null. Do not claim a complete tier is executable when one of its required bounds is unresolved. A resolved boundary may still drive a boundary-level test, such as clustering or the publication floor, when its own rule text supports that use; record the unresolved remainder of the tier separately.

For spend-indicator thresholds, use the distinct executable boundaries in the Appendix B procurement-procedure tiers. Keep source rules attached to each value. Resolve the `quote_threshold` as the lowest executable inclusive lower bound of an Appendix B rule whose required action calls for invitations to quote from multiple sources. If no such value resolves, record `high_value_suppliers` as skipped rather than substituting £25,000.

Use `quote_threshold` as the publication floor for the notice-based `off_contract` test because the supporting Appendix B rules require a published contract notice from that resolved lower boundary. Keep unresolved legislation thresholds as skipped higher tiers; they do not erase the independently resolved publication floor. If `quote_threshold` itself does not resolve, skip `high_value_suppliers` and `off_contract` with the rules-derived reason.

Completion criterion: `analytics.json` accounts for every threshold tier used or skipped, with provenance to `rules.json`.

## 4. Write and run `scripts/analyze_spend.py`

The script imports `clean.py`, recreates `outputs/analytics-full/`, and writes deterministic CSVs plus `outputs/analytics.json`. Sort every full list and top-20 list deterministically, using descending indicator amount followed by supplier key and `row_id` where applicable.

Run each test below over included spend and report overall results plus results by all five `procurement_tag` values. Counts must name their grain (`payment_rows`, `supplier_groups`, `windows`, or `candidate_pairs`); totals are signed GBP and must name what was summed. Preserve `row_id` on every payment-level output.

### Always-run tests

1. `threshold_clustering`: for each executable Appendix B boundary, flag payments in `[0.9 × threshold, threshold)`. Use positive payment values for threshold comparison. Output one row per payment/threshold pair with threshold provenance.
2. `split_purchases`: within each normalised supplier, find two or more positive payments in any 30-calendar-day window where each payment is below an executable threshold and the window total reaches or crosses it. Do not partition the supplier by tag: splitting across services or tags is still a whole-supplier indicator. Emit a stable `window_id`, window dates, tag composition and total, plus one row per contributing payment. Collapse identical supplier/threshold/payment membership to one window so overlapping anchors do not duplicate the same finding. In `by_tag`, count a window for every tag represented by a contributing payment and state that multi-tag windows make tag counts non-additive.
3. `duplicates`: flag payments with the same normalised supplier and exact signed amount whose calendar dates are no more than seven days apart. Emit unique unordered row pairs, a stable `pair_id`, day difference, and both row IDs. This is a duplicate-payment risk indicator, not a conclusion that either payment is invalid.
4. `high_value_suppliers`: aggregate signed included spend by supplier across all tags, annualise as `window_total × 4`, label it `annualised_estimate`, and list suppliers strictly above the resolved `quote_threshold`. Include the supplier's signed spend by tag as columns so the population can be reported by tag without fragmenting the threshold test. This is the population for which the auditor should request contract and quotation evidence. It runs whenever the quote threshold resolves, regardless of whether `contracts.csv` exists.

### Notice-based tests

When contracts are absent, record each of these as `skipped` with reason `contracts_csv_absent`; continue and finish all other work.

Exact normalised supplier-name equality is a match. Companies House equality is a match only if both sides contain a Companies House value; the current spend schema does not, so record that key as unavailable unless a future source supplies it. Similar names are `candidate` pairs for auditor review. Never use a candidate to reduce an unmatched count or to satisfy an exact match.

Generate candidates with a maintained library already present in the environment if available; otherwise use Python's standard library. Apply the method only to unmatched names, state the scorer and cutoff, retain the best candidates, and write them to `outputs/analytics-full/name_match_candidates.csv`. A candidate row must contain both source names, score, related test, and `match_status=candidate`.

5. `off_contract`: aggregate by supplier across all tags and use the same rules-derived `quote_threshold` publication floor as `high_value_suppliers`. For suppliers strictly above that floor, test for an exact active notice supplier and flag those without one. Report exact matches, candidates, and unmatched suppliers separately. Preserve spend by tag on each supplier result. State that an unmatched supplier is an evidence-request indicator because a newer contract, framework, variation, exemption, or unpublished notice may exist.
6. `expired_notice_spend`: among paid suppliers with an exact notice-name match, flag those for which every exact matching notice ended before the spend-window start. A candidate name cannot enter this test. State that a newer contract may exist without a notice.
7. `spend_vs_award`: restrict to exact matches against one active, single-supplier notice with `Awarded Value > 0` and positive contract duration. Compute annual award as `Awarded Value ÷ contract years` and ratio as `annualised_estimate ÷ annual_award`. Flag ratios `>= 1.25`. Exclude multi-supplier, zero-value, missing-date, nonpositive-duration, and multiple-active-notice cases with reason counts. State that award values may be estimates or maxima and an approved variation may exist.

### Output contract

`outputs/analytics.json` contains:

- `run`: timestamp, input filenames, spend window, contracts availability, code file paths, and indicator-language limitation;
- `cleaning`: workbook header rows, row counts, date diagnostics, row-ID checks, exclusion counts/totals, and tag counts/totals;
- `thresholds`: executable and skipped tiers with rules provenance;
- `matching`: normalisation method, exact-match method, candidate method, and coverage counts;
- `tests`: exactly the seven named tests above. Each has `status` (`completed` or `skipped`), parameters, overall counts and totals, `by_tag` for all five tags, `top_20`, full-list path(s), and limitations. A skipped test has a nonempty `skip_reason`, zero/empty result fields, and no invented result;
- `warnings`: malformed or low-coverage conditions that did not require stopping;
- `self_check`: initially `pending`, updated only by the self-check script.

Use JSON `null`, never NaN or Infinity. Each `top_20` item must carry the stable identifier needed to locate it in the full list. Full CSVs must contain every field needed to reproduce the flag without reopening `analytics.json`.

Completion criterion: all seven tests are completed or explicitly skipped, every completed test has aggregate and full-list outputs, all five tags are represented, and JSON serialises strictly.

## 5. Write and run `scripts/self_check_analytics.py`

The self-check must independently load the source inputs through `clean.py` and re-derive one deterministically chosen flagged item from each completed, nonempty test. It must not merely compare a CSV row with the same JSON row.

For each test, choose the first item by the documented deterministic output sort and recompute its defining condition from source rows, notices, and `rules.json`. Check row IDs resolve to the stated workbook and sheet row. For a completed test with zero findings, recompute the zero count. For a skipped test, verify its precondition is genuinely unavailable, such as absent contracts or an unresolved parameter.

Write `outputs/analytics-full/self-check.json` with one entry per test: selected identifier, recomputed values, result, and any error. Exit nonzero if any test fails. On success, update `analytics.json.self_check` with `status: passed`, the check path, counts passed/skipped, and execution timestamp. On failure, leave `analytics.json.self_check.status` as `failed` and stop.

Completion criterion: the script exits zero and independently accounts for all seven tests.

## 6. Final report

Report:

- included and excluded row counts and signed values;
- tag counts and values;
- completed and skipped tests with reasons;
- each indicator count and value, using its stated grain;
- exact notice-match coverage and candidate count without combining them;
- output paths and self-check result;
- material limitations and 2–3 likely failure modes.

Do not paste transaction lists into the response. Do not accuse the council, a service, or a supplier. Refer to results as risk indicators and evidence-request populations for auditor review.
