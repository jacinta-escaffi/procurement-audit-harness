"""Aggregate-only input reconnaissance; not the spend-analyst pipeline."""
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {'Service', 'Expenditure category', 'Narrative', 'Date', 'Net amount', 'Supplier name'}


def counts(series):
    return {str(k): int(v) for k, v in series.fillna('<missing>').value_counts().items()}


def normalize(value):
    value = re.sub(r'[^\w\s]', '', str(value).upper())
    value = re.sub(r'(?:\s+(?:LTD|LIMITED|PLC|LLP))+$', '', value.strip())
    return re.sub(r'\s+', ' ', value).strip()


def date_profile(series, fmt):
    parsed = pd.to_datetime(series, format=fmt, errors='raise', utc=fmt == 'ISO8601')
    return parsed, {'min': str(parsed.min()), 'max': str(parsed.max()), 'blank': int(parsed.isna().sum())}


def main():
    report = {'workbooks': [], 'method': {'annualisation': 'sum of signed Net amount across all three files times 4; strictly above GBP 25000', 'match': 'exact normalized supplier name; punctuation removed, uppercase, terminal LTD/LIMITED/PLC/LLP removed', 'population': 'named suppliers; redacted/missing names excluded; no unapproved procurement exclusions or deduplication', 'active': 'specification filter: Contract end date >= first spend date, or blank; start-date overlap reported separately'}}
    frames = []
    for path in sorted((ROOT / 'data/spend').glob('*.xlsx')):
        raw = pd.read_excel(path, sheet_name='Data to publish', header=None)
        headers = [i for i, row in raw.iterrows() if EXPECTED.issubset(set(row.dropna().astype(str).str.strip()))]
        if len(headers) != 1:
            raise ValueError(f'{path.name}: expected one header, found {len(headers)}')
        df = pd.read_excel(path, sheet_name='Data to publish', header=headers[0])
        assert EXPECTED.issubset(df.columns)
        types = dict(Counter(type(v).__name__ for v in df['Date']))
        df['Date'] = df['Date'].map(lambda v: pd.to_datetime(v, format='%m/%d/%y') if isinstance(v, str) else pd.Timestamp(v))
        pd.to_numeric(df['Net amount'], errors='raise')
        redacted = df['Supplier name'].astype(str).str.strip().str.upper().eq('REDACTED')
        report['workbooks'].append({'file': path.name, 'header_excel_row': headers[0]+1, 'columns': df.columns.tolist(), 'rows': len(df), 'date_types': types, 'date_min': str(df['Date'].min()), 'date_max': str(df['Date'].max()), 'months': counts(df['Date'].dt.strftime('%Y-%m')), 'non_midnight_dates': int((df['Date'] != df['Date'].dt.normalize()).sum()), 'redacted_rows': int(redacted.sum()), 'redacted_net': float(df.loc[redacted, 'Net amount'].sum()), 'missing_supplier_rows': int(df['Supplier name'].isna().sum()), 'narrative_values': counts(df['Narrative']), 'category_values': counts(df['Expenditure category'])})
        frames.append(df)
    spend = pd.concat(frames, ignore_index=True)
    lo, hi = spend['Date'].min().normalize(), spend['Date'].max().normalize()
    report['spend'] = {'rows': len(spend), 'window_start': str(lo.date()), 'window_end': str(hi.date()), 'net': float(spend['Net amount'].sum()), 'narrative_values': counts(spend['Narrative']), 'category_values': counts(spend['Expenditure category'])}
    raw = pd.read_csv(ROOT / 'data/contracts.csv')
    strict = raw['Organisation Name'].isin(['West Berkshire Council', 'West Berkshire'])
    insensitive = raw['Organisation Name'].str.strip().str.upper().isin(['WEST BERKSHIRE COUNCIL', 'WEST BERKSHIRE'])
    dates = {}
    for col in ['Start Date', 'End Date', 'Awarded Date', 'Contract start date', 'Contract end date']:
        raw[col], dates[col] = date_profile(raw[col], '%d/%m/%Y')
    raw['Published Date'], dates['Published Date'] = date_profile(raw['Published Date'], 'ISO8601')
    supplier_col = 'Supplier [Name|Address|Ref type|Ref Number|Is SME|Is VCSE]'
    report['contracts'] = {'rows': len(raw), 'buyer_counts': counts(raw['Organisation Name']), 'strict_council_rows': int(strict.sum()), 'case_insensitive_council_rows': int(insensitive.sum()), 'date_fields_all_buyers': dates, 'status_counts': counts(raw['Status']), 'variants': {}}
    named = spend.loc[spend['Supplier name'].notna() & ~spend['Supplier name'].astype(str).str.strip().str.upper().eq('REDACTED')].copy()
    named['key'] = named['Supplier name'].map(normalize)
    totals = named.groupby('key')['Net amount'].sum()
    eligible = totals[totals * 4 > 25000]
    report['matches'] = {'named_supplier_groups': len(totals), 'eligible_supplier_groups': len(eligible), 'eligible_3month_net': float(eligible.sum()), 'variants': {}}
    for label, mask in [('strict', strict), ('case_insensitive', insensitive)]:
        council = raw.loc[mask].copy()
        active = council['Contract end date'].isna() | council['Contract end date'].ge(lo)
        overlap = active & (council['Contract start date'].isna() | council['Contract start date'].le(hi))
        blocks = []
        malformed = 0
        per_notice = []
        for idx, row in council.iterrows():
            value = row[supplier_col]
            if pd.isna(value):
                per_notice.append(0)
                continue
            parts = str(value).strip('[]').split('][')
            per_notice.append(len(parts))
            for part in parts:
                fields = part.split('|')
                if len(fields) != 6:
                    malformed += 1
                blocks.append({'key': normalize(fields[0]), 'active': bool(active.loc[idx]), 'overlap': bool(overlap.loc[idx]), 'ch': len(fields) >= 4 and fields[2] == 'COMPANIES_HOUSE'})
        all_keys = {b['key'] for b in blocks}
        active_keys = {b['key'] for b in blocks if b['active']}
        overlap_keys = {b['key'] for b in blocks if b['overlap']}
        stats = {}
        for name, keys in [('any_notice', all_keys), ('spec_active_notice', active_keys), ('date_overlap_notice', overlap_keys)]:
            matched = eligible[eligible.index.isin(keys)]
            stats[name] = {'matched_suppliers': len(matched), 'unmatched_suppliers': len(eligible)-len(matched), 'supplier_share_pct': float(len(matched)/len(eligible)*100), 'matched_3month_net': float(matched.sum()), 'spend_share_pct': float(matched.sum()/eligible.sum()*100)}
        report['matches']['variants'][label] = stats
        report['contracts']['variants'][label] = {'rows': len(council), 'unique_notice_ids': int(council['Notice Identifier'].nunique()), 'published_by_year': counts(council['Published Date'].dt.year), 'date_fields': {c: {'min': str(council[c].min()), 'max': str(council[c].max()), 'blank': int(council[c].isna().sum())} for c in dates}, 'live_spec_count': int(active.sum()), 'date_overlap_count': int(overlap.sum()), 'expired_count': int((~active).sum()), 'future_start_among_spec_live': int((active & ~overlap).sum()), 'blank_supplier_notices': sum(n == 0 for n in per_notice), 'supplier_blocks': len(blocks), 'multi_supplier_notices': sum(n > 1 for n in per_notice), 'max_blocks_per_notice': max(per_notice), 'malformed_blocks': malformed, 'companies_house_blocks': sum(b['ch'] for b in blocks), 'zero_award_notices': int(pd.to_numeric(council['Awarded Value'], errors='raise').eq(0).sum()), 'live_by_spend_month': {str(month): int((council['Contract end date'].isna() | council['Contract end date'].ge(month.start_time)) .sum()) for month in sorted(spend['Date'].dt.to_period('M').unique())}}
    out = ROOT / 'outputs/input-profile.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ['workbooks', 'spend']}, indent=2))


if __name__ == '__main__':
    main()
