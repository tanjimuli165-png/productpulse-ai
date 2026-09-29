from __future__ import annotations
import importlib.util
import os
from pathlib import Path

REQUIRED = ["streamlit", "requests", "bs4", "reportlab", "pydantic", "pandas", "PIL", "pypdf", "svglib"]
OPTIONAL = ["youtube_transcript_api", "pg8000", "openai", "extra_streamlit_components"]
print("Digital Product Opportunity Engine — environment doctor")
print("Python:", os.sys.version.split()[0])
missing=[]
for name in REQUIRED:
    ok=importlib.util.find_spec(name) is not None
    print(f"{'OK' if ok else 'MISSING':7} {name}")
    if not ok: missing.append(name)
for name in OPTIONAL:
    ok=importlib.util.find_spec(name) is not None
    print(f"{'OK' if ok else 'OPTIONAL':7} {name}")
print("AI provider:", "configured" if (os.getenv("PRODUCT_BUILDER_API_KEY") or os.getenv("OPENAI_API_KEY")) else "not configured — free deterministic mode will be used")
print("Storage:", os.getenv("APP_STORAGE_BACKEND", "local"))
print("Result:", "READY" if not missing else "INSTALL MISSING REQUIRED PACKAGES")
raise SystemExit(0 if not missing else 1)
