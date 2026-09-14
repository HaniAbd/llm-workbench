"""Can the eval runner still read the dataset? This is the check that would
catch a schema change the runner can no longer parse, without a model."""

import json
from pathlib import Path

import pytest

import scoring

CASES = json.loads(
    (Path(__file__).resolve().parent.parent / "evals" / "cases.json").read_text()
)["cases"]
IDS = [c["id"] for c in CASES]

# A response shaped like a real one. Values are deliberately arbitrary: the
# point is that scoring runs and produces a number, not that it scores well.
SYNTHETIC = {
    "status": 200,
    "is_support_ticket": True,
    "category": "billing",
    "urgency": "low",
    "sentiment": "neutral",
    "requires_human": False,
}


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_case_can_be_scored(case):
    scores = scoring.score_case(case["expect"], SYNTHETIC)
    assert scores, f"{case['id']} scored no fields at all"
    for field, value in scores.items():
        assert isinstance(value, float)
        assert 0.0 <= value <= 1.0


def test_scored_fields_cover_every_field_used_by_the_dataset():
    used = {f for c in CASES for f in c["expect"]}
    assert used <= set(scoring.SCORED_FIELDS)


def test_a_perfect_response_scores_one_for_that_case():
    """Sanity check on the scorer itself: feeding back a case's own first
    acceptable value must score 1.0 on every field it states."""
    for case in CASES:
        ideal = {f: vals[0] for f, vals in case["expect"].items()}
        scores = scoring.score_case(case["expect"], ideal)
        assert all(v == 1.0 for v in scores.values()), case["id"]
