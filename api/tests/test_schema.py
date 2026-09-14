"""The JSON schema handed to the provider. Breaking it breaks classification at
request time only."""

import json

import classification


def test_schema_has_no_refs():
    """Ollama does not resolve $ref in a format schema. Swapping a Literal for
    an Enum silently reintroduces them."""
    schema = classification._SCHEMA
    assert "$defs" not in schema
    assert "$ref" not in json.dumps(schema)


def test_schema_forbids_extra_properties():
    """additionalProperties:false is what stops the model inventing fields."""
    assert classification._SCHEMA["additionalProperties"] is False


def test_schema_enums_match_the_declared_values():
    props = classification._SCHEMA["properties"]
    assert props["category"]["enum"] == list(classification.CATEGORIES)
    assert props["urgency"]["enum"] == list(classification.URGENCIES)
    assert props["sentiment"]["enum"] == list(classification.SENTIMENTS)


def test_schema_does_not_leak_response_only_fields():
    """`prompt_id` and `trace` belong to the response, not to what the model is
    asked to generate."""
    props = classification._SCHEMA["properties"]
    assert "prompt_id" not in props
    assert "trace" not in props


def test_non_ticket_constant_is_valid_against_the_schema():
    assert classification.NOT_A_TICKET.is_support_ticket is False
    assert classification.NOT_A_TICKET.category in classification.CATEGORIES
