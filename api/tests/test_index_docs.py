"""The corpus boundary: what may be indexed, and where that is decided.

The bug these exist for: the prompt exclusion lived in `documents()` only, so
naming a prompt file explicitly walked past it and indexed the model's own
instructions into the corpus it answers from. A rule enforced on one path out
of two is not a rule, so what is tested here is mostly that there is no second
path - the checks are made against `index_document` itself, the one function
that writes, rather than against the CLI that happens to call it.

No model and no database: every case here is refused before either is reached.
`conn=None` is deliberate - a refusal that tried to write would raise
AttributeError instead, and the test would notice.
"""

from pathlib import Path

import pytest

import index_docs
from index_docs import REPO, NotIndexable, check_indexable, documents, index_document

PROMPT = "api/prompts/classify_ticket.md"


def _abs(rel: str) -> Path:
    return REPO / rel


# --- the exclusion holds wherever a document is indexed ---------------------


@pytest.mark.parametrize("rel, reason", [
    (PROMPT, "prompt"),
    ("api/prompts/nested/deeper.md", "prompt"),
    ("api/main.py", "not a markdown file"),
    ("api/does-not-exist.md", "not a file"),
    (".venv/lib/thing.md", "inside an excluded directory"),
])
def test_index_document_refuses_rather_than_trusting_its_caller(rel, reason):
    """The check is on the function that writes, so no caller can skip it.

    Asserting the reason, not just the refusal: three of these would be
    refused by the `.md`/is-a-file checks even with the exclusion gone, and a
    bare `raises` would call that a pass.
    """
    with pytest.raises(NotIndexable, match=reason):
        index_document(None, _abs(rel))


def test_index_document_refuses_a_path_outside_the_repository():
    with pytest.raises(NotIndexable, match="outside the repository"):
        index_document(None, Path("/etc/hosts"))


def test_a_prompt_cannot_be_indexed_by_any_route():
    """The original hole, from both directions.

    `documents()` excluded it and `index_document` did not, so naming it
    explicitly indexed it. Both must refuse now.
    """
    assert _abs(PROMPT).is_file(), "precondition: the prompt file exists"
    assert _abs(PROMPT) not in documents()
    with pytest.raises(NotIndexable, match="prompt"):
        check_indexable(_abs(PROMPT))
    with pytest.raises(NotIndexable):
        index_document(None, _abs(PROMPT))


def test_the_refusal_says_why_a_prompt_is_excluded():
    """The reason is the point - 'excluded' alone invites someone to override
    it. It must say what indexing a prompt would cost."""
    with pytest.raises(NotIndexable) as raised:
        check_indexable(_abs(PROMPT))
    assert "own instructions" in str(raised.value)


# --- one definition, not two ----------------------------------------------


def test_documents_and_the_check_cannot_disagree():
    """Every document the corpus lists must also pass the gate that guards it.

    This is what stops the bug coming back in the other direction: if the two
    were defined separately, `documents()` could offer something
    `index_document` then refuses, and a full run would fail halfway.
    """
    listed = documents()
    assert listed, "precondition: the corpus is not empty"
    for path in listed:
        assert check_indexable(path) == path.resolve()


def test_the_corpus_is_real_markdown_and_holds_no_prompts():
    listed = documents()
    assert all(p.suffix == ".md" for p in listed)
    assert not [p for p in listed
                if p.relative_to(REPO).as_posix().startswith("api/prompts/")]


def test_adding_a_prompt_needs_no_change_here():
    """The exclusion is a prefix, so a prompt that does not exist yet - and one
    in a subdirectory nobody has created - is already covered."""
    for future in ("api/prompts/not-written-yet.md",
                   "api/prompts/some/deeper/one.md"):
        with pytest.raises(NotIndexable, match="prompt"):
            check_indexable(_abs(future))


def test_a_valid_document_resolves_rather_than_being_returned_verbatim():
    messy = REPO / "api" / ".." / "api" / "README.md"
    assert check_indexable(messy) == (REPO / "api/README.md").resolve()


# --- the CLI reports a refusal instead of a traceback ----------------------


def _run_cli(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["index_docs.py", "--dry-run", *argv])
    return index_docs.main()


def test_cli_refuses_a_prompt_file_and_says_so(monkeypatch, capsys):
    assert _run_cli(monkeypatch, PROMPT) == 1
    assert "refused" in capsys.readouterr().err


def test_cli_refuses_a_path_outside_the_repository_without_a_traceback(
        monkeypatch, capsys):
    """This used to be an uncaught ValueError from `relative_to`."""
    assert _run_cli(monkeypatch, "/etc/hosts") == 1
    assert "outside the repository" in capsys.readouterr().err


def test_one_bad_path_refuses_the_whole_batch(monkeypatch, capsys):
    """Refusing halfway would leave the index partly updated with nothing to
    say which half."""
    assert _run_cli(monkeypatch, "../README.md", PROMPT) == 1
    err = capsys.readouterr().err
    assert "nothing was indexed" in err
    assert "refused 1 of 2" in err


def test_cli_still_accepts_a_real_document(monkeypatch, capsys):
    assert _run_cli(monkeypatch, "../README.md") == 0
    assert "would index" in capsys.readouterr().out
