---
name: extract-rules
description: Extract and verify testable procurement rules from the supplied West Berkshire Council Contract Rules PDF when a human invokes /extract-rules.
---

# Extract contract rules

Read `CLAUDE.md` before working. This skill is the human-invoked first stage: read `data/contract-rules.pdf` and write `outputs/rules.json`. It does not analyse spend or infer current law. Stop and report if the PDF, `pdftotext`, or verification is unavailable.

## Read and account for the source

Run `pdftotext data/contract-rules.pdf -` and `pdftotext -layout data/contract-rules.pdf -`. Read both renderings, with page boundaries preserved. The plain rendering helps with prose; `-layout` reveals Appendix A, B, and C table columns. Check the relevant rendered PDF page when the text order obscures a cell. Page numbers in output are one-based PDF page numbers.

Make an exhaustive clause ledger before writing rules. Account for every numbered clause and subclause in sections 1–11, including parent clauses, and every labeled row of Appendices A–C. Put each clause or appendix row in `rules` if it states a testable condition, action, approval, record, or prohibition; otherwise put it in `excluded` with a one-line reason. A clause may support multiple independently testable rules, each with its own ID; it is then covered once or more in `rules`. For parent clauses that only introduce subclauses, record an exclusion explaining that the child clauses carry the operative requirements. Appendix B footnotes and Appendix C's final unnumbered approval sentence also need ledger entries using `App B:footnotes` and `App C:final`. Use identifiers such as `App A:row1`, `App B:B1`, and `App C:D` for table rows; keep them distinct from prose clause numbers.

Distinguish a rule that can be checked against a contract file or procurement record from a rule inferable from spend. Do not promise that a spend indicator establishes compliance or wrongdoing. Capture contradictions and drafting gaps in `notes`, citing affected clause IDs and pages; preserve both readings without silently choosing one. In this PDF, pay particular attention to the unnamed `Threshold` values, VAT basis in clause 6.15 versus Appendix B, exact boundary gaps and overlaps in Appendices A and B, the £24,999.99 wording in 7.2, `Part []` placeholders, and Appendix C row E's above/below Threshold coverage.

## Output contract

Write one JSON object with exactly `parameters`, `rules`, `excluded`, and `notes` at the top level. `parameters` contains `goods_services_threshold`, `works_concessions_threshold`, and `light_touch_threshold`, each set to `null`: the PDF names these but prints no values. Do not import values from law, websites, memory, or other documents. If a rule refers to one of them, name it in the relevant bound's `parameter` field.

Each rule has:

- `rule_id`: `CPR-001`, `CPR-002`, ... in source order, without gaps.
- `clause`: the exact ledger identifier.
- `page`: one-based integer for the page containing its quote.
- `type`: one of `threshold`, `procedure`, `approval`, `record`, `prohibition`, `anti-avoidance`, `exception`, or `definition`.
- `scope`: concise description of the affected purchase or contract, including category where the source limits it.
- `thresholds`: an array of applicable value bands. Use an empty array when there is no value condition. Each band has `basis` (`contract_value`, `annual_value`, `payment_value`, or `other`) and `low` and `high` bounds. Each bound has `printed` (exact source wording, or `null` if unbounded), `gbp` (integer GBP amount or `null`), `inclusive` (boolean or `null`), and `parameter` (declared parameter name or `null`). Preserve `Above`, `more than`, `less than`, `up to`, and `or more` precisely. Never round a printed penny amount to an integer: retain `printed`, set `gbp` to `null`, and explain the precision limitation in `notes`. Do not convert a percentage such as the bond's 10% into a GBP threshold.
- `required_action`: the observable requirement or prohibition, in plain language.
- `approver`: the named approval authority, or `null` when none is stated; do not infer one.
- `evidence_source`: the record an auditor would request to test this rule, or `null` when no specific record is identifiable. This is distinct from the governing PDF, which is located by `page` and `verbatim_quote`.
- `verbatim_quote`: a continuous exact excerpt from the stated PDF page, preserving words and punctuation. Whitespace caused by line wrapping may be collapsed to single spaces. For table rules, quote a continuous cell excerpt that supports the action; capture the value band in the bound's `printed` field and verify it against the page.

Each `excluded` item has `clause`, `page`, and a one-line `reason`. Each `notes` item has `clauses` (nonempty list of ledger identifiers), `pages` (nonempty list of one-based page numbers), and `issue` (specific contradiction, ambiguity, or drafting gap). Avoid duplicate reasons and speculative interpretations.

## Verify and finish

Run `.venv/Scripts/python.exe .claude/skills/extract-rules/verify_quotes.py outputs/rules.json data/contract-rules.pdf` on Windows, or the equivalent venv Python path on another platform. The verifier checks structure, coverage of numbered clauses and appendix entries, rule IDs, parameter references, threshold representation, and each quote against its stated page. Correct extraction or source interpretation errors and rerun. If a printed quote cannot be recovered from text extraction, report the limitation; do not weaken the quote check or fabricate text.

Report the number of rules and exclusions, unresolved parameters, notable notes, and verification result. Keep source-derived statements as indicators or audit criteria for human review.
