from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess

from app.collectors.marketplaces import MarketplaceCollector
from app.database.models import Evidence, MarketplaceGap, Report
from app.pdf.generator import benchmark_price_label, concise_text, generate_pdf, observed_price_label
from app.product.launch_suite import natural_customer_language

long_text = "I keep losing valuable hours every single week because this complicated workflow is confusing and difficult to manage when I need a reliable result for my customers"
assert len(natural_customer_language(long_text).split()) <= 15
assert natural_customer_language(long_text).endswith("...")
assert concise_text(long_text).split()[-1] == "..."
assert observed_price_label(None) == "Not found"
assert benchmark_price_label(None) == "Not available"
assert observed_price_label("$12") == "$12"
report = Report(id="v21", topic="Meal Prep", evidence=[Evidence(source="Etsy", title="Example", text="This is enough evidence text for a report.")], marketplace_gaps=[MarketplaceGap(marketplace="Etsy", title="Notion planner", price=None, price_benchmark="$19 – $29", format="Notion")], sales_hooks=[long_text], executive_summary="Test")
with TemporaryDirectory() as directory:
    path = Path(directory) / "report.pdf"
    generate_pdf(report, path)
    text = " ".join(subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, check=True).stdout.split())
    assert "Not found" in text and "Benchmark estimate" in text and "$19" in text
print("V2.1 PDF formatting smoke test passed")
