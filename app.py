"""Совместимость: Streamlit-панель живёт в studio/app.py."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
runpy.run_path(str(ROOT / "studio" / "app.py"), run_name="__main__")