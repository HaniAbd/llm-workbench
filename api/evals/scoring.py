"""How a classification is turned into a number.

Two kinds of field, because they are not the same kind of question:

  exact    The field has a right answer. `category` is nominal - "billing" is
           not nearer to "technical" than to "feedback" - so the only
           sensible credit is all or nothing. Arguable cases are handled by
           listing every value you would accept, not by inventing a distance.

  ordinal  The field is a scale. `urgency` runs low->medium->high and
           `sentiment` runs positive->neutral->frustrated->angry, so "one step
           out" is a genuinely different answer from "the opposite end", and
           collapsing both to zero throws away the signal a prompt edit is
           most likely to move.

In both cases the expectation is a *set* of acceptable values. Nothing here
asks for a single ideal answer to a question that does not have one; graded
credit is measured to the nearest member of that set, not to a "correct" one.

The rules live in a dict rather than in code so they can be hashed. Change any
credit value or any scale and CREDIT_DIGEST changes, which is what stops a
score quietly meaning something new from one run to the next.
"""

import json
from hashlib import sha256

SCORING_RULES = {
    "version": 1,
    "fields": {
        # The hard contract. A wrong status means the rest is meaningless, and
        # the runner zeroes the other fields when it misses.
        "status": {"kind": "exact"},
        "is_support_ticket": {"kind": "exact"},
        "category": {"kind": "exact"},
        "requires_human": {"kind": "exact"},
        "urgency": {
            "kind": "ordinal",
            "scale": ["low", "medium", "high"],
            # distance from the nearest acceptable value -> credit
            "credit": {"0": 1.0, "1": 0.5},
        },
        "sentiment": {
            "kind": "ordinal",
            "scale": ["positive", "neutral", "frustrated", "angry"],
            "credit": {"0": 1.0, "1": 0.5},
        },
    },
}

SCORED_FIELDS = tuple(SCORING_RULES["fields"])


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def digest(obj) -> str:
    return sha256(_canonical(obj).encode("utf-8")).hexdigest()[:12]


# Identifies the scoring method itself. A run carries it, and two runs with
# different digests are not comparable no matter how similar the numbers look.
SCORER_DIGEST = digest(SCORING_RULES)


def score_field(field: str, expected: list, actual) -> float:
    """Credit in [0, 1] for one field against the values that would be accepted."""
    rule = SCORING_RULES["fields"][field]

    if actual in expected:
        return 1.0
    if rule["kind"] == "exact":
        return 0.0

    scale = rule["scale"]
    if actual not in scale:
        return 0.0
    # Nearest acceptable value on the scale, in steps.
    steps = min(
        abs(scale.index(actual) - scale.index(e)) for e in expected if e in scale
    )
    return float(rule["credit"].get(str(steps), 0.0))


def score_case(expect: dict, body: dict) -> dict[str, float]:
    """Per-field credit for one case. Only fields the case states are scored."""
    return {
        field: score_field(field, expect[field], body.get(field))
        for field in SCORED_FIELDS
        if field in expect
    }
