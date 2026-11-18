"""Summarise the venue voice samples into results/voice_real.json (guide W3.5).

    uv run python scripts/voice_real_eval.py                      # data/lab/voice_samples.jsonl -> results/voice_real.json
    uv run python scripts/voice_real_eval.py --samples PATH --out PATH

Reads the samples captured through POST /api/lab/voice-sample (VERITY_LAB_MODE=1). It makes no network call. With no samples it
writes NOTHING and exits 1: the evidence ledger treats the existence of results/voice_real.json as "measured", so an empty file
would claim a measurement that never happened. Under 30 samples or 10 speakers the file is written with "preliminary": true.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from verity.paths import DATA_DIR, RESULTS_DIR  # noqa: E402
from verity.voice_lab import SampleStore, summarise  # noqa: E402


def default_samples_path() -> Path:
    return Path(os.environ.get("VERITY_LAB_DIR") or DATA_DIR / "lab") / "voice_samples.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=Path, default=default_samples_path())
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "voice_real.json")
    args = parser.parse_args(argv)

    samples, skipped = SampleStore(args.samples).read()
    if skipped:
        print(f"warning: {skipped} line(s) in {args.samples} could not be read and are not counted", file=sys.stderr)
    if not samples:
        print(f"no samples in {args.samples}: nothing written (the ledger would call an empty file a measurement)", file=sys.stderr)
        return 1
    summary = summarise(samples, skipped_lines=skipped)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    status = f"PRELIMINARY ({summary['preliminary_reason']})" if summary["preliminary"] else "meets the 30-sample / 10-speaker target"
    print(f"{summary['n_samples']} samples from {summary['n_speakers']} speakers -> {args.out}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
