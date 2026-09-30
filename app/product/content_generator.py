                    "schema": SECTION_CONTENT_JSON_SCHEMA,
                },
            },
            **_token_limit(model),
        )
        raw = response.choices[0].message.content
        if not raw:
            raise ValueError("The provider returned an empty section.")
        payload = json.loads(raw)
        generated = GeneratedSection(
            section_index=section_index,
            title=section.title,
            purpose=section.purpose,
            blocks=payload["blocks"],
        )
        _validate_component_contract(section, generated)
        _validate_content_grounding(blueprint, section, generated, product_inputs)
        used_ids = {evidence_id for block in generated.blocks for evidence_id in block.evidence_ids}
        unknown_ids = used_ids - allowed_ids
        if unknown_ids:
            raise ValueError("Generated content cited a source that was not supplied.")
        return generated
    except ContentGenerationError:
        raise
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("Generated content grounding check failed:"):
            raise ContentGenerationError(detail) from exc
        name = type(exc).__name__
        raise ContentGenerationError(
            f"Section content generation failed ({name}). Check the configured provider/model and try again. Previously saved sections are preserved."
        ) from exc
    except Exception as exc:
        name = type(exc).__name__
        raise ContentGenerationError(
            f"Section content generation failed ({name}). Check the configured provider/model and try again. Previously saved sections are preserved."
        ) from exc