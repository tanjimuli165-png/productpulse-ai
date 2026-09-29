from app.collectors.youtube import YouTubeCollector
from app.database.models import Evidence
from app.processors.problem_miner import mine_problems

assert YouTubeCollector._video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
items = [Evidence(source="Web", title="Complaint", text="I waste hours manually reconciling invoices and the workaround is a spreadsheet. This is a serious problem for my team.", url="https://example.test/1")]
problems = mine_problems(items)
assert problems
assert "waste hours manually reconciling invoices" in problems[0].problem
assert "Customers are seeking" not in str(problems)
print("V1.1 smoke tests passed")
