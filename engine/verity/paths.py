"""Repository paths shared by scripts, experiments and the API."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVERLESS_TMP = Path("/tmp/verity")  # the only writable place on Vercel (its deployment files are read-only)
DATA_DIR = REPO_ROOT / "data"
SIM_DIR = DATA_DIR / "sim"
SPLITS_DIR = DATA_DIR / "splits"
CACHE_DIR = DATA_DIR / "cache"
PROCESSED_DIR = DATA_DIR / "processed"
CORPUS_DIR = DATA_DIR / "corpus"  # hand-collected public evidence (REAL, committed, no private text)
PRIVATE_DIR = DATA_DIR / "private"  # gitignored: full review text and anything else never committed
RESULTS_DIR = REPO_ROOT / "results"
DOCS_DIR = REPO_ROOT / "docs"


def on_serverless() -> bool:
    """True on Vercel, which sets ``VERCEL=1`` in its build and runtime environment."""
    return bool(os.environ.get("VERCEL", "").strip())


def writable_dir(path: Path) -> Path:
    """Where to WRITE something that lives at ``path``: the same place normally; on Vercel the same path under
    /tmp/verity (lost when the instance is recycled). Paths outside the repo (a test's tmp dir) are left alone.
    Read-only data (data/processed, corpus, sim, gazetteer, audio clips) keeps being read from the repo."""
    path = Path(path)
    if not on_serverless():
        return path
    try:
        return SERVERLESS_TMP / path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return path
