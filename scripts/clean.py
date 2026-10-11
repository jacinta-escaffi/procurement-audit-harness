from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd

EXPECTED = ["Service", "Expenditure category", "Narrative", "Date", "Net amount", "Supplier name"]
TAGS = ["care_or_education_placement", "grant", "agency_staff", "premises", "general_procurement"]
SUPPLIER_COL = "Supplier [Name|Address|Ref type|Ref Number|Is SME|Is VCSE]"


def normalise_supplier(value: object) -> str:
    if pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    text = re.sub(r"\s+(LTD|LIMITED|PLC|LLP)$", "", text).strip()
    return re.sub(r"\s+", " ", text)


def _contains(series: pd.Series, pattern: str) -> pd.Series:
    return series.fillna("").astype(str).str.upper().str.contains(pattern, regex=True, na=False)


def _classify(df: pd.DataFrame) -> pd.DataFrame:
    supplier = df["Supplier name"].fillna("").astype(str).str.strip()
    narrative = df["Narrative"].fillna("").astype(str)
    reasons = pd.Series("", index=df.index, dtype="object")
    rules = pd.Series("", index=df.index, dtype="object")
    mask = supplier.eq("") | supplier.str.upper().eq("REDACTED")
    reasons.loc[mask] = "redacted_supplier"; rules.loc[mask] = "Supplier name blank or REDACTED"
    statutory = r"\b(LGPS|PENSION|PRUDENTIAL AVC|COURT ORDER CONTROL|COUNCIL TAX COURT ORDERS)\b"
    mask = reasons.eq("") & _contains(narrative, statutory)
    reasons.loc[mask] = "pension_or_statutory_payment"; rules.loc[mask] = "Narrative matched bounded pension/statutory-control phrase"
    public = r"\b(MINISTRY OF|DEPARTMENT FOR|BOROUGH COUNCIL|CITY COUNCIL|COUNTY COUNCIL|DISTRICT COUNCIL|PARISH COUNCIL|NHS|POLICE AND CRIME COMMISSIONER|POLICE AUTHORITY|LOCAL AUTHORITY)\b"
    mask = reasons.eq("") & (_contains(supplier, public) | _contains(narrative, r"\b(PAYMENT TO|RECHARGE FROM|JOINT ARRANGEMENT WITH)\b.*" + public))
    reasons.loc[mask] = "other_public_body"; rules.loc[mask] = "Supplier/Narrative bounded public-body pattern"
    df["exclusion_reason"] = reasons; df["exclusion_rule"] = rules
    return df


def _tag(df: pd.DataFrame) -> pd.DataFrame:
    service = df["Service"].fillna("").astype(str)
    narrative = df["Narrative"].fillna("").astype(str)
    category = df["Expenditure category"].fillna("").astype(str)
    tag = pd.Series("general_procurement", index=df.index, dtype="object")
    rule = pd.Series("fallback: general procurement", index=df.index, dtype="object")
    premises = _contains(category, r"^PREMISES$") | _contains(narrative, r"\b(RENT|REPAIRS? AND MAINTENANCE|MINOR WORKS|CLEANING|GROUNDS MAINTENANCE|ENERGY|WATER RATES|FIXTURES?/?FITTINGS?|PREMISES INSURANCE|STORAGE)\b")
    tag.loc[premises] = "premises"; rule.loc[premises] = "category/narrative premises rule"
    agency = _contains(narrative, r"\b(AGENCY STAFF|TEMPORARY STAFF|TEMP STAFF)\b")
    tag.loc[agency] = "agency_staff"; rule.loc[agency] = "narrative agency/temporary staff"
    grant = _contains(narrative, r"\bGRANTS?\b") | _contains(service, r"\bGRANTS?\b")
    tag.loc[grant] = "grant"; rule.loc[grant] = "service/narrative grant"
    care_service = _contains(service, r"ADULT SOCIAL CARE|CHILDREN.?S SOCIAL CARE|EDUCATION.*SEND|EDUCATION \(DSG FUNDED\).*OTHER EDUCATION GRANTS")
    placement = _contains(narrative, r"PRIVATE CONTRACTORS|PROVISION.?CARE ALLOWANCES|ADULT.?PLACEMENT ALLOWANCES|DIRECT PAYMENTS|PLACEMENT|CHILDREN.?IN.?CARE")
    care = care_service & placement
    tag.loc[care] = "care_or_education_placement"; rule.loc[care] = "care/education service plus placement/provision narrative"
    df["procurement_tag"] = tag; df["tag_rule"] = rule
    assert set(df["procurement_tag"]).issubset(TAGS)
    return df


def load_spend(spend_dir: Path | str = "data/spend"):
    frames, headers, unexpected = [], {}, {}
    files = sorted(Path(spend_dir).glob("*.xlsx"))
    if not files: raise ValueError("No spend workbooks found")
    for path in files:
        raw = pd.read_excel(path, sheet_name="Data to publish", header=None)
        hits = [i for i, row in raw.iterrows() if set(EXPECTED).issubset({str(x).strip() for x in row.dropna()})]
        if len(hits) != 1: raise ValueError(f"{path.name}: expected exactly one header row, found {len(hits)}")
        h = hits[0]; headers[path.name] = h + 1
        source = pd.read_excel(path, sheet_name="Data to publish", header=h)
        missing = set(EXPECTED) - set(source.columns)
        if missing: raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
        unexpected[path.name] = [str(c) for c in source.columns if c not in EXPECTED]
        df = source[EXPECTED].copy().dropna(how="all")
        df["source_file"] = path.name; df["source_sheet"] = "Data to publish"
        df["source_sheet_row"] = df.index + h + 2
        df["row_id"] = df["source_file"] + "|Data to publish|" + df["source_sheet_row"].astype(str)
        df["source_date_value"] = df["Date"]
        def date_parse(v):
            if pd.isna(v): return pd.NaT
            if isinstance(v, (pd.Timestamp, __import__('datetime').datetime)): return pd.Timestamp(v)
            return pd.to_datetime(str(v), format="%m/%d/%y", errors="raise")
        df["payment_date"] = df["Date"].map(date_parse).dt.normalize()
        df["net_amount"] = pd.to_numeric(df["Net amount"], errors="raise")
        df["supplier_normalized"] = df["Supplier name"].map(normalise_supplier)
        frames.append(df)
    all_spend = pd.concat(frames, ignore_index=True)
    if all_spend["row_id"].isna().any() or all_spend["row_id"].duplicated().any(): raise ValueError("row_id is null or duplicated")
    all_spend = _classify(all_spend)
    exclusions = all_spend[all_spend.exclusion_reason.ne("")].copy()
    included = _tag(all_spend[all_spend.exclusion_reason.eq("")].copy())
    return included, exclusions, {"header_rows": headers, "unexpected_columns": unexpected}


def load_contracts(path: Path | str, start: pd.Timestamp, end: pd.Timestamp):
    path = Path(path)
    if not path.exists(): return None, {"contracts_available": False}
    raw = pd.read_csv(path)
    required = {"Notice Identifier", "Organisation Name", "Published Date", "Awarded Value", "Contract start date", "Contract end date", SUPPLIER_COL}
    missing = required-set(raw.columns)
    if missing: raise ValueError(f"contracts.csv missing columns {sorted(missing)}")
    buyer = raw["Organisation Name"].fillna("").astype(str).str.strip()
    keep = buyer.str.casefold().isin(["west berkshire council", "west berkshire"])
    c = raw.loc[keep].copy()
    c["published_date"] = pd.to_datetime(c["Published Date"], utc=True, errors="raise")
    for col in ["Start Date", "End Date", "Awarded Date", "Contract start date", "Contract end date"]:
        if col in c: c[col+"_parsed"] = pd.to_datetime(c[col], format="%d/%m/%Y", errors="raise")
    c["award_value"] = pd.to_numeric(c["Awarded Value"], errors="raise")
    rows=[]
    for _, row in c.iterrows():
        packed = row[SUPPLIER_COL]
        if pd.isna(packed): raise ValueError(f"Notice {row['Notice Identifier']}: blank supplier block")
        blocks = re.split(r"\]\[", str(packed).strip()[1:-1])
        for block in blocks:
            fields=block.split("|")
            if len(fields)!=6: raise ValueError(f"Notice {row['Notice Identifier']}: supplier block has {len(fields)} fields")
            out=row.to_dict(); out["supplier_raw_block"]=block; out["contract_supplier_name"]=fields[0]
            out["supplier_normalized"]=normalise_supplier(fields[0]); out["companies_house_number"]=fields[3].strip() if fields[2].strip().upper()=="COMPANIES_HOUSE" else ""
            rows.append(out)
    suppliers=pd.DataFrame(rows)
    suppliers["notice_supplier_count"] = suppliers.groupby("Notice Identifier")["supplier_normalized"].transform("nunique")
    ce=suppliers["Contract end date_parsed"]; cs=suppliers["Contract start date_parsed"]
    suppliers["end_date_kept"] = ce.isna() | ce.ge(start)
    suppliers["active_overlap"] = suppliers["end_date_kept"] & (cs.isna() | cs.le(end))
    years=c["published_date"].dt.year.value_counts().sort_index().to_dict()
    warnings=[]
    if len(c)<50 or any(v<2 for v in years.values()): warnings.append("Contracts export total or a publication-year count appears implausibly low; export cap or coverage gap may apply")
    return suppliers, {"contracts_available":True,"source_rows":len(raw),"retained_notice_rows":len(c),"dropped_buyer_rows":int((~keep).sum()),"buyer_spellings":buyer[keep].value_counts().to_dict(),"supplier_rows":len(suppliers),"one_sided_end_date_count":int(suppliers.end_date_kept.sum()),"true_overlap_count":int(suppliers.active_overlap.sum()),"published_by_year":years,"warnings":warnings}


if __name__ == "__main__":
    included, excluded, diagnostics=load_spend()
    print({"included_rows":len(included),"excluded_rows":len(excluded),"spend_window":[str(included.payment_date.min().date()),str(included.payment_date.max().date())],**diagnostics})
