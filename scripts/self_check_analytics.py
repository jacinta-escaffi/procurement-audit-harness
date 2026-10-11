from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from clean import load_contracts, load_spend

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"outputs"; FULL=OUT/"analytics-full"

def ok_entry(test, selected, values, passed, error=None):
    return {"test":test,"selected_identifier":selected,"recomputed_values":values,"result":"passed" if passed else "failed","error":error}

def main():
    analytics=json.loads((OUT/"analytics.json").read_text(encoding="utf-8")); rules=json.loads((OUT/"rules.json").read_text(encoding="utf-8"))
    spend,_,_=load_spend(ROOT/"data/spend"); lookup=spend.set_index("row_id",drop=False)
    notices,_=load_contracts(ROOT/"data/contracts.csv",spend.payment_date.min(),spend.payment_date.max())
    checks=[]
    # Clustering
    df=pd.read_csv(FULL/"threshold_clustering.csv")
    if len(df):
        x=df.iloc[0]; s=lookup.loc[x.row_id]; passed=s.net_amount>0 and s.net_amount>=.9*x.threshold_gbp and s.net_amount<x.threshold_gbp
        checks.append(ok_entry("threshold_clustering",x.row_id,{"source_amount":s.net_amount,"threshold":x.threshold_gbp,"source_file":s.source_file,"sheet_row":s.source_sheet_row},passed))
    else: checks.append(ok_entry("threshold_clustering",None,{"csv_rows":0},True))
    # Split
    w=pd.read_csv(FULL/"split_purchase_windows.csv"); m=pd.read_csv(FULL/"split_purchase_payments.csv")
    if len(w):
        x=w.iloc[0]; mm=m[m.window_id==x.window_id]; src=lookup.loc[mm.row_id]
        passed=len(src)>=2 and (src.net_amount>0).all() and (src.net_amount<x.threshold_gbp).all() and src.net_amount.sum()>=x.threshold_gbp and (src.payment_date.max()-src.payment_date.min()).days<=30
        checks.append(ok_entry("split_purchases",x.window_id,{"source_rows":len(src),"source_total":src.net_amount.sum(),"threshold":x.threshold_gbp,"days":(src.payment_date.max()-src.payment_date.min()).days},passed))
    else: checks.append(ok_entry("split_purchases",None,{"csv_rows":0},True))
    # Duplicate
    df=pd.read_csv(FULL/"duplicates.csv")
    if len(df):
        x=df.iloc[0]; a,b=lookup.loc[x.row_id_1],lookup.loc[x.row_id_2]; days=abs((b.payment_date-a.payment_date).days)
        passed=a.supplier_normalized==b.supplier_normalized and a.net_amount==b.net_amount and days<=7 and a.row_id!=b.row_id
        checks.append(ok_entry("duplicates",x.pair_id,{"same_supplier":a.supplier_normalized==b.supplier_normalized,"amount_1":a.net_amount,"amount_2":b.net_amount,"days":days},passed))
    else: checks.append(ok_entry("duplicates",None,{"csv_rows":0},True))
    quote=analytics["thresholds"]["quote_threshold"]; supplier=spend.groupby("supplier_normalized").net_amount.sum()
    # High value
    df=pd.read_csv(FULL/"high_value_suppliers.csv")
    if len(df):
        x=df.iloc[0]; total=float(supplier.loc[x.supplier_normalized]); annual=total*4; passed=annual>quote["gbp"]
        checks.append(ok_entry("high_value_suppliers",x.supplier_normalized,{"source_total":total,"annualised":annual,"threshold":quote["gbp"]},passed))
    else: checks.append(ok_entry("high_value_suppliers",None,{"source_above":int((supplier*4>quote['gbp']).sum())},int((supplier*4>quote['gbp']).sum())==0))
    active_names=set(notices.loc[notices.active_overlap,"supplier_normalized"]) if notices is not None else set()
    all_names=set(notices.supplier_normalized) if notices is not None else set()
    # Off contract
    df=pd.read_csv(FULL/"off_contract.csv")
    if len(df):
        x=df.iloc[0]; total=float(supplier.loc[x.supplier_normalized]); passed=total*4>quote["gbp"] and x.supplier_normalized not in active_names
        checks.append(ok_entry("off_contract",x.supplier_normalized,{"annualised":total*4,"threshold":quote["gbp"],"active_exact_match":x.supplier_normalized in active_names},passed))
    else: checks.append(ok_entry("off_contract",None,{"csv_rows":0},True))
    # Expired
    df=pd.read_csv(FULL/"expired_notice_spend.csv")
    if len(df):
        x=df.iloc[0]; g=notices[notices.supplier_normalized==x.supplier_normalized]; ends=g["Contract end date_parsed"]
        passed=x.supplier_normalized in all_names and ends.notna().all() and ends.lt(spend.payment_date.min()).all()
        checks.append(ok_entry("expired_notice_spend",x.supplier_normalized,{"matching_notices":len(g),"all_ended_before_window":bool(ends.lt(spend.payment_date.min()).all())},passed))
    else: checks.append(ok_entry("expired_notice_spend",None,{"csv_rows":0},True))
    # Spend versus award
    df=pd.read_csv(FULL/"spend_vs_award.csv")
    if len(df):
        x=df.iloc[0]; g=notices[(notices.supplier_normalized==x.supplier_normalized)&notices.active_overlap]; row=g.iloc[0]
        years=(row["Contract end date_parsed"]-row["Contract start date_parsed"]).days/365.25; ratio=float(supplier.loc[x.supplier_normalized])*4/(row.award_value/years)
        passed=g["Notice Identifier"].nunique()==1 and row.notice_supplier_count==1 and row.award_value>0 and years>0 and ratio>=1.25
        checks.append(ok_entry("spend_vs_award",x.supplier_normalized,{"active_notice_ids":g['Notice Identifier'].nunique(),"contract_years":years,"recomputed_ratio":ratio},passed))
    else: checks.append(ok_entry("spend_vs_award",None,{"csv_rows":0},True))
    failed=[c for c in checks if c["result"]!="passed"]
    report={"timestamp":datetime.now(timezone.utc).isoformat(),"method":"Independent source reload through clean.py; first deterministically sorted flag per nonempty test re-derived from source rows/notices/rules.","checks":checks,"counts":{"passed":len(checks)-len(failed),"skipped":0,"failed":len(failed)},"status":"failed" if failed else "passed"}
    (FULL/"self-check.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8")
    analytics["self_check"]={"status":report["status"],"check_path":"outputs/analytics-full/self-check.json","counts":report["counts"],"execution_timestamp":report["timestamp"]}
    (OUT/"analytics.json").write_text(json.dumps(analytics,indent=2,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    print(json.dumps(report["counts"],indent=2))
    if failed: raise SystemExit(1)
if __name__=="__main__": main()
