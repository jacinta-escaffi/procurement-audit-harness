# Planning memo

## Scope and basis
The auditor approved 5 risks for testing. The work uses the council's Contract Procedure Rules, three monthly spend workbooks, the public Contracts Finder notice export, and supplied committee papers. Public notices are an incomplete proxy for the council's contract register. All analytics are risk indicators requiring corroboration, not findings of breach.

## Risk focus
Threshold and potential split-purchase indicators include 1380 overlapping windows [metric:$.tests.split_purchases.overall.windows]. Potential duplicate pairs total 4245 [metric:$.tests.duplicates.overall.candidate_pairs]. The unmatched high-value notice population contains 641 supplier groups [metric:$.tests.off_contract.overall.supplier_groups]. The expiry and award-profile tests each flag 7 and 7 supplier groups respectively [metric:$.tests.expired_notice_spend.overall.supplier_groups] [metric:$.tests.spend_vs_award.overall.supplier_groups].

## Approach and limits
For each sampled indicator, obtain the underlying procurement and payment evidence, verify the applicable contract value and route, then determine whether the indicator has a legitimate explanation. Overlapping windows, recurring payments, name aliases, frameworks, unlisted notices, and estimated award values can produce false positives. The sample is targeted and does not support a population-wide compliance rate. History records recent Contract Letting Reasonable Assurance, but action-level due dates and statuses are unavailable in the supplied papers.
