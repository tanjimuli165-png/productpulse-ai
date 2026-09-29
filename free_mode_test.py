import unittest
from app.product.product_schema import ProductInputs
from app.product.strategy import generate_blueprint
from app.product.content_generator import generate_section_content

class FreeModeTest(unittest.TestCase):
    def setUp(self):
        self.inputs = ProductInputs(product_title="Client onboarding toolkit", audience="independent consultants", problem="Client onboarding feels scattered", promise="Use a repeatable onboarding workflow", format_hints=["workbook", "template"], differentiation=["simple workflow"], validation_steps=["test with five users"])
    def test_blueprint_without_provider(self):
        bp = generate_blueprint(self.inputs, "Workbook")
        self.assertGreaterEqual(len(bp.outline), 3)
        self.assertEqual(bp.product_type, "Workbook")
    def test_section_without_provider(self):
        bp = generate_blueprint(self.inputs, "Workbook")
        section = generate_section_content(bp, 0, self.inputs.evidence)
        self.assertTrue(section.blocks)

if __name__ == "__main__":
    unittest.main()
