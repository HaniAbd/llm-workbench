"""The eval dataset. A typo here does not fail loudly - it scores zero forever
and reads as a model failure."""

import json
from pathlib import Path

import pytest

import classification
import scoring

CASES_PATH = Path(__file__).resolve().parent.parent / "evals" / "cases.json"
CASES = json.loads(CASES_PATH.read_text())["cases"]
ENUMS = {
    "category": set(classification.CATEGORIES),
    "urgency": set(classification.URGENCIES),
    "sentiment": set(classification.SENTIMENTS),
}
IDS = [c["id"] for c in CASES]


def test_dataset_is_not_empty():
    assert CASES


def test_ids_are_unique():
    assert len(IDS) == len(set(IDS))


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_case_is_well_formed(case):
    assert case["text"] is not None
    assert case.get("group"), "every case needs a group"
    assert case.get("note"), "every case needs a note explaining what it is for"
    assert case["expect"].get("status"), "every case must state an expected status"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_expected_values_are_legal(case):
    """Catches `"biling"`, a field renamed in the schema, and a bare value
    written where a list of acceptable values belongs."""
    for field, values in case["expect"].items():
        assert field in scoring.SCORED_FIELDS, f"{field} is not a scored field"
        assert isinstance(values, list) and values, f"{field} must be a non-empty list"
        if field in ENUMS:
            illegal = [v for v in values if v not in ENUMS[field]]
            assert not illegal, f"{field}: {illegal} not in the schema enum"
        if field == "is_support_ticket" or field == "requires_human":
            assert all(isinstance(v, bool) for v in values)
        if field == "status":
            assert all(isinstance(v, int) for v in values)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_accepted_failures_name_fields_the_case_actually_expects(case):
    """An acceptance for a field the case does not score is dead weight that
    silently hides nothing."""
    for field in case.get("accepted_failures", []):
        assert field in case["expect"], f"accepted failure {field!r} is not expected here"


def test_rejected_cases_score_no_model_fields():
    """A 422 never reaches the model, so expecting classification fields on one
    would be unscoreable."""
    for case in CASES:
        if case["expect"]["status"] == [422]:
            assert set(case["expect"]) == {"status"}, case["id"]
