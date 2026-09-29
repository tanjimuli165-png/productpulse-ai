from pathlib import Path
from tempfile import TemporaryDirectory

from app.database.db import ReportStore
from app.database.models import Report

with TemporaryDirectory() as directory:
    db_path = Path(directory) / "history.db"
    store = ReportStore(db_path)
    report = Report(id="history-test", topic="Meal Prep", executive_summary="Saved summary for the opportunity scan.")
    store.save(report)
    reloaded_store = ReportStore(db_path)
    history = reloaded_store.recent(limit=5)
    assert len(history) == 1
    assert history[0].id == "history-test"
    assert history[0].topic == "Meal Prep"
    assert history[0].executive_summary == "Saved summary for the opportunity scan."
print("V1.8 history smoke test passed")
