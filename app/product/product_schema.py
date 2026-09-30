from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DESIGN_TEMPLATE_IDS = ["minimal_professional", "modern_business", "clean_workbook"]
PAGE_SIZE_OPTIONS = ["letter", "a4"]
PRODUCT_TYPES = [
    "Ebook",
    "Playbook",
    "Workbook",
    "Planner",
    "Checklist",
    "Guide",
    "Journal",
    "Tracker",
    "Action Plan",
    "Challenge",
    "Template",
    "Worksheet",
]


class ProductDesign(BaseModel):
    """Persisted design choice for in-app layout rendering; not an export setting."""

    model_config = ConfigDict(extra="forbid")

    template_id: str = "minimal_professional"
    page_size: str = "letter"

    @field_validator("template_id")
    @classmethod
    def valid_template(cls, value: str) -> str:
        if value not in DESIGN_TEMPLATE_IDS:
            raise ValueError("Choose one of the available product design templates.")
        return value

    @field_validator("page_size")
    @classmethod
    def valid_page_size(cls, value: str) -> str:
        if value not in PAGE_SIZE_OPTIONS:
            raise ValueError("Choose A4 or US Letter for the design canvas.")
        return value


class EvidenceReference(BaseModel):
    """A source record already present in the research report; never AI-invented."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_title: str
    source_type: str
    customer_language: str
    url: str = ""


class ProductInputs(BaseModel):
    """Research-derived fields loaded into the builder and editable by the user."""

    model_config = ConfigDict(extra="forbid")

    product_title: str = Field(min_length=1, max_length=160)
    audience: str = Field(min_length=1, max_length=600)
    problem: str = Field(min_length=1, max_length=1200)
    promise: str = Field(min_length=1, max_length=900)
    format_hints: List[str] = Field(default_factory=list, max_length=12)
    differentiation: List[str] = Field(default_factory=list, max_length=12)
    evidence: List[EvidenceReference] = Field(default_factory=list, max_length=10)
    validation_steps: List[str] = Field(default_factory=list, max_length=12)
    reference_material: str = Field(default="", max_length=50000)
    research_backed: bool = False
    market_context: List[str] = Field(default_factory=list, max_length=12)

    @field_validator("format_hints", "differentiation", "validation_steps", "market_context")
    @classmethod
    def trim_list_values(cls, values: List[str]) -> List[str]:
        return [str(value).strip() for value in values if str(value).strip()]


class BlueprintSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=500)
    components: List[str] = Field(default_factory=list, max_length=12)

    @field_validator("components")
    @classmethod
    def valid_components(cls, values: List[str]) -> List[str]:
        allowed = {
            "paragraph", "steps", "example", "exercise", "checklist",
            "worksheet", "table", "reflection", "action_steps", "reference",
        }
        cleaned = [str(value).strip().lower() for value in values if str(value).strip()]
        invalid = [value for value in cleaned if value not in allowed]
        if invalid:
            raise ValueError(f"Unsupported content component(s): {', '.join(sorted(set(invalid)))}")
        return list(dict.fromkeys(cleaned))


class ProductBlueprint(BaseModel):
    """Editable strategy blueprint. It is not a completed product or demand claim."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    subtitle: str = Field(min_length=1, max_length=260)
    target_audience: str = Field(min_length=1, max_length=600)
    core_problem: str = Field(min_length=1, max_length=1200)
    desired_outcome: str = Field(min_length=1, max_length=600)
    promise: str = Field(min_length=1, max_length=900)
    product_type: str
    recommended_types: List[str] = Field(min_length=1, max_length=3)
    recommendation_reason: str = Field(min_length=1, max_length=700)
    outline: List[BlueprintSection] = Field(min_length=3, max_length=12)
    estimated_page_count: int = Field(ge=3, le=120)
    exercises: List[str] = Field(default_factory=list, max_length=15)
    checklists: List[str] = Field(default_factory=list, max_length=15)
    worksheets: List[str] = Field(default_factory=list, max_length=15)
    examples: List[str] = Field(default_factory=list, max_length=15)
    templates: List[str] = Field(default_factory=list, max_length=15)
    bonuses: List[str] = Field(default_factory=list, max_length=10)
    design_direction: str = Field(min_length=1, max_length=800)

    @field_validator("product_type")
    @classmethod
    def valid_product_type(cls, value: str) -> str:
        if value not in PRODUCT_TYPES:
            raise ValueError("Choose a supported product type.")
        return value

    @field_validator("recommended_types")
    @classmethod
    def valid_recommendations(cls, values: List[str]) -> List[str]:
        if not values or any(value not in PRODUCT_TYPES for value in values):
            raise ValueError("Recommendations must use supported product types.")
        return list(dict.fromkeys(values))

    @field_validator("exercises", "checklists", "worksheets", "examples", "templates", "bonuses")
    @classmethod
    def trim_blueprint_lists(cls, values: List[str]) -> List[str]:
        return [str(value).strip() for value in values if str(value).strip()]


ContentBlockKind = Literal[
    "paragraph", "steps", "example", "exercise", "checklist", "worksheet",
    "table", "reflection", "action_steps", "reference",
]


class ContentBlock(BaseModel):
    """A typed reusable content component for a future template renderer."""

    model_config = ConfigDict(extra="forbid")

    kind: ContentBlockKind
    title: str = Field(default="", max_length=120)
    body: str = Field(default="", max_length=5000)
    items: List[str] = Field(default_factory=list, max_length=30)
    columns: List[str] = Field(default_factory=list, max_length=8)
    rows: List[List[str]] = Field(default_factory=list, max_length=20)
    evidence_ids: List[str] = Field(default_factory=list, max_length=10)

    @field_validator("items", "columns", "evidence_ids")
    @classmethod
    def trim_items(cls, values: List[str]) -> List[str]:
        return [str(value).strip() for value in values if str(value).strip()]

    @model_validator(mode="after")
    def validate_component_content(self):
        if not (self.body.strip() or self.items or self.rows):
            raise ValueError("Each content block must contain text, items, or table rows.")
        if self.kind == "table":
            if len(self.columns) < 2:
                raise ValueError("A table needs at least two column headings.")
            if not self.rows or any(len(row) != len(self.columns) for row in self.rows):
                raise ValueError("Every table row must match the number of column headings.")
        elif self.columns or self.rows:
            raise ValueError("Table columns and rows are only valid for table blocks.")
        if self.kind == "reference" and not self.evidence_ids:
            raise ValueError("A reference block must cite at least one supplied evidence ID.")
        if self.kind != "reference" and self.evidence_ids:
            raise ValueError("Evidence IDs may only be attached to reference blocks.")
        return self


class GeneratedSection(BaseModel):
    """Generated content for exactly one approved outline section."""

    model_config = ConfigDict(extra="forbid")

    section_index: int = Field(ge=0, le=11)
    title: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=500)
    blocks: List[ContentBlock] = Field(min_length=1, max_length=24)


class ProductContent(BaseModel):
    """Persistable, partially or fully generated content tied to a blueprint version."""

    model_config = ConfigDict(extra="forbid")

    product_title: str = Field(min_length=1, max_length=160)
    subtitle: str = Field(default="", max_length=260)
    blueprint_fingerprint: str = Field(min_length=16, max_length=64)
    sections: List[GeneratedSection] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unique_section_indices(self):
        indices = [section.section_index for section in self.sections]
        if len(indices) != len(set(indices)):
            raise ValueError("A product content draft cannot contain duplicate sections.")
        return self

    def is_complete(self, expected_sections: int) -> bool:
        return expected_sections > 0 and len(self.sections) == expected_sections
