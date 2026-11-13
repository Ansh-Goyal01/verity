"""Check every hand-collected CSV in data/corpus/ and print friendly, row-by-row messages.

    uv run python scripts/validate_corpus.py            # errors and warnings
    uv run python scripts/validate_corpus.py --errors   # errors only

Row numbers match the spreadsheet (the header is row 1). ERROR rows are never used by any result;
WARN lines are a to-do list for drafts. Exit code 1 when any ERROR exists. Review text in
data/private/ is checked for matching review_ids only; its text is never printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from verity.corpus import (
    APP_REVIEWS,
    ERROR,
    PRIVATE_REVIEW_HEADER,
    PRIVATE_REVIEW_TEXT,
    SPECS,
    WARN,
    Issue,
    check_file,
    read_csv,
)
from verity.paths import CORPUS_DIR, PRIVATE_DIR


def check_private_reviews(corpus_folder: Path, private_folder: Path) -> list[Issue]:
    """Every labelled review should have its full text in data/private/ (gitignored). Text is never printed."""
    path = private_folder / PRIVATE_REVIEW_TEXT
    name = f"private/{PRIVATE_REVIEW_TEXT}"
    if not path.exists():
        return [Issue(name, 0, "", WARN, "not present on this machine; review classification (F6) will skip")]
    header, rows = read_csv(path)
    if [h.strip() for h in header] != PRIVATE_REVIEW_HEADER:
        return [Issue(name, 0, "", ERROR, f"header must be exactly: {','.join(PRIVATE_REVIEW_HEADER)}")]
    with_text = {r.get("review_id", "").strip() for r in rows if (r.get("text") or "").strip()}
    reviews_path = corpus_folder / APP_REVIEWS.filename
    if not reviews_path.exists():
        return []
    _, reviews = read_csv(reviews_path)
    missing = [r.get("review_id", "").strip() for r in reviews if r.get("review_id", "").strip() not in with_text]
    if missing:
        shown = ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else "")
        return [Issue(name, 0, "", WARN, f"{len(missing)} review_id(s) have no text yet: {shown}")]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the hand-collected corpus CSVs.")
    parser.add_argument("--errors", action="store_true", help="show errors only")
    parser.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    parser.add_argument("--private", type=Path, default=PRIVATE_DIR)
    args = parser.parse_args(argv)

    total_errors = 0
    for spec in SPECS:
        issues, n_rows, n_usable = check_file(spec, args.corpus)
        errors = sum(i.level == ERROR for i in issues)
        warns = sum(i.level == WARN for i in issues)
        total_errors += errors
        print(f"\n== {spec.filename}: {n_rows} rows, {n_usable} usable in results, "
              f"{errors} error(s), {warns} warning(s)")
        for issue in issues:
            if issue.level == ERROR or not args.errors:
                print("  " + issue.render())
    private_issues = check_private_reviews(args.corpus, args.private)
    total_errors += sum(i.level == ERROR for i in private_issues)
    print(f"\n== private/{PRIVATE_REVIEW_TEXT} (text never shown)")
    for issue in private_issues:
        if issue.level == ERROR or not args.errors:
            print("  " + issue.render())
    print("\nAll files pass (no errors)." if total_errors == 0 else
          f"\n{total_errors} error(s) to fix. Rows with errors are left out of every result.")
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
