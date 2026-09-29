from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
REPORT_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "opportunities.db"
for directory in (DATA_DIR, RAW_DIR, REPORT_DIR):
    directory.mkdir(parents=True, exist_ok=True)

APP_TITLE = "Global Digital Product Opportunity Engine — V1"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "12"))
USER_AGENT = os.getenv("USER_AGENT", "DigitalProductOpportunityEngine/1.0 (research tool)")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
MAX_ITEMS_PER_SOURCE = int(os.getenv("MAX_ITEMS_PER_SOURCE", "25"))
