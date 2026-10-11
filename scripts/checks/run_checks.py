from common import main, paths
from check_quotes import check as quotes
from check_numbers import check as numbers
from check_trace import check as trace
from check_samples import check as samples

CHECKS = (("quotes", quotes), ("numbers", numbers), ("trace", trace), ("samples", samples))


def run(root):
    results = []
    for name, check in CHECKS:
        try:
            issues = check(root)
        except Exception as exc:
            issues = [f"{type(exc).__name__}: {exc}"]
        results.append((name, issues))
    lines = ["| Check | Result | Items |", "| --- | --- | --- |"]
    for name, issues in results:
        detail = "<br>".join(item.replace("|", "\\|") for item in issues) if issues else "—"
        lines.append(f"| {name} | {'FAIL' if issues else 'PASS'} | {detail} |")
    verdict = "BLOCKED" if any(issues for _, issues in results) else "READY FOR SIGN-OFF"
    lines.extend(["", f"Verdict: {verdict}"])
    return "\n".join(lines), bool(verdict == "READY FOR SIGN-OFF"), results


if __name__ == "__main__":
    report, passed, _ = run(paths())
    print(report)
    raise SystemExit(0 if passed else 1)
