"""App-first OpenAI vector-store backed file explorer."""

from __future__ import annotations

import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SHARED_ADMIN_SRC = _PROJECT_ROOT / "vendor" / "ai-portfolio-admin" / "src"
if _SHARED_ADMIN_SRC.exists():
    shared_admin_src = str(_SHARED_ADMIN_SRC)
    if shared_admin_src not in sys.path:
        sys.path.insert(0, shared_admin_src)
