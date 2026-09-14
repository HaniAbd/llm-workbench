"""Prompts are files, so nothing type-checks them. These are the failures that
would otherwise appear only when a request is served."""

import pytest

import classification
import prompts


def test_every_prompt_file_loads():
    names = prompts.available()
    assert names, "no prompt files found"
    for name in names:
        # Templated prompts need their variables; those are covered below. A
        # prompt with no placeholders must load bare.
        if name == "classify_ticket":
            continue
        p = prompts.get(name)
        assert p.text.strip(), f"{name} is empty"
        assert p.id.startswith(f"{name}@")


def test_classify_prompt_renders_with_the_variables_the_code_passes():
    """The real failure mode: a $placeholder added to the .md that
    classification.py does not pass, or a variable removed from the call."""
    p = classification._prompt()
    assert p.text.strip()
    assert "$" not in p.text, "unrendered placeholder left in the prompt"


def test_rendered_prompt_lists_the_allowed_values():
    """Guards the $placeholder lines themselves.

    Checking that each value merely appears *somewhere* is too weak to fail:
    the per-value definitions below the list would satisfy it even with the
    whole `category $categories` line deleted. Asserting the rendered list is
    present catches a deleted or renamed placeholder, which would leave a
    prompt that never states the legal values."""
    text = classification._prompt().text
    for label, values in (
        ("categories", classification.CATEGORIES),
        ("urgencies", classification.URGENCIES),
        ("sentiments", classification.SENTIMENTS),
    ):
        joined = " | ".join(values)
        assert joined in text, f"the ${label} list is missing from the rendered prompt"


def test_missing_variable_is_an_error_not_a_silent_gap():
    with pytest.raises(KeyError):
        prompts.get("classify_ticket", categories="a")


def test_missing_prompt_file_raises_promptnotfound():
    with pytest.raises(prompts.PromptNotFound):
        prompts.get("no_such_prompt")


def test_identity_is_content_addressed():
    a = prompts.get("chat_system")
    b = prompts.get("chat_system")
    assert a.id == b.id, "same text must give the same id"
