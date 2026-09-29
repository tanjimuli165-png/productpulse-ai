from app.search_utils import is_dictionary_or_definition_url, normalize_query, problem_query
from app.ui.web_interface import run_engine

assert normalize_query("Cocking") == "Cooking"
assert "complaints" in problem_query("Cocking")
assert "mistakes" in problem_query("Cocking")
assert is_dictionary_or_definition_url("https://en.wikipedia.org/wiki/Cooking")
assert is_dictionary_or_definition_url("https://www.merriam-webster.com/dictionary/cooking")
assert not is_dictionary_or_definition_url("https://example.com/cooking")
assert callable(run_engine)
print("V1.2 smoke tests passed")
