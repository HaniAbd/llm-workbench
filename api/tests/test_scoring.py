"""The scorer. A drift here changes what every recorded score means."""

import classification
import scoring


def test_ordinal_scales_match_the_schema_enums_exactly():
    """Adding a category or reordering urgency without updating the scorer
    would silently change graded credit."""
    expected = {
        "urgency": list(classification.URGENCIES),
        "sentiment": list(classification.SENTIMENTS),
    }
    for field, scale in expected.items():
        rule = scoring.SCORING_RULES["fields"][field]
        assert rule["kind"] == "ordinal"
        assert rule["scale"] == scale, f"{field} scale has drifted from the schema"


def test_exact_fields_are_real_response_fields():
    model_fields = set(classification.Classification.model_fields)
    for field, rule in scoring.SCORING_RULES["fields"].items():
        if rule["kind"] == "exact" and field != "status":
            assert field in model_fields, f"{field} is scored but not returned"


def test_credit_values_are_in_range_and_decreasing():
    for field, rule in scoring.SCORING_RULES["fields"].items():
        if rule["kind"] != "ordinal":
            continue
        credits = [rule["credit"][k] for k in sorted(rule["credit"], key=int)]
        assert credits[0] == 1.0, f"{field}: zero distance must score 1.0"
        assert all(0.0 <= c <= 1.0 for c in credits)
        assert credits == sorted(credits, reverse=True), f"{field}: credit must not rise with distance"


def test_digest_changes_when_a_rule_changes():
    """The guarantee that old scores cannot silently mean something new."""
    before = scoring.digest(scoring.SCORING_RULES)
    mutated = {
        **scoring.SCORING_RULES,
        "fields": {
            **scoring.SCORING_RULES["fields"],
            "urgency": {
                **scoring.SCORING_RULES["fields"]["urgency"],
                "credit": {"0": 1.0, "1": 0.25},
            },
        },
    }
    assert scoring.digest(mutated) != before


def test_score_field_behaviour():
    assert scoring.score_field("category", ["billing"], "billing") == 1.0
    assert scoring.score_field("category", ["billing"], "technical") == 0.0
    assert scoring.score_field("urgency", ["high"], "medium") == 0.5
    assert scoring.score_field("urgency", ["high"], "low") == 0.0
    assert scoring.score_field("urgency", ["high"], "nonsense") == 0.0
