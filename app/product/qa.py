from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Iterable

from app.product.product_schema import ProductBlueprint, ProductContent, ProductInputs


STOP_WORDS = {
    "a", "about", "after", "again", "all", "also", "an", "and", "any", "are", "as", "at", "be", "because",
    "been", "before", "being", "between", "both", "but", "by", "can", "could", "did", "do", "does", "each",
    "for", "from", "get", "got", "had", "has", "have", "he", "her", "here", "hers", "him", "his", "how",
    "i", "if", "in", "into", "is", "it", "its", "just", "may", "me", "might", "more", "most", "much",
    "must", "my", "no", "not", "of", "on", "or", "our", "ours", "out", "over", "she", "should", "so",
    "some", "such", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those",
    "through", "to", "too", "up", "us", "very", "was", "we", "were", "what", "when", "where", "which", "who",
    "why", "will", "with", "would", "you", "your",
}
NEGATION_WORDS = {"not", "never", "no", "without", "cannot", "can't", "dont", "don't", "doesnt", "doesn't", "isnt", "isn't", "wont", "won't"}
FILLER_PHRASES = (
    "in today's fast-paced world", "it is important to note", "let's dive in", "without further ado",
    "at the end of the day", "game changer", "unlock your potential", "in conclusion",
)
ABSOLUTE_CLAIM_RE = re.compile(
    r"\b(?:guarantee(?:d|s)?|100\s*%|always|never|proven to|will definitely|will sell|risk[- ]free|works for everyone|eliminate all)\b",
    re.IGNORECASE,
)
NUMBER_CLAIM_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent|times|days?|weeks?|months?|years?|people|customers|users|sales|revenue)\b", re.IGNORECASE)


class _PreviewParser(HTMLParser):
    """Collect a small structural inventory from our deterministic HTML preview."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pages: list[dict[str, Any]] = []
        self.headings: list[tuple[str, str]] = []
        self.images: list[dict[str, str]] = []
        self.tables: list[dict[str, Any]] = []
        self.main_attrs: dict[str, str] = {}
        self._page_depth = 0
        self._heading: tuple[str, list[str]] | None = None
        self._footer_depth = 0
        self._current_page_text: list[str] = []
        self._current_table: dict[str, Any] | None = None
        self._current_row_cells = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "").split()
        if tag == "main":
            self.main_attrs = values
        if tag == "section" and "dpe-page" in classes:
            self.pages.append({"text": [], "footer": ""})
            self._page_depth += 1
        if tag in {"h1", "h2", "h3"}:
            self._heading = (tag, [])
        if tag == "footer":
            self._footer_depth += 1
        if tag == "img":
            self.images.append({"src": values.get("src", ""), "alt": values.get("alt", "")})
        if tag == "table":
            self._current_table = {"widths": [], "rows": 0}
        if tag == "tr" and self._current_table is not None:
            self._current_row_cells = 0
        if tag in {"td", "th"} and self._current_table is not None:
            self._current_row_cells += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3"} and self._heading:
            kind, value = self._heading
            self.headings.append((kind, " ".join("".join(value).split())))
            self._heading = None
        if tag == "tr" and self._current_table is not None:
            self._current_table["rows"] += 1
            self._current_table["widths"].append(self._current_row_cells)
        if tag == "table" and self._current_table is not None:
            self.tables.append(self._current_table)
            self._current_table = None
        if tag == "footer" and self._footer_depth:
            self._footer_depth -= 1
        if tag == "section" and self._page_depth:
            self.pages[-1]["text"] = self._current_page_text
            self._current_page_text = []
            self._page_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading[1].append(data)
        if self._page_depth:
            self._current_page_text.append(data)
            if self._footer_depth:
                self.pages[-1]["footer"] += data


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(word) > 2 and word not in STOP_WORDS}


def _shingle_similarity(left: str, right: str) -> float:
    """Return a bounded-cost character 5-gram Jaccard similarity for repetition candidates."""
    def shingles(value: str) -> set[str]:
        compact = re.sub(r"\s+", " ", value.strip())
        if len(compact) < 5:
            return {compact}
        return {compact[index:index + 5] for index in range(len(compact) - 4)}

    left_shingles, right_shingles = shingles(left), shingles(right)
    union = left_shingles | right_shingles
    return len(left_shingles & right_shingles) / len(union) if union else 1.0


def _text_blocks(content: ProductContent) -> list[tuple[str, str, Any]]:
    results: list[tuple[str, str, Any]] = []
    for section in sorted(content.sections, key=lambda item: item.section_index):
        for block_index, block in enumerate(section.blocks):
            fragments = [block.title, block.body, *block.items, *[cell for row in block.rows for cell in row]]
            text = "\n".join(value for value in fragments if value and value.strip())
            results.append((f"Section {section.section_index + 1} · block {block_index + 1}", text, block))
    return results


def _check(name: str, category: str, status: str, message: str, method: str, issues: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "status": status,
        "message": message,
        "method": method,
        "issues": list(issues),
    }


def _sentence_candidates(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def _contradiction_candidates(blocks: list[tuple[str, str, Any]]) -> list[str]:
    sentences = [(label, sentence) for label, text, _ in blocks for sentence in _sentence_candidates(text)]
    indexed: dict[str, list[tuple[str, bool, str]]] = {}
    for label, sentence in sentences:
        words = re.findall(r"[a-z0-9']+", sentence.lower())
        if len(words) < 4:
            continue
        negative = any(word in NEGATION_WORDS for word in words)
        skeleton = " ".join(word for word in words if word not in NEGATION_WORDS)
        if len(skeleton) >= 12:
            indexed.setdefault(skeleton, []).append((label, negative, sentence))
    findings = []
    for variants in indexed.values():
        if len({negative for _, negative, _ in variants}) > 1:
            first = variants[0]
            opposite = next(value for value in variants[1:] if value[1] != first[1])
            findings.append(f"Possible negation conflict between {first[0]} (“{first[2][:140]}”) and {opposite[0]} (“{opposite[2][:140]}”).")
    return findings


def _expected_practical_kinds(product_type: str) -> set[str]:
    name = product_type.strip().lower()
    mapping = {
        "workbook": {"exercise", "worksheet", "table", "checklist"},
        "planner": {"table", "checklist", "action_steps", "steps"},
        "tracker": {"table", "checklist"},
        "checklist": {"checklist"},
        "worksheet": {"worksheet", "exercise"},
        "journal": {"reflection", "exercise"},
        "action plan": {"action_steps", "steps", "checklist"},
        "challenge": {"action_steps", "steps", "checklist"},
        "playbook": {"action_steps", "steps", "checklist", "exercise"},
        "template": {"table", "worksheet", "checklist"},
    }
    return mapping.get(name, set())


def _preview_checks(preview_html: str | None, content: ProductContent, design: dict[str, str], visual_assets: list[dict]) -> list[dict[str, Any]]:
    if not preview_html:
        return [
            _check("Preview structure", "Preview / PDF", "NOT RUN", "No rendered HTML preview was supplied to QA.", "The preview parser needs the current deterministic template output."),
            _check("PDF rendering checks", "PDF", "NOT RUN", "This QA run does not inspect an exported PDF; overflow, broken PDF pages, and final PDF numbering are not verified.", "Phase 7 checks saved content and its indicative HTML preview; PDF assembly is a separate export action."),
        ]

    parser = _PreviewParser()
    parser.feed(preview_html)
    parser.close()
    expected_pages = len(content.sections) + 1
    page_count = len(parser.pages)
    page_issues = []
    if page_count != expected_pages:
        page_issues.append(f"Expected {expected_pages} indicative preview pages (cover plus saved sections), found {page_count}.")
    for index, page in enumerate(parser.pages, start=1):
        text = " ".join(page.get("text", []))
        if not text.strip():
            page_issues.append(f"Indicative preview page {index} is empty.")
    page_status = "FLAG" if page_issues else "PASS"

    h1 = [value for kind, value in parser.headings if kind == "h1"]
    h2 = [value for kind, value in parser.headings if kind == "h2"]
    heading_issues = []
    if len(h1) != 1:
        heading_issues.append(f"Expected one cover h1 heading, found {len(h1)}.")
    if len(h2) != len(content.sections):
        heading_issues.append(f"Expected {len(content.sections)} section h2 headings, found {len(h2)}.")
    expected_titles = [section.title.strip() for section in sorted(content.sections, key=lambda item: item.section_index)]
    if h2 != expected_titles:
        heading_issues.append("Rendered section heading text/order does not match the saved section titles.")

    table_issues = []
    expected_tables = sum(1 for _, _, block in _text_blocks(content) if block.kind == "table")
    if len(parser.tables) != expected_tables:
        table_issues.append(f"Expected {expected_tables} rendered tables from structured content, found {len(parser.tables)}.")
    for table_index, table in enumerate(parser.tables, start=1):
        if table["rows"] < 2 or not table["widths"] or len(set(table["widths"])) != 1:
            table_issues.append(f"Preview table {table_index} has no body rows or inconsistent row cell counts.")

    asset_issues = []
    expected_images: dict[str, int] = {}
    for asset in visual_assets:
        title = str(asset.get("title", "Product visual"))[:120]
        expected_images[title] = expected_images.get(title, 0) + 1
    for title, expected_count in expected_images.items():
        matching = [image for image in parser.images if image.get("alt") == title]
        if len(matching) < expected_count or any(not image.get("src", "").startswith("data:image/") for image in matching):
            asset_issues.append(f"{expected_count} saved visual(s) titled “{title}” expected; found {len(matching)} embedded preview image(s) with that alt text.")
    missing_alt = sum(1 for image in parser.images if not image.get("alt", "").strip())
    if missing_alt:
        asset_issues.append(f"{missing_alt} preview image(s) have no non-empty alt text.")

    page_labels = []
    for page in parser.pages:
        matches = re.findall(r"(?<!\d)(\d+\s*/\s*\d+)(?!\d)", page.get("footer", ""))
        page_labels.append(re.sub(r"\s+", "", matches[-1]) if matches else "")
    expected_labels = [f"{index}/{expected_pages}" for index in range(1, expected_pages + 1)]
    numbering_issues = []
    if page_labels != expected_labels:
        numbering_issues.append("Indicative HTML preview page labels do not match the expected sequence.")

    layout_issues = []
    if parser.main_attrs.get("data-template-id") != design.get("template_id"):
        layout_issues.append("Preview template identifier differs from the selected saved template.")
    if parser.main_attrs.get("data-page-size") != design.get("page_size"):
        layout_issues.append("Preview page-size identifier differs from the selected saved page size.")

    results = [
        _check("Empty / missing preview pages", "Preview / PDF", page_status,
               "Preview page containers and non-empty text were compared with the cover plus saved sections.",
               "Parses .dpe-page containers and checks each has text; this does not measure physical overflow.", page_issues),
        _check("Preview headings", "Preview / PDF", "FLAG" if heading_issues else "PASS",
               "Cover and section heading count, title, and order were compared with saved content.",
               "Counts h1/h2 tags and compares rendered h2 text to section titles.", heading_issues),
        _check("Preview tables", "Preview / PDF", "FLAG" if table_issues else "PASS",
               "Structured table count and row cell counts were compared with the rendered preview.",
               "Checks generated HTML table rows; underlying table blocks are schema-validated before storage.", table_issues),
        _check("Saved visuals in preview", "Preview / PDF", "FLAG" if asset_issues else "PASS",
               "Saved visuals supplied to this QA run were checked for an embedded preview image and alt text.",
               "Matches saved visual title to a data:image source and non-empty alt attribute; does not test PDF embedding or pixel quality.", asset_issues),
        _check("Indicative page labels", "Preview / PDF", "FLAG" if numbering_issues else "PASS",
               "The preview's own sequential page labels were checked.",
               "Checks HTML footer labels only; these are not PDF page numbers.", numbering_issues),
        _check("Preview layout settings", "Preview / PDF", "FLAG" if layout_issues else "PASS",
               "The preview's declared template and page size were compared with saved settings.",
               "Compares deterministic renderer data attributes; does not validate layout quality across browsers.", layout_issues),
        _check("Final PDF rendering", "PDF", "NOT RUN",
               "This QA run does not inspect an exported PDF, so PDF text overflow, broken PDF pages, final empty pages, embedded-image validity, and final page numbering remain unverified.",
               "Phase 7 QA checks content and the indicative preview only; product PDF assembly/export is a separate action."),
    ]
    return results



def _stem(value: str) -> str:
    token = re.sub(r"[^a-z0-9]", "", (value or "").lower())
    for suffix in ("ization", "ations", "ation", "ments", "ment", "ingly", "edly", "ing", "ers", "ies", "es", "ed", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            token = token[:-len(suffix)]
            break
    return token


def _concept_coverage(source_text: str, target_text: str) -> tuple[int, int, set[str]]:
    source_terms = sorted(_tokens(source_text))
    target_terms = _tokens(target_text)
    target_stems = {_stem(term) for term in target_terms}
    matched = []
    for term in source_terms:
        stem = _stem(term)
        if term in target_terms or stem in target_stems:
            matched.append(term)
    return len(matched), len(source_terms), set(matched)


def _distinct_reference_terms(reference_material: str) -> set[str]:
    return _tokens(reference_material) - {
        "provided", "reference", "material", "notes", "source", "example", "examples",
        "product", "digital", "information", "content", "reader", "use", "using",
    }


def run_product_qa(
    blueprint: ProductBlueprint,
    content: ProductContent,
    inputs: ProductInputs,
    *,
    design: dict[str, str],
    visual_assets: list[dict] | None = None,
    preview_html: str | None = None,
) -> dict[str, Any]:
    """Run transparent deterministic content and preview checks; never emit an arbitrary quality score."""
    assets = visual_assets or []
    checks: list[dict[str, Any]] = []
    expected = {index: section for index, section in enumerate(blueprint.outline)}
    actual = {section.section_index: section for section in content.sections}
    missing = [f"Section {index + 1}: {expected[index].title}" for index in expected if index not in actual]
    extra = [f"Section {index + 1}: {section.title}" for index, section in actual.items() if index not in expected]
    mismatched = [
        f"Section {index + 1}: saved title “{actual[index].title}” differs from outline title “{expected[index].title}”."
        for index in expected.keys() & actual.keys() if actual[index].title.strip().casefold() != expected[index].title.strip().casefold()
    ]
    structure_issues = [*(f"Missing {item}." for item in missing), *(f"Unexpected {item}." for item in extra), *mismatched]
    checks.append(_check(
        "Outline coverage", "Content", "FLAG" if structure_issues else "PASS",
        f"Compared {len(actual)} saved sections with {len(expected)} approved outline sections.",
        "Exact section-index and case-insensitive title comparison; partial drafts are flagged, not treated as complete.", structure_issues,
    ))

    blocks = _text_blocks(content)
    nonempty_sections = [section for section in content.sections if any((block.body.strip() or block.items or block.rows) for block in section.blocks)]
    empty_sections = [f"Section {section.section_index + 1} ({section.title})" for section in content.sections if section not in nonempty_sections]
    checks.append(_check(
        "Empty content sections", "Content", "FLAG" if empty_sections else "PASS",
        f"Checked all {len(content.sections)} saved sections for at least one non-empty block.",
        "Checks saved structured text, list items, and table rows; does not judge usefulness.", [f"{item} has no usable body, list item, or table row." for item in empty_sections],
    ))

    repetition_issues = []
    candidates = [(label, re.sub(r"\W+", " ", text.lower()).strip()) for label, text, _ in blocks if len(text.strip()) >= 35]
    seen: set[tuple[int, int]] = set()
    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            a, b = candidates[left][1], candidates[right][1]
            if len(a) < 35 or len(b) < 35:
                continue
            ratio = _shingle_similarity(a, b)
            if ratio >= 0.90 and (left, right) not in seen:
                seen.add((left, right))
                repetition_issues.append(f"{candidates[left][0]} and {candidates[right][0]} are near-duplicates (text similarity {ratio:.0%}); review for unnecessary repetition.")
    checks.append(_check(
        "Repeated content", "Content", "FLAG" if repetition_issues else "PASS",
        "Compared non-trivial content blocks for exact or very high textual similarity.",
        "Deterministic character 5-gram Jaccard threshold >= 90%; paraphrased or concept-level repetition may be missed, and similar labels may be intentional.", repetition_issues,
    ))

    contradiction_issues = _contradiction_candidates(blocks)
    checks.append(_check(
        "Contradiction candidates", "Content", "FLAG" if contradiction_issues else "PASS",
        "Searched for sentence pairs with identical wording after removing an explicit negation cue.",
        "Conservative negation-pattern heuristic only; it cannot establish semantic consistency, and a flagged pair needs human review.", contradiction_issues,
    ))

    grammar_issues = []
    for label, text, _ in blocks:
        for pattern, message in (
            (r"\s{2,}", "repeated spaces"),
            (r"\s+[,.!?;:]", "space before punctuation"),
            (r"[!?.,]{2,}", "repeated punctuation"),
            (r"\bi\b", "lowercase standalone ‘i’"),
        ):
            if re.search(pattern, text):
                grammar_issues.append(f"{label}: possible {message}.")
        for sentence in _sentence_candidates(text):
            first = re.search(r"[A-Za-z]", sentence)
            if first and sentence[first.start()].islower() and len(sentence) > 20:
                grammar_issues.append(f"{label}: sentence may start with lowercase text (“{sentence[:90]}”).")
                break
    checks.append(_check(
        "Grammar / formatting candidates", "Content", "FLAG" if grammar_issues else "PASS",
        "Scanned saved block text for a small set of common mechanical problems.",
        "Rules cover repeated spaces, spaces before punctuation, repeated punctuation, lowercase standalone ‘i’, and lowercase sentence starts; no general grammar or proofreading model is used.", grammar_issues,
    ))

    claim_issues = []
    known_ids = {item.evidence_id for item in inputs.evidence}
    used_ids = {evidence_id for _, _, block in blocks for evidence_id in block.evidence_ids}
    unknown_ids = sorted(used_ids - known_ids)
    for evidence_id in unknown_ids:
        claim_issues.append(f"Citation ID {evidence_id} is not among the evidence references supplied with this product.")
    for label, text, block in blocks:
        for match in ABSOLUTE_CLAIM_RE.finditer(text):
            excerpt = text[max(0, match.start() - 45):min(len(text), match.end() + 60)].replace("\n", " ")
            claim_issues.append(f"{label}: absolute/guarantee-style wording needs evidence and careful qualification (“{excerpt[:150]}”).")
        if NUMBER_CLAIM_RE.search(text) and not block.evidence_ids:
            match = NUMBER_CLAIM_RE.search(text)
            excerpt = text[max(0, match.start() - 45):min(len(text), match.end() + 60)].replace("\n", " ")
            claim_issues.append(f"{label}: specific numeric statement has no evidence ID on that block; verify or cite it (“{excerpt[:150]}”).")
    checks.append(_check(
        "Unsupported-claim candidates", "Content / evidence", "FLAG" if claim_issues else "PASS",
        "Checked supplied citation IDs and scanned for absolute/guarantee wording and specific numeric claims.",
        "Heuristic only: linked IDs are not proof that a claim is true, and claims without these patterns may still need sourcing. This check never invents or validates evidence.", claim_issues,
    ))

    filler_issues = []
    for label, text, _ in blocks:
        for phrase in FILLER_PHRASES:
            if phrase in text.lower():
                filler_issues.append(f"{label}: consider removing or replacing the filler phrase “{phrase}”.")
    checks.append(_check(
        "Common filler phrases", "Content", "FLAG" if filler_issues else "PASS",
        "Searched for a short, named list of generic filler phrases.",
        "Exact phrase list only; this does not measure whether prose is concise or useful.", filler_issues,
    ))

    vague_patterns = ("do this", "as needed", "handle accordingly", "do what works", "repeat as necessary", "etc.")
    instruction_issues = []
    for label, text, block in blocks:
        candidates = ([text] if block.kind in {"steps", "action_steps"} else list(block.items) if block.items else [])
        for value in candidates:
            if len(value.split()) <= 3:
                instruction_issues.append(f"{label}: short instruction (“{value[:100]}”) may need a concrete action or detail.")
            if any(phrase in value.lower() for phrase in vague_patterns):
                instruction_issues.append(f"{label}: vague instruction wording (“{value[:100]}”) may need a measurable next step.")
    checks.append(_check(
        "Unclear instruction candidates", "Content", "FLAG" if instruction_issues else "PASS",
        "Reviewed action-step blocks and list items for very short or named vague phrases.",
        "Length/phrase heuristics only; concise prompts can be intentional and clarity requires a human reader.", instruction_issues,
    ))

    content_text = " ".join(
        [section.title + " " + section.purpose for section in content.sections]
        + [text for _, text, _ in blocks]
    )

    problem_match_count, problem_total, _problem_matched = _concept_coverage(inputs.problem, content_text)
    if problem_total == 0:
        problem_status = "REVIEW"
        problem_message = "No distinctive problem concepts were available for deterministic coverage analysis."
        problem_issues = ["Review the researched problem manually and confirm that the saved sections directly address it."]
    else:
        problem_ratio = problem_match_count / problem_total
        problem_status = "PASS" if problem_ratio >= 0.30 else "REVIEW"
        problem_message = f"Matched {problem_match_count} of {problem_total} problem concepts using exact and simple morphological matching."
        problem_issues = [] if problem_status == "PASS" else [
            f"Only {problem_match_count} of {problem_total} problem concepts were found. Review whether the content addresses the researched problem rather than merely sharing its wording."
        ]
    checks.append(_check(
        "Problem concept coverage", "Product fit", problem_status,
        problem_message,
        "Deterministic exact/stem-aware term coverage; this is a relevance signal, not semantic proof.", problem_issues,
    ))

    outcome_match_count, outcome_total, _outcome_matched = _concept_coverage(blueprint.desired_outcome, content_text)
    if outcome_total == 0:
        outcome_status = "REVIEW"
        outcome_message = "No distinctive desired-outcome concepts were available for deterministic coverage analysis."
        outcome_issues = ["Review whether the product content makes the intended reader outcome clear."]
    else:
        outcome_ratio = outcome_match_count / outcome_total
        outcome_status = "PASS" if outcome_ratio >= min(0.45, 2 / max(1, outcome_total)) else "REVIEW"
        outcome_message = f"Matched {outcome_match_count} of {outcome_total} desired-outcome concepts using exact and simple morphological matching."
        outcome_issues = [] if outcome_status == "PASS" else [
            f"Only {outcome_match_count} of {outcome_total} outcome concepts were found. Review whether the saved activities actually lead toward the stated outcome."
        ]
    checks.append(_check(
        "Outcome concept coverage", "Product fit", outcome_status,
        outcome_message,
        "Deterministic exact/stem-aware term coverage; this is a relevance signal, not an achievement guarantee.", outcome_issues,
    ))

    reference_terms = _distinct_reference_terms(inputs.reference_material)
    reference_matches = sorted(reference_terms & _tokens(content_text))
    if inputs.reference_material.strip():
        reference_status = "PASS" if reference_matches else "REVIEW"
        reference_issues = [] if reference_matches else [
            "Creator/reference material was supplied, but no distinctive terms were found in saved content. Recheck that the generated product actually uses the supplied material."
        ]
        reference_message = f"Found {len(reference_matches)} distinctive supplied-material term(s) in saved product text."
    else:
        reference_status = "PASS"
        reference_issues = []
        reference_message = "No extra creator/reference material was supplied; this check has no additional requirement."
    checks.append(_check(
        "Creator material usage", "Product fit / evidence", reference_status,
        reference_message,
        "Exact distinctive-term presence only; it does not prove correct interpretation of supplied material.", reference_issues,
    ))

    if inputs.research_backed:
        evidence_count = len(inputs.evidence)
        evidence_status = "PASS" if evidence_count else "FLAG"
        evidence_issues = [] if evidence_count else [
            "This product came from a research-backed opportunity, but no linked evidence record was carried into the builder. Re-run or re-link the research before publication."
        ]
        evidence_message = f"{evidence_count} linked evidence reference(s) are attached to the product inputs."
    else:
        evidence_count = len(inputs.evidence)
        evidence_status = "PASS"
        evidence_issues = []
        evidence_message = f"Creator-topic mode: {evidence_count} supplied reference record(s) are attached; independent market evidence is not claimed."
    checks.append(_check(
        "Research evidence readiness", "Research / evidence", evidence_status,
        evidence_message,
        "Counts source references carried into the product builder; it does not assess source quality, independence, or truth.", evidence_issues,
    ))

    if inputs.research_backed:
        market_status = "PASS" if inputs.market_context else "REVIEW"
        market_issues = [] if inputs.market_context else [
            "No marketplace/listing context was attached. Similar-seller comparison is not verified in this product snapshot."
        ]
        market_message = (
            f"{len(inputs.market_context)} comparable marketplace/listing record(s) are attached for review."
            if inputs.market_context
            else "Marketplace comparison data is unavailable for this opportunity."
        )
        diff_status = "PASS" if inputs.differentiation else "REVIEW"
        diff_issues = [] if inputs.differentiation else [
            "No explicit differentiation statement is recorded. Compare the finished product directly with observed alternatives before publishing positioning claims."
        ]
        diff_message = (
            f"{len(inputs.differentiation)} differentiation statement(s) are recorded."
            if inputs.differentiation
            else "No explicit differentiation statement is recorded."
        )
    else:
        market_status, market_message, market_issues = "PASS", "Creator-topic mode: independent market comparison was not run.", []
        diff_status = "PASS"
        diff_message = f"{len(inputs.differentiation)} differentiation statement(s) supplied by the creator."
        diff_issues = []
    checks.append(_check(
        "Market comparison context", "Research / competition", market_status,
        market_message,
        "Presence/absence check on attached seller/listing context; listing presence does not prove a gap, demand, or sales.",
        market_issues,
    ))
    checks.append(_check(
        "Differentiation captured", "Product fit / competition", diff_status,
        diff_message,
        "Presence check only; meaningful differentiation still requires evidence-grounded side-by-side comparison.",
        diff_issues,
    ))

    action_kinds = {block.kind for _, _, block in blocks if block.kind in {"steps", "action_steps", "checklist", "exercise", "worksheet", "table", "reflection"}}
    actionable_issues = []
    if not action_kinds:
        actionable_issues.append("No steps, checklists, exercises, worksheets, reflection prompts, or tables were found; consider adding at least one practical component.")
    checks.append(_check("Actionable components", "Product fit", "FLAG" if actionable_issues else "PASS",
                         f"Found these practical component types: {', '.join(sorted(action_kinds)) or 'none'}.",
                         "Counts typed blocks only; component presence does not show that instructions are usable.", actionable_issues))

    expected_kinds = _expected_practical_kinds(blueprint.product_type)
    type_issues = []
    if expected_kinds and not (expected_kinds & action_kinds):
        type_issues.append(f"No practical block type associated with {blueprint.product_type} was found; expected at least one of: {', '.join(sorted(expected_kinds))}.")
    checks.append(_check("Structure for selected product type", "Product fit", "FLAG" if type_issues else "PASS",
                         f"Product type: {blueprint.product_type}. Applicable component expectations: {', '.join(sorted(expected_kinds)) or 'no type-specific rule configured'}.",
                         "Small explicit type-to-block rule map; does not decide whether a type choice is strategically correct.", type_issues))

    component_issues = []
    for section in sorted(content.sections, key=lambda item: item.section_index):
        outline_section = expected.get(section.section_index)
        if not outline_section:
            continue
        planned = {str(kind).strip().lower() for kind in outline_section.components}
        actual_kinds = {block.kind.strip().lower() for block in section.blocks}
        missing_components = sorted(planned - actual_kinds)
        if missing_components:
            component_issues.append(
                f"Section {section.section_index + 1} ({section.title}) is missing planned component(s): {', '.join(missing_components)}."
            )
    checks.append(_check(
        "Section component coverage", "Product fit", "FLAG" if component_issues else "PASS",
        "Compared every saved section with the exact content components planned in its approved blueprint.",
        "Case-insensitive set comparison of planned component kinds against saved structured block kinds; it checks presence, not quality or usefulness.",
        component_issues,
    ))

    practical_sections = {"workbook", "planner", "tracker", "worksheet", "action plan", "challenge", "playbook"}
    if blueprint.product_type.lower() in practical_sections and not (blueprint.exercises or blueprint.worksheets or blueprint.templates or blueprint.checklists):
        practical_issues = [f"Blueprint lists no exercises, worksheets, templates, or checklists for this {blueprint.product_type.lower()} concept; review whether additional practical material is needed."]
    else:
        practical_issues = []
    checks.append(_check("Practical material planned", "Product fit", "FLAG" if practical_issues else "PASS",
                         "Checked the blueprint's planned practical-material lists for formats that commonly need them.",
                         "Presence-only check; not every product needs every component, and a listed component may not have generated yet.", practical_issues))

    evidence_references = [item.model_dump(mode="json") for item in inputs.evidence]
    evidence_status = "PASS" if evidence_references else "FLAG"
    evidence_issues = [] if evidence_references else ["No directly linked source references were supplied; keep research claims qualified and validate the opportunity independently."]
    checks.append(_check("Research references supplied", "Product fit / evidence", evidence_status,
                         f"{len(evidence_references)} source references are attached to the product inputs.",
                         "Counts supplied structured references only; it does not assess source quality, independence, or whether product claims follow from them.", evidence_issues))

    checks.extend(_preview_checks(preview_html, content, design, assets))
    overall = "NEEDS REVISION" if any(item["status"] == "FLAG" for item in checks) else "PASS"
    return {
        "status": overall,
        "checks": checks,
        "limitations": [
            "This deterministic QA finds selected patterns and structural mismatches; it does not guarantee accuracy, safety, usefulness, demand, sales, or commercial success.",
            "Contradiction, grammar, claim support, semantic fit, clarity, and layout quality require human review; heuristics can miss issues and create false positives.",
            "Phase 7 QA does not assemble or inspect the separately exported product PDF. PDF-only overflow, final pagination, PDF page breaks, and final embedded-asset rendering are not validated by these checks.",
            "PASS means no finding was raised by the checks available on the current saved content and preview; it is not a certification or approval to publish.",
        ],
        "evidence_reference_count": len(evidence_references),
    }


def qa_snapshot_fingerprint(
    blueprint: ProductBlueprint,
    content: ProductContent,
    *,
    design: dict[str, str],
    visual_assets: list[dict] | None = None,
    preview_html: str | None = None,
) -> str:
    """Fingerprint precisely the inputs checked so saved results can be marked stale."""
    assets = []
    for asset in visual_assets or []:
        raw = asset.get("content", b"")
        assets.append({
            "asset_id": str(asset.get("asset_id", "")),
            "title": str(asset.get("title", "")),
            "mime_type": str(asset.get("mime_type", "")),
            "placement": str(asset.get("placement", "")),
            "content_sha256": hashlib.sha256(raw if isinstance(raw, bytes) else str(raw).encode("utf-8")).hexdigest(),
        })
    snapshot = {
        "blueprint": blueprint.model_dump(mode="json"),
        "content": content.model_dump(mode="json"),
        "design": design,
        "assets": assets,
        "preview_sha256": hashlib.sha256((preview_html or "").encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()



def final_verification(blueprint, content, inputs, *, design, visual_assets=None, preview_html=None, pdf_bytes=None) -> dict[str, Any]:
    """Final pre-publish gate combining saved-content QA with PDF-level structural checks."""
    result = run_product_qa(
        blueprint, content, inputs, design=design,
        visual_assets=visual_assets or [], preview_html=preview_html,
    )
    checks = list(result.get("checks", []))
    blocking = [c for c in checks if c.get("status") in {"FLAG", "NOT RUN"}]

    if pdf_bytes:
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(pdf_bytes))
            pages = list(reader.pages)
            pdf_issues = []
            if not pages:
                pdf_issues.append("PDF contains no pages.")
            page_texts = []
            for idx, page in enumerate(pages, start=1):
                text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
                page_texts.append(text)
                if not text:
                    pdf_issues.append(f"PDF page {idx} has no extractable text; inspect the page visually.")
            # Catch accidental repeated long passages across different pages while ignoring short labels.
            repeated = []
            for i in range(len(page_texts)):
                for j in range(i + 1, len(page_texts)):
                    a, b = page_texts[i], page_texts[j]
                    if len(a) < 100 or len(b) < 100:
                        continue
                    ratio = _shingle_similarity(a, b)
                    if ratio >= 0.92:
                        repeated.append(f"PDF pages {i + 1} and {j + 1} are near-duplicates ({ratio:.0%} similarity); inspect for repeated content.")
            pdf_issues.extend(repeated)
            checks.append(_check(
                "Final PDF structural verification", "Final PDF", "FLAG" if pdf_issues else "PASS",
                f"Inspected {len(pages)} exported PDF page(s) for readable structure and repeated long-page content.",
                "Uses pypdf text extraction plus a conservative 5-gram similarity check. It does not replace human visual inspection of typography, spacing, images, or print rendering.",
                pdf_issues,
            ))
        except Exception as exc:
            checks.append(_check(
                "Final PDF structural verification", "Final PDF", "NOT RUN",
                f"The exported PDF could not be structurally inspected ({type(exc).__name__}).",
                "PDF inspection requires a readable PDF byte stream.", [str(exc)],
            ))
    else:
        checks.append(_check(
            "Final PDF structural verification", "Final PDF", "NOT RUN",
            "No exported PDF was supplied to the final verification gate.",
            "Export the current saved product first, then run final verification again.",
        ))

    final_issues = [c for c in checks if c.get("status") in {"FLAG", "NOT RUN"}]
    result["checks"] = checks
    result["status"] = "PASS" if not final_issues else "NEEDS REVISION"
    result["final_verification"] = True
    result["limitations"] = list(result.get("limitations", [])) + [
        "Final verification is a deterministic pre-publish gate; it cannot judge taste, factual truth, commercial demand, or whether a visual is aesthetically ideal.",
        "Always open the final PDF once on the device where it will be delivered, especially when custom fonts, unusual symbols, or user-uploaded images are used.",
    ]
    return result
