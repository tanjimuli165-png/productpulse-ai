from app.collectors.marketplaces import MarketplaceCollector
from app.collectors.youtube import YouTubeCollector
from app.product.launch_suite import natural_customer_language

assert YouTubeCollector._video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
assert natural_customer_language("YouTube result: meal prep struggles complaints mistakes") == "The workflow."
clean = natural_customer_language("I waste hours planning meals and keep missing ingredients")
assert clean == "I waste hours planning meals and keep missing ingredients."
item = MarketplaceCollector._from_text("Etsy", "Meal planner", "Printable planner $19 $29 $49 4.3 out of 5", "https://etsy.com/item", "Google fallback")
assert item.price == "$19, $29, $49"
assert item.rating == "4.3"
assert MarketplaceCollector()._dedupe([], 10) == []
print("V1.6 smoke test passed")
