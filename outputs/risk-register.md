# Procurement risk register

Ratings describe risk indicators for auditor investigation, not established non-compliance.

Scoring: current (Actual) likelihood × impact. The 25-cell council matrix in `history.json` supplies the score and displayed band. Its High label is used for cells where the response table says Medium-High (scores 8–12); the response-table wording is retained here as a source conflict. The strategy also conflicts on the Corporate Risk Register threshold (Extreme 15–25 versus score 9 or above), so this register makes no escalation decision. For the overlapping Medium/Major financial impact ranges, the printed £500k boundary is used; Section 4.3's Medium (2) conflicts with Table 1's Medium (3), and Table 1 controls the impact label.

## Revision after auditor gate


## R-01: Purchasing may be split or timed around procurement thresholds, so the required competition and approval route may not be applied.

- **Likelihood:** 3 Likely (base 3). Repeated indicators appear across 1380 overlapping windows and 338 payment-threshold pairs [metric:$.tests.split_purchases.overall.windows] [metric:$.tests.threshold_clustering.overall.payment_threshold_pairs]. The window method can count recurring expenditure more than once, so the lower adjacent rating is used.
- **Impact:** 3 Medium. The anti-avoidance requirement creates a compliance exposure if the indicator reflects artificial disaggregation; the Medium compliance dimension is used because the data supports investigation but not an established breach [rule:CPR-038].
- **Score and band:** 9 High.
- **Evidence:** The rules prohibit artificial splitting [rule:CPR-038]. The analytics identify 1380 overlapping windows and 338 payment-threshold pairs [metric:$.tests.split_purchases.overall.windows] [metric:$.tests.threshold_clustering.overall.payment_threshold_pairs].
- **Proposed focus:** Inspect distinct suppliers and transactions from the flagged populations against procurement plans, aggregation decisions, quotation or tender records, and approvals.

## R-02: Potential duplicate payments may not be prevented or recovered promptly.

- **Likelihood:** 3 Likely (base 3). The analysis identifies 4245 same-supplier, same-amount candidate pairs within the defined review interval [metric:$.tests.duplicates.overall.candidate_pairs]. Recurring legitimate payments may create matches, so the lower adjacent rating is used.
- **Impact:** 3 Medium. The candidate-pair amount is 27339273.26, supporting the Medium financial dimension as an exposure population rather than a proven loss [metric:$.tests.duplicates.overall.signed_pair_amount_gbp].
- **Score and band:** 9 High.
- **Evidence:** There are 4245 candidate pairs with a signed pair amount of 27339273.26 [metric:$.tests.duplicates.overall.candidate_pairs] [metric:$.tests.duplicates.overall.signed_pair_amount_gbp].
- **Proposed focus:** Test the candidate pairs against invoice identifiers, credit notes, recurring-payment schedules, approval evidence, and recovery records.

## R-03: High-value supplier spend may lack an evidenced active procurement notice or compliant route to market.

- **Likelihood:** 3 Likely (base 3). No exact active notice was found for 641 high-value supplier groups, compared with 34 exact active matches [metric:$.tests.off_contract.overall.supplier_groups] [metric:$.tests.off_contract.overall.exact_active_matches]. The export, aliases, frameworks, and unpublished records may explain unmatched suppliers, while the recent Contract Letting audit gave Reasonable Assurance [history:audit_coverage:0]; these uncertainties support the lower adjacent rating.
- **Impact:** 4 Major. Applicable purchases require procurement compliance and the relevant quotation or notice route, creating a potentially Major compliance consequence if a compliant route cannot be evidenced [rule:CPR-025] [rule:CPR-108]. The unmatched spend is an indicator population, not a proven loss or breach.
- **Score and band:** 12 High.
- **Evidence:** The unmatched population has 641 supplier groups and signed window spend of 62932762.51 [metric:$.tests.off_contract.overall.supplier_groups] [metric:$.tests.off_contract.overall.signed_window_total_gbp]. Exact active matches and similar-name candidates are reported separately [metric:$.tests.off_contract.overall.exact_active_matches] [metric:$.tests.off_contract.overall.candidate_pairs].
- **Proposed focus:** Reconcile the unmatched high-value suppliers to the council contract register, frameworks, exemptions, waivers, aliases, and procurement portal records before testing compliance.

## R-04: Spend may have continued after recorded contract expiry without an evidenced approved extension or replacement arrangement.

- **Likelihood:** 2 Unlikely (base 2). The indicator is limited to 7 supplier groups with exact-name notice matches [metric:$.tests.expired_notice_spend.overall.supplier_groups]. A newer contract or approved extension may exist outside the notice export, so the isolated-indicator anchor is used.
- **Impact:** 3 Medium. The signed window spend associated with the indicator is 190366.92, supporting the Medium financial dimension as exposure rather than proven loss; extensions also require approval [metric:$.tests.expired_notice_spend.overall.signed_window_total_gbp] [rule:CPR-116].
- **Score and band:** 6 Moderate.
- **Evidence:** The analysis identifies 7 supplier groups and 190366.92 of signed window spend where every exact matching notice ended before the spend window [metric:$.tests.expired_notice_spend.overall.supplier_groups] [metric:$.tests.expired_notice_spend.overall.signed_window_total_gbp].
- **Proposed focus:** Obtain current contracts, extension clauses, approval records, replacement procurement evidence, and payment dates for the flagged suppliers.

## R-05: Supplier spend may exceed the recorded award profile without an evidenced approved variation.

- **Likelihood:** 2 Unlikely (base 2). The comparison flags 7 supplier groups after restricting the test to one active single-supplier notice with a usable award value [metric:$.tests.spend_vs_award.overall.supplier_groups]. Award values may be estimates or maxima, so the isolated-indicator anchor is used.
- **Impact:** 4 Major. The associated signed window spend is 633768.19, which crosses the printed boundary used for the Major financial dimension; this is exposure, not proven loss, and variations require prior approvals [metric:$.tests.spend_vs_award.overall.signed_window_total_gbp] [rule:CPR-117].
- **Score and band:** 8 High.
- **Evidence:** The analysis identifies 7 supplier groups and 633768.19 of signed window spend under its award-profile comparison [metric:$.tests.spend_vs_award.overall.supplier_groups] [metric:$.tests.spend_vs_award.overall.signed_window_total_gbp].
- **Proposed focus:** Compare the flagged spend with contract scope, duration, award basis, indexation, approved variations, and cumulative contract ledger values.

## Auditor decisions

Record Approve, Reject, or Change and any requested change in `outputs/auditor-comments.md`.

| Risk ID | Decision | Comment |
| --- | --- | --- |
| R-01 | Approve | Repeated threshold indicators justify targeted testing; overlapping windows are already acknowledged. |
| R-02 | Approve | Change: impact rating inconsistent with the £500k Major boundary applied elsewhere (R-05). Re-assessed: £27.3M is an overlapping candidate-pair exposure population, not a quantified loss, so Medium is retained. |
| R-03 | Approve | Retain reconciliation testing and the revised compliance impact justification. |
| R-04 | Approve | Exact matches to expired notices justify checking extensions and replacement contracts at the cautious current rating. |
| R-05 | Approve | Retain the revised impact assessment based on potential exposure. |
