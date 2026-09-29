from app.collectors.marketplaces import MarketplaceCollector
from app.database.models import MarketplaceGap, Opportunity, ProblemSignal
from app.product.launch_suite import build_blueprint, build_launch_kit, natural_customer_language

long_text = "I keep losing valuable hours every single week because this complicated workflow is confusing and difficult to manage when I need a reliable result for my customers"
clean = natural_customer_language(long_text)
assert len(clean.rstrip(".").split()) <= 20
assert natural_customer_language("YouTube result: meal prep struggles complaints mistakes") == "The workflow."
notion_item = MarketplaceCollector._from_text("Etsy", "Notion planner", "Notion planner for meal prep", "https://etsy.com/item", "Google fallback")
spreadsheet_item = MarketplaceCollector._from_text("Etsy", "Excel tracker", "Excel spreadsheet tracker", "https://etsy.com/item2", "Google fallback")
assert notion_item.price is None and notion_item.price_benchmark == "$19 – $29"
assert spreadsheet_item.price is None and spreadsheet_item.price_benchmark == "$29 – $49"
problem = ProblemSignal(problem=long_text, customer_language=[long_text])
opp = Opportunity(name="Meal Prep Kit", audience="home cooks", promise=long_text, format=["Notion"], problem_fit=.5, willingness_to_pay=.5, competition_gap=.5, evidence_strength=.5, validation_score=50, pricing={"starter":"$19","core":"$29","premium":"$49"}, components=[], differentiation=[], risks=[], next_steps=[])
blueprint = build_blueprint("Meal Prep", opp, problem)
kit = build_launch_kit("Meal Prep", opp, problem, [MarketplaceGap(marketplace="Etsy", title="Notion planner")])
assert len(blueprint["customer_pain_anchor"].rstrip(".").split()) <= 20
assert len(kit["listing_description"].split()) < 100
print("V1.7 smoke test passed")
