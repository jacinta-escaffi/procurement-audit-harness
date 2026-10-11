import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def paths():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    return args.root.resolve()


def json_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def csv_file(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def main(check):
    try:
        issues = check(paths())
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        issues = [str(exc)]
    for issue in issues:
        print(f"FAIL: {issue}")
    if not issues:
        print("PASS")
    return 1 if issues else 0
