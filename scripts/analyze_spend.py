from __future__ import annotations
import hashlib, json, shutil
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
import pandas as pd
from clean import TAGS, load_contracts, load_spend

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"outputs"; FULL=OUT/"analytics-full"

def js(v):
    if isinstance(v, dict): return {str(k):js(x) for k,x in v.items()}
    if isinstance(v, list): return [js(x) for x in v]
    if isinstance(v, (pd.Timestamp,datetime)): return v.isoformat()
    if pd.isna(v): return None
    if hasattr(v,"item"): return v.item()
    return v

def recs(df,n=20): return js(df.head(n).to_dict("records"))
def agg_tags(df, grain, amount_col, tag_col="procurement_tag"):
    out={}
    for tag in TAGS:
        x=df[df[tag_col].eq(tag)] if tag_col in df else df.iloc[0:0]
        out[tag]={grain:int(len(x)),f"signed_{amount_col}_gbp":float(x[amount_col].sum()) if amount_col in x else 0.0}
    return out
def write(df,name):
    p=FULL/name; df.to_csv(p,index=False); return str(p.relative_to(ROOT)).replace("\\","/")

def thresholds(rules):
    params=rules["parameters"]; executable=[]; skipped=[]
    for rule in rules["rules"]:
        if not str(rule.get("clause","")).startswith("App B:"): continue
        for i,tier in enumerate(rule.get("thresholds",[])):
            complete=True
            for side in ("low","high"):
                b=tier.get(side,{})
                if not b.get("printed"): continue
                value=b.get("gbp"); par=b.get("parameter")
                if value is None and par: value=params.get(par)
                item={"rule_id":rule["rule_id"],"clause":rule["clause"],"tier_index":i,"basis":tier.get("basis"),"boundary_side":side,"printed_text":b.get("printed"),"gbp":value,"inclusive":b.get("inclusive"),"parameter":par}
                if isinstance(value,(int,float)): executable.append(item)
                else: skipped.append({**item,"reason":"null_parameter" if par else "non_numeric_boundary"}); complete=False
            if not complete: skipped.append({"rule_id":rule["rule_id"],"clause":rule["clause"],"tier_index":i,"reason":"incomplete_tier_unresolved_bound"})
    # distinct boundary values, retaining provenance
    vals={}
    for x in executable: vals.setdefault(float(x["gbp"]),[]).append(x)
    boundaries=[{"gbp":k,"sources":v} for k,v in sorted(vals.items())]
    quote=[]
    for rule in rules["rules"]:
        if str(rule.get("clause","")).startswith("App B:") and "invitations to quote" in rule.get("required_action","").lower() and "multiple" not in "":
            for t in rule.get("thresholds",[]):
                b=t.get("low",{}); val=b.get("gbp") if b.get("gbp") is not None else params.get(b.get("parameter"))
                if b.get("inclusive") and isinstance(val,(int,float)): quote.append((float(val),rule["rule_id"],rule["clause"]))
    # Wording says multiple sources; extracted action states at least three.
    quote=sorted(quote)
    return boundaries, skipped, ({"gbp":quote[0][0],"rule_id":quote[0][1],"clause":quote[0][2]} if quote else None)

def base_test(params, limitations, paths):
    return {"status":"completed","parameters":params,"overall":{},"by_tag":{t:{} for t in TAGS},"top_20":[],"full_list_paths":paths,"limitations":limitations}

def main():
    if not (OUT/"rules.json").exists(): raise SystemExit("outputs/rules.json unavailable")
    rules=json.loads((OUT/"rules.json").read_text(encoding="utf-8"))
    if "parameters" not in rules or "rules" not in rules: raise SystemExit("rules.json requires parameters and rules")
    spend, exclusions, clean_diag=load_spend(ROOT/"data/spend")
    if FULL.exists(): shutil.rmtree(FULL)
    FULL.mkdir(parents=True)
    exclusion_path=write(exclusions,"exclusions.csv")
    start,end=spend.payment_date.min(),spend.payment_date.max()
    notices, contract_diag=load_contracts(ROOT/"data/contracts.csv",start,end)
    boundaries, skipped_bounds, quote=thresholds(rules)
    tests={}

    # threshold clustering
    chunks=[]
    for b in boundaries:
        v=b["gbp"]; x=spend[(spend.net_amount>0)&(spend.net_amount>=.9*v)&(spend.net_amount<v)].copy()
        x["threshold_gbp"]=v; x["threshold_sources"]=json.dumps(b["sources"],ensure_ascii=False)
        chunks.append(x)
    tc=pd.concat(chunks,ignore_index=True) if chunks else spend.iloc[0:0].copy()
    tc=tc.sort_values(["net_amount","supplier_normalized","row_id"],ascending=[False,True,True])
    p=write(tc,"threshold_clustering.csv"); t=base_test({"window":"[0.9 x threshold, threshold)","positive_payments_only":True,"boundaries":boundaries},["Payment-level values are indicators; contract aggregation and VAT basis may differ."],[p])
    t["overall"]={"payment_threshold_pairs":len(tc),"signed_flagged_payment_gbp":float(tc.net_amount.sum())}; t["by_tag"]=agg_tags(tc,"payment_threshold_pairs","net_amount"); t["top_20"]=recs(tc[["row_id","supplier_normalized","payment_date","net_amount","threshold_gbp"]]); tests["threshold_clustering"]=t

    # split windows, canonical maximal membership per anchor then dedupe membership
    windows=[]; members=[]
    positive=spend[spend.net_amount>0].sort_values(["supplier_normalized","payment_date","row_id"])
    for vsrc in boundaries:
      v=vsrc["gbp"]
      for supplier,g in positive[positive.net_amount<v].groupby("supplier_normalized"):
        g=g.reset_index(drop=True)
        for i,row in g.iterrows():
          w=g[(g.payment_date>=row.payment_date)&(g.payment_date<=row.payment_date+pd.Timedelta(days=30))]
          if len(w)>=2 and w.net_amount.sum()>=v:
            membership=tuple(sorted(w.row_id)); key=supplier+"|"+str(v)+"|"+"|".join(membership)
            wid="SW-"+hashlib.sha256(key.encode()).hexdigest()[:16]
            if any(z["window_id"]==wid for z in windows): continue
            tags="|".join(sorted(w.procurement_tag.unique()))
            windows.append({"window_id":wid,"supplier_normalized":supplier,"threshold_gbp":v,"window_start":w.payment_date.min(),"window_end":w.payment_date.max(),"payment_rows":len(w),"window_total_gbp":float(w.net_amount.sum()),"tag_composition":tags})
            for _,m in w.iterrows(): members.append({**windows[-1],"row_id":m.row_id,"payment_date":m.payment_date,"net_amount":m.net_amount,"procurement_tag":m.procurement_tag,"source_file":m.source_file,"source_sheet_row":m.source_sheet_row})
    sw=pd.DataFrame(windows); sm=pd.DataFrame(members)
    if len(sw): sw=sw.sort_values(["window_total_gbp","supplier_normalized","window_id"],ascending=[False,True,True]); sm=sm.merge(sw[["window_id"]].assign(_order=range(len(sw))),on="window_id").sort_values(["_order","payment_date","row_id"]).drop(columns="_order")
    wp=write(sw,"split_purchase_windows.csv"); mp=write(sm,"split_purchase_payments.csv")
    t=base_test({"window_days":30,"positive_payments_only":True,"boundaries":boundaries},["Overlapping economic activity can form more than one distinct membership window.","By-tag window counts are non-additive because a window counts for every represented tag."],[wp,mp]); t["overall"]={"windows":len(sw),"signed_window_total_gbp":float(sw.window_total_gbp.sum()) if len(sw) else 0,"contributing_payment_rows":len(sm)}
    for tag in TAGS:
        x=sw[sw.tag_composition.str.split("|").map(lambda z:tag in z)] if len(sw) else sw
        t["by_tag"][tag]={"windows":len(x),"signed_window_total_gbp":float(x.window_total_gbp.sum()) if len(x) else 0}
    t["top_20"]=recs(sw); tests["split_purchases"]=t

    # duplicate pairs
    pairs=[]
    for (supplier,amount),g in spend.groupby(["supplier_normalized","net_amount"]):
      rows=list(g.sort_values(["payment_date","row_id"]).itertuples())
      for i,a in enumerate(rows):
       for b in rows[i+1:]:
        d=(b.payment_date-a.payment_date).days
        if d>7: break
        pid="DP-"+hashlib.sha256((a.row_id+"|"+b.row_id).encode()).hexdigest()[:16]
        pairs.append({"pair_id":pid,"supplier_normalized":supplier,"signed_amount_gbp":amount,"day_difference":d,"row_id_1":a.row_id,"row_id_2":b.row_id,"payment_date_1":a.payment_date,"payment_date_2":b.payment_date,"procurement_tag":a.procurement_tag,"tag_2":b.procurement_tag})
    dup=pd.DataFrame(pairs)
    if len(dup): dup=dup.sort_values(["signed_amount_gbp","supplier_normalized","pair_id"],ascending=[False,True,True])
    p=write(dup,"duplicates.csv"); t=base_test({"days":7,"amount":"exact signed"},["Repeated fields are a duplicate-payment risk indicator, not evidence either payment is invalid."],[p]); t["overall"]={"candidate_pairs":len(dup),"signed_pair_amount_gbp":float(dup.signed_amount_gbp.sum()) if len(dup) else 0}; t["by_tag"]=agg_tags(dup,"candidate_pairs","signed_amount_gbp"); t["top_20"]=recs(dup); tests["duplicates"]=t

    # suppliers and notice matching
    bytag=spend.pivot_table(index="supplier_normalized",columns="procurement_tag",values="net_amount",aggfunc="sum",fill_value=0).reindex(columns=TAGS,fill_value=0)
    suppliers=spend.groupby("supplier_normalized").agg(window_total_gbp=("net_amount","sum"),payment_rows=("row_id","count")).join(bytag).reset_index(); suppliers["annualised_estimate_gbp"]=suppliers.window_total_gbp*4
    if quote:
      hv=suppliers[suppliers.annualised_estimate_gbp>quote["gbp"]].sort_values(["annualised_estimate_gbp","supplier_normalized"],ascending=[False,True])
      p=write(hv,"high_value_suppliers.csv"); t=base_test({"quote_threshold":quote,"annualisation":"3-month signed total x4; strictly above"},["Annualisation is an estimate and seasonal spend may differ."],[p]); t["overall"]={"supplier_groups":len(hv),"signed_window_total_gbp":float(hv.window_total_gbp.sum()),"annualised_estimate_gbp":float(hv.annualised_estimate_gbp.sum())}; t["by_tag"]={tag:{"supplier_groups":int((hv[tag]!=0).sum()),"signed_tag_spend_gbp":float(hv[tag].sum())} for tag in TAGS}; t["top_20"]=recs(hv); tests["high_value_suppliers"]=t
    else:
      hv=suppliers.iloc[0:0]; tests["high_value_suppliers"]={"status":"skipped","skip_reason":"quote_threshold_unresolved","parameters":{},"overall":{},"by_tag":{x:{} for x in TAGS},"top_20":[],"full_list_paths":[],"limitations":[]}

    matching={"normalisation":"Unicode NFKD ASCII, uppercase, punctuation removed, whitespace collapsed, terminal LTD/LIMITED/PLC/LLP removed","exact_match":"normalised supplier equality; Companies House unavailable in spend schema","candidate_method":"difflib.SequenceMatcher ratio, best score >= 0.80, unmatched names only","coverage_counts":{}}
    if notices is None:
      for name in ["off_contract","expired_notice_spend","spend_vs_award"]: tests[name]={"status":"skipped","skip_reason":"contracts_csv_absent","parameters":{},"overall":{},"by_tag":{x:{} for x in TAGS},"top_20":[],"full_list_paths":[],"limitations":[]}
    else:
      all_names=set(notices.supplier_normalized); active_names=set(notices.loc[notices.active_overlap,"supplier_normalized"])
      eligible=hv.copy(); eligible["exact_active_match"]=eligible.supplier_normalized.isin(active_names); eligible["exact_any_match"]=eligible.supplier_normalized.isin(all_names)
      unmatched=eligible[~eligible.exact_active_match].copy()
      candidates=[]
      target=sorted(active_names)
      for s in unmatched.supplier_normalized:
        scores=sorted(((SequenceMatcher(None,s,n).ratio(),n) for n in target),reverse=True)
        if scores and scores[0][0]>=.80: candidates.append({"spend_supplier_normalized":s,"notice_supplier_normalized":scores[0][1],"score":scores[0][0],"related_test":"off_contract","match_status":"candidate"})
      cand=pd.DataFrame(candidates); cp=write(cand,"name_match_candidates.csv")
      off=unmatched.sort_values(["annualised_estimate_gbp","supplier_normalized"],ascending=[False,True]); op=write(off,"off_contract.csv")
      t=base_test({"publication_floor":quote,"exact_match_only":True},["Unmatched is an evidence-request indicator; a newer contract, framework, variation, exemption, or unpublished notice may exist."],[op,cp]); t["overall"]={"supplier_groups":len(off),"signed_window_total_gbp":float(off.window_total_gbp.sum()),"annualised_estimate_gbp":float(off.annualised_estimate_gbp.sum()),"exact_active_matches":int(eligible.exact_active_match.sum()),"candidate_pairs":len(cand)}; t["by_tag"]={tag:{"supplier_groups":int((off[tag]!=0).sum()),"signed_tag_spend_gbp":float(off[tag].sum())} for tag in TAGS}; t["top_20"]=recs(off); tests["off_contract"]=t
      exact_paid=suppliers[suppliers.supplier_normalized.isin(all_names)].copy()
      expired_names={n for n,g in notices.groupby("supplier_normalized") if g["Contract end date_parsed"].notna().all() and g["Contract end date_parsed"].lt(start).all()}
      ex=exact_paid[exact_paid.supplier_normalized.isin(expired_names)].sort_values(["annualised_estimate_gbp","supplier_normalized"],ascending=[False,True]); ep=write(ex,"expired_notice_spend.csv")
      t=base_test({"exact_name_matches_only":True,"spend_window_start":start},["A newer contract may exist without a notice."],[ep]); t["overall"]={"supplier_groups":len(ex),"signed_window_total_gbp":float(ex.window_total_gbp.sum())}; t["by_tag"]={tag:{"supplier_groups":int((ex[tag]!=0).sum()),"signed_tag_spend_gbp":float(ex[tag].sum())} for tag in TAGS}; t["top_20"]=recs(ex); tests["expired_notice_spend"]=t
      exclusions={"no_exact_active_notice":0,"multiple_active_notices":0,"multi_supplier_notice":0,"zero_or_missing_award":0,"missing_or_nonpositive_duration":0}; sva=[]
      for _,s in suppliers.iterrows():
        g=notices[(notices.supplier_normalized==s.supplier_normalized)&notices.active_overlap]
        if len(g)==0: exclusions["no_exact_active_notice"]+=1; continue
        ids=g["Notice Identifier"].unique()
        if len(ids)!=1: exclusions["multiple_active_notices"]+=1; continue
        row=g.iloc[0]
        if row.notice_supplier_count!=1: exclusions["multi_supplier_notice"]+=1; continue
        if pd.isna(row.award_value) or row.award_value<=0: exclusions["zero_or_missing_award"]+=1; continue
        cs=row["Contract start date_parsed"]; ce=row["Contract end date_parsed"]
        if pd.isna(cs) or pd.isna(ce) or ce<=cs: exclusions["missing_or_nonpositive_duration"]+=1; continue
        years=(ce-cs).days/365.25; annual=row.award_value/years; ratio=s.annualised_estimate_gbp/annual
        if ratio>=1.25: sva.append({**s.to_dict(),"notice_id":row["Notice Identifier"],"award_value_gbp":row.award_value,"contract_years":years,"annual_award_gbp":annual,"ratio":ratio})
      sv=pd.DataFrame(sva)
      if len(sv): sv=sv.sort_values(["ratio","supplier_normalized"],ascending=[False,True])
      sp=write(sv,"spend_vs_award.csv"); t=base_test({"ratio_floor":1.25,"exclusion_reason_counts":exclusions},["Award values may be estimates or maxima; an approved variation may exist."],[sp]); t["overall"]={"supplier_groups":len(sv),"signed_window_total_gbp":float(sv.window_total_gbp.sum()) if len(sv) else 0}; t["by_tag"]={tag:{"supplier_groups":int((sv[tag]!=0).sum()) if len(sv) else 0,"signed_tag_spend_gbp":float(sv[tag].sum()) if len(sv) else 0} for tag in TAGS}; t["top_20"]=recs(sv); tests["spend_vs_award"]=t
      matching["coverage_counts"]={"eligible_supplier_groups":len(eligible),"exact_active_supplier_groups":int(eligible.exact_active_match.sum()),"unmatched_supplier_groups":len(off),"candidate_pairs":len(cand),"exact_any_paid_supplier_groups":len(exact_paid),"companies_house_spend_key_available":False}

    exagg=exclusions if isinstance(exclusions,dict) else None
    exclusion_summary={k:{"rows":int(len(g)),"signed_net_amount_gbp":float(g.net_amount.sum())} for k,g in (load_spend(ROOT/"data/spend")[1]).groupby("exclusion_reason")}
    tag_summary={tag:{"rows":int(len(g)),"signed_net_amount_gbp":float(g.net_amount.sum())} for tag,g in spend.groupby("procurement_tag")}
    for tag in TAGS: tag_summary.setdefault(tag,{"rows":0,"signed_net_amount_gbp":0})
    result={"run":{"timestamp":datetime.now(timezone.utc).isoformat(),"input_filenames":{"rules":"outputs/rules.json","spend":[p.name for p in sorted((ROOT/'data/spend').glob('*.xlsx'))],"contracts":"data/contracts.csv" if notices is not None else None},"spend_window":{"start":start,"end":end},"contracts_available":notices is not None,"code_file_paths":["scripts/clean.py","scripts/analyze_spend.py","scripts/self_check_analytics.py"],"limitation":"Risk indicators and evidence-request populations for auditor review; no finding establishes non-compliance or wrongdoing."},"cleaning":{**clean_diag,"included":{"rows":len(spend),"signed_net_amount_gbp":float(spend.net_amount.sum())},"excluded":{"rows":len(load_spend(ROOT/'data/spend')[1]),"signed_net_amount_gbp":float(load_spend(ROOT/'data/spend')[1].net_amount.sum()),"by_reason":exclusion_summary,"full_list_path":exclusion_path},"row_id_checks":{"non_null":True,"unique":True},"date_diagnostics":{"min":start,"max":end,"calendar_date_used":True},"tags":tag_summary,"contracts":contract_diag},"thresholds":{"executable_boundaries":boundaries,"skipped":skipped_bounds,"quote_threshold":quote},"matching":matching,"tests":tests,"warnings":contract_diag.get("warnings",[]),"self_check":{"status":"pending"}}
    (OUT/"analytics.json").write_text(json.dumps(js(result),indent=2,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    print(json.dumps({"included":len(spend),"excluded":result['cleaning']['excluded']['rows'],"tests":{k:v['status'] for k,v in tests.items()}},indent=2))
if __name__=="__main__": main()
