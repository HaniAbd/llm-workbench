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
from index_docs import (
    EXCLUDED_PATHS,
    REPO,
    NotIndexable,
    check_indexable,
    documents,
    index_document,
)

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


# --- excluding a second kind of thing ---------------------------------------
#
# The prompt exclusion was the only one for a while. Scaffolding - a generated
# file, a pointer file - is the second, and the second exclusion is where a
# rule usually turns into a special case. These check that it did not.


@pytest.mark.parametrize("rel, reason", [
    ("web/AGENTS.md", "next dev"),
    ("web/CLAUDE.md", "pointer"),
])
def test_scaffolding_is_excluded_with_its_own_reason(rel, reason):
    """Each exclusion says why it exists, in its own words.

    One shared reason across unlike things would be the giveaway that the
    second exclusion was bolted onto the first.
    """
    assert _abs(rel) not in documents()
    with pytest.raises(NotIndexable, match=reason):
        index_document(None, _abs(rel))


def test_adding_an_exclusion_is_data_not_code(monkeypatch):
    """The property worth having: a new exclusion needs no change to
    `_rejection`, only an entry with a reason.

    Asserted by adding one at runtime. If this ever needs a branch somewhere
    to pass, the rule has become a special case again.
    """
    monkeypatch.setitem(EXCLUDED_PATHS, "README.md", "excluded by this test")
    with pytest.raises(NotIndexable, match="excluded by this test"):
        check_indexable(REPO / "README.md")
    assert (REPO / "README.md") not in documents()


def test_every_exclusion_carries_a_reason():
    """An exclusion with no reason is an exclusion nobody can argue with."""
    for pattern, reason in EXCLUDED_PATHS.items():
        assert reason.strip(), f"{pattern} is excluded without saying why"
        assert len(reason) > 20, f"{pattern}'s reason explains nothing"


def test_a_directory_prefix_and_an_exact_path_behave_differently():
    """Prompts are a prefix, so the next one added is covered. A named file is
    exact, so it cannot swallow a neighbour nobody excluded."""
    assert "api/prompts/" in EXCLUDED_PATHS
    assert "web/CLAUDE.md" in EXCLUDED_PATHS

    # prefix: anything beneath it
    with pytest.raises(NotIndexable):
        check_indexable(_abs("api/prompts/anything/at/all.md"))

    # exact: a longer name starting with it is a different file. It does not
    # exist, so the refusal must be about that rather than about exclusion.
    with pytest.raises(NotIndexable, match="not a file"):
        check_indexable(_abs("web/CLAUDE.md.bak.md"))


# --- what the corpus is now -------------------------------------------------


def test_the_corpus_holds_only_documents_written_for_this_project():
    listed = {p.relative_to(REPO).as_posix() for p in documents()}
    assert listed == {"CLAUDE.md", "README.md", "api/README.md", "web/README.md"}


def test_the_front_end_readme_was_rewritten_rather_than_excluded():
    """It was stock create-next-app text; it is in the corpus because it now
    says something true about this project, not because it was left alone."""
    text = (REPO / "web/README.md").read_text(encoding="utf-8")
    assert "create-next-app" not in text
    assert "Deploy on Vercel" not in text
    for real in ("NEXT_PUBLIC_API_URL", "check:api", "/agent"):
        assert real in text, f"the front-end README does not mention {real}"
