from pathlib import Path
from tempfile import TemporaryDirectory

from app.database.db import ReportStore
from app.database.models import Report
from app.product.launch_suite import natural_customer_language
from app.collectors.marketplaces import MarketplaceCollector

with TemporaryDirectory() as directory:
    store = ReportStore(Path(directory) / "auth.db")
    ok, message = store.register_user("alice", "correct-horse-123")
    assert ok, message
    ok, message = store.register_user("bob", "another-pass-456")
    assert ok, message
    assert store.authenticate("alice", "wrong-password") is None
    alice = store.authenticate("ALICE", "correct-horse-123")
    bob = store.authenticate("bob", "another-pass-456")
    assert alice and bob and alice["user_id"] != bob["user_id"]
    alice_report = Report(id="alice-report", topic="Meal Prep", user_id=alice["user_id"], executive_summary="Alice summary")
    bob_report = Report(id="bob-report", topic="SaaS", user_id=bob["user_id"], executive_summary="Bob summary")
    store.save(alice_report, alice["user_id"])
    store.save(bob_report, bob["user_id"])
    assert [item.topic for item in store.recent(user_id=alice["user_id"])] == ["Meal Prep"]
    assert [item.topic for item in store.recent(user_id=bob["user_id"])] == ["SaaS"]
    assert store.get("bob-report", user_id=alice["user_id"]) is None

assert len(natural_customer_language("I keep losing valuable hours every single week because this complicated workflow is confusing and difficult to manage when I need a reliable result for my customers").rstrip(".").split()) <= 20
notion_item = MarketplaceCollector._from_text("Etsy", "Notion planner", "Notion planner", "https://etsy.com/item", "test")
assert notion_item.price is None and notion_item.price_benchmark == "$19 – $29"
print("V1.9 authentication and history smoke test passed")
