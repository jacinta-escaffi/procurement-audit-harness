from decimal import Decimal, InvalidOperation
import re

from common import csv_file, json_file, main

TOKEN = re.compile(r"(?<![\w.])£?\d[\d,]*(?:\.\d+)?(?![\w.])")


def numbers(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from numbers(child)
    elif isinstance(value, list):
        for child in value:
            yield from numbers(child)
    elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        yield Decimal(str(value))
    elif isinstance(value, str):
        try:
            yield Decimal(value.replace(",", "").removeprefix("£"))
        except InvalidOperation:
            pass


def prose(line):
    line = re.sub(r"`[^`]*`", " ", line)
    line = re.sub(r"\[(?:rule|metric|history):[^\]]+\]", " ", line)
    line = re.sub(r"\b(?:CPR|R|C|T|SW|DP)-[A-Za-z0-9-]+\b", " ", line)
    line = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", " ", line)
    line = re.sub(r"\b(?:19|20)\d{2}(?:[/–-]\d{2,4})?\b", " ", line)
    line = re.sub(r"\b(?:clause|section|page|p\.)\s+\d+(?:\.\d+)*\b", " ", line, flags=re.I)
    return line


def check(root):
    out = root / "outputs"
    source = []
    for name in ("analytics.json", "pack-figures.json", "rules.json"):
        source.extend(numbers(json_file(out / name)))
    samples = sorted((out / "samples").glob("*.csv"))
    if not samples:
        return ["no sample CSV files"]
    for path in samples:
        source.extend(numbers(csv_file(path)))
    # A figure may be displayed rounded to 2 decimal places (the builders format money that way).
    allowed = set(source) | {value.quantize(Decimal("0.01")) for value in source if value == value and abs(value) < Decimal(10) ** 15}
    issues = []
    for name in ("planning-memo.md", "risk-control-matrix.md", "audit-program.md"):
        for line_number, line in enumerate((out / name).read_text(encoding="utf-8").splitlines(), 1):
            for match in TOKEN.finditer(prose(line)):
                token = match.group()
                digits = re.sub(r"\D", "", token.split(".")[0])
                if not token.startswith("£") and len(digits) < 3:
                    continue
                amount = Decimal(token.removeprefix("£").replace(",", ""))
                if amount not in allowed:
                    issues.append(f"{name}:{line_number}: unsupported figure {token}")
    return issues


if __name__ == "__main__":
    raise SystemExit(main(check))
