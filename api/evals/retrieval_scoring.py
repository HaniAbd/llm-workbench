"""How a retrieval answer is turned into numbers.

Two numbers, never one. Retrieval and generation fail independently: the index
can miss the right passage, or it can find it and the model can still answer
badly from it. A blended score says something went wrong without saying which
half to fix, so they are kept apart all the way through - including in the
run record, so old runs stay separable too.

  retrieval   Did the right passage come back at all? Recall over the expected
              passages, order ignored, because a passage in position four was
              still put in front of the model. Fractional, so a question whose
              answer spans two documents can score 0.5 for finding one.
              Cases the documentation does not answer have nothing to find,
              so their retrieval score is None - not zero, which would read as
              a failure of the index.

  answer      For answerable cases: the fraction of required facts present.
              Facts are given as alternatives, so a paraphrase that uses a
              different word for the same thing still counts.
              For unanswerable cases: binary. The answer must decline, and
              must not contain any of the claims it might otherwise invent.
              A half-refusal is not a thing.

Why required facts rather than a judge: a second model call would double the
run and put llama3.2's own noise into every score, and the point of the suite
is to detect a change in retrieval, not in the judge's mood. The cost is real
and worth stating - a valid paraphrase nobody anticipated scores zero, so the
fact lists are written as alternatives and are meant to be edited when that
happens.
"""

import json
import re
from hashlib import sha256

SCORING_RULES = {
    "version": 1,
    "retrieval": {
        "kind": "recall",
        "order_sensitive": False,
        "match": "case-insensitive substring of a retrieved passage, within the expected source",
        "unanswerable": "not scored (None)",
    },
    "answer": {
        "answerable": {
            "kind": "required_facts",
            "credit": "fraction of fact groups satisfied; a group is satisfied by any alternative",
        },
        "unanswerable": {
            "kind": "refusal",
            "credit": "1.0 only if a refusal marker appears and no forbidden claim does; else 0.0",
            "forbidden_match": "word-boundary, so 'MIT' does not fire on 'limit'",
            # In the rules, therefore hashed: changing what counts as a
            # refusal changes the digest and old scores stop being comparable.
            "refusal_markers": [
                "could not find", "couldn't find", "cannot find", "can't find",
                "unable to find", "did not find", "didn't find",
                "no information", "not have information", "don't have information",
                "no mention", "not mention", "does not mention", "doesn't mention",
                "not contain", "does not contain", "doesn't contain",
                "not covered", "not provided", "not provide", "not in the provided",
                "not specify", "does not specify", "doesn't specify",
                "not available in", "no reference to",
            ],
        },
    },
}


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def digest(obj) -> str:
    return sha256(_canonical(obj).encode("utf-8")).hexdigest()[:12]


SCORER_DIGEST = digest(SCORING_RULES)
METRICS = ("retrieval", "answer")

_WS = re.compile(r"\s+")


def contains_claim(text: str, term: str) -> bool:
    """Word-boundary match, not a bare substring.

    A forbidden claim like "MIT" or "Paris" appears inside "li-mit-" and
    "com-paris-on"; a plain `in` test would fail a correct refusal because the
    answer happened to use an ordinary English word.
    """
    return re.search(r"\b" + re.escape(term), text) is not None


# Expanded before matching so a refusal marker needs one form, not two:
# "doesn't provide" does not contain "not provide", and listing every
# contraction separately is how a marker list quietly stops covering things.
_CONTRACTIONS = {
    "doesn't": "does not", "don't": "do not", "didn't": "did not",
    "isn't": "is not", "wasn't": "was not", "aren't": "are not",
    "couldn't": "could not", "can't": "cannot", "won't": "will not",
    "haven't": "have not", "hasn't": "has not", "wouldn't": "would not",
}


def normalise(text: str) -> str:
    """Lowercase, collapse whitespace, straighten quotes, expand contractions.

    Passages are wrapped markdown, so a phrase can be split across a newline;
    the model writes curly apostrophes about as often as straight ones.
    """
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = _WS.sub(" ", text).strip().lower()
    for short, long in _CONTRACTIONS.items():
        text = text.replace(short, long)
    return text


def score_retrieval(expected: list, retrieved: list) -> float | None:
    """Fraction of expected passages that came back, order ignored.

    `expected` entries are {source, contains}: a distinctive phrase rather than
    a heading path, so re-chunking or renaming a heading does not break the
    dataset for a reason that has nothing to do with retrieval.
    """
    if not expected:
        return None
    hay = [(r["source"], normalise(r.get("text", ""))) for r in retrieved]
    found = sum(
        any(src == e["source"] and normalise(e["contains"]) in text for src, text in hay)
        for e in expected
    )
    return round(found / len(expected), 4)


def matched_passages(expected: list, retrieved: list) -> list[bool]:
    """Which expectations were satisfied, for the report."""
    hay = [(r["source"], normalise(r.get("text", ""))) for r in retrieved]
    return [
        any(src == e["source"] and normalise(e["contains"]) in text for src, text in hay)
        for e in expected
    ]


def refused(answer: str) -> bool:
    a = normalise(answer)
    return any(m in a for m in SCORING_RULES["answer"]["unanswerable"]["refusal_markers"])


def score_answer(expect: dict, answer: str) -> float:
    a = normalise(answer)

    if expect.get("must_refuse"):
        if any(contains_claim(a, normalise(f)) for f in expect.get("must_not_contain", [])):
            return 0.0
        return 1.0 if refused(a) else 0.0

    groups = expect.get("must_contain", [])
    if not groups:
        return None
    satisfied = sum(any(normalise(alt) in a for alt in group) for group in groups)
    return round(satisfied / len(groups), 4)


def score_case(case: dict, retrieved: list, answer: str) -> dict:
    return {
        "retrieval": score_retrieval(case["expect"].get("passages", []), retrieved),
        "answer": score_answer(case["expect"], answer),
    }
