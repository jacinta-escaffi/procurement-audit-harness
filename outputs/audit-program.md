# Audit program

Indicators require evidence and auditor judgment; they do not establish non-compliance.

## R-01: Purchasing may be split or timed around procurement thresholds, so the required competition and approval route may not be applied.

### T-01A

Control: C-01. For flagged supplier windows, reconcile the constituent payments to the underlying requirement, procurement strategy, aggregated contract-value calculation, quotation or tender route, and approvals. Treat recurring legitimate purchases as a possible explanation. The selected windows prioritize two-payment candidates, then higher window values, to broaden supplier coverage across the repeated-window population. [rule:CPR-038] [metric:$.tests.split_purchases.overall.windows]

Sample: 15 indicators, 29 verified transactions from `split_purchase_windows.csv`. See `samples/T-01A.csv`.

### T-01B

Control: C-01. For payments near a printed threshold, compare full contract value and linked payments with the applicable quoted route; establish whether the payment is part of a larger purchase. [rule:CPR-037] [metric:$.tests.threshold_clustering.overall.payment_threshold_pairs]

Sample: 15 indicators, 15 verified transactions from `threshold_clustering.csv`. See `samples/T-01B.csv`.

## R-02: Potential duplicate payments may not be prevented or recovered promptly.

### T-02

Control: C-02. Compare each candidate pair's invoice numbers, source invoices, credit notes, recurring-payment schedules, approval trail and any recovery record; establish whether two distinct valid liabilities exist. [metric:$.tests.duplicates.overall.candidate_pairs]

Sample: 15 indicators, 24 verified transactions from `duplicates.csv`. See `samples/T-02.csv`.

## R-03: High-value supplier spend may lack an evidenced active procurement notice or compliant route to market.

### T-03

Control: C-03. Use the sampled payment as an anchor. Obtain each sampled supplier's complete spend ledger for the three-month window, reconcile the supplier total and procurement category, then compare the current council contract register, frameworks, aliases, waivers and portal records before assessing the applicable route and publication. [rule:CPR-108] [metric:$.tests.off_contract.overall.supplier_groups]

Sample: 15 indicators, 15 verified transactions from `off_contract.csv`. See `samples/T-03.csv`.

## R-04: Spend may have continued after recorded contract expiry without an evidenced approved extension or replacement arrangement.

### T-04

Control: C-04. For each flagged supplier, use the sampled payment as an anchor, obtain the complete payment sequence across the spend window, and compare all payment dates with executed contract terms, any replacement contract, extension clause and S151 Officer approval; resolve notice-export gaps before concluding. [rule:CPR-116] [metric:$.tests.expired_notice_spend.overall.supplier_groups]

Sample: 7 indicators, 7 verified transactions from `expired_notice_spend.csv`. See `samples/T-04.csv`.

## R-05: Supplier spend may exceed the recorded award profile without an evidenced approved variation.

### T-05

Control: C-05. Compare the flagged annualised estimate with actual cumulative contract spend, original award basis, scope, indexation, term and approved variations; establish the applicable threshold and approval route. [rule:CPR-117] [metric:$.tests.spend_vs_award.overall.supplier_groups]

Sample: 7 indicators, 7 verified transactions from `spend_vs_award.csv`. See `samples/T-05.csv`.

## PBC list

- For T-01A, provide procurement plans, aggregate value calculations, quotations or tenders, approval records, purchase orders, and contracts for sampled suppliers and periods.
- For T-01B, provide purchase orders, contract values, procurement route records, quotation or tender files, and approvals for sampled transactions.
- For T-02, provide invoice images and identifiers, purchase orders, payment approvals, supplier statements, credit notes, and recovery records for sampled pairs.
- For T-03, provide each sampled supplier's complete three-month spend ledger, the current contract register, procurement portal and CDP records, framework call-offs, supplier aliases, approved waivers, and relevant contract files.
- For T-04, provide the complete payment ledger for each flagged supplier throughout the spend window, current and preceding signed contracts, extension clauses and approvals, replacement procurement files, and sampled payment support.
- For T-05, provide signed contracts, award-value basis, cumulative spend ledgers, variation requests, prior approvals, and sampled invoice support.

