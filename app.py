"""Vercel entrypoint: exposes the FastAPI ``app`` of api/main.py (Vercel looks for ``app`` in a root ``app.py``).

The repo's own ``engine/`` goes first on the import path so ``verity`` resolves to the checked-out source, whose
``paths.REPO_ROOT`` finds ``data/``, even if the platform also installed the package into site-packages.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for entry in (ROOT / "engine", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from api.main import app  # noqa: E402,F401  (the ASGI app Vercel serves)
