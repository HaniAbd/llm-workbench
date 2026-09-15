"""Markdown chunking. Pure logic, no model and no database - and a silent
breakage here degrades every answer without any error appearing."""

import chunking


def test_splits_on_headings_not_length():
    # Both bodies are comfortably over MIN_CHARS; below it they would be
    # merged on purpose, which test_tiny_sections_are_merged covers.
    md = "# A\n" + "alpha " * 40 + "\n\n# B\n" + "beta " * 40
    chunks = chunking.chunk_markdown(md, "doc.md")
    assert len(chunks) == 2
    assert chunks[0].heading_path == "doc.md > A"
    assert chunks[1].heading_path == "doc.md > B"


def test_heading_path_nests():
    md = "# Top\n" + "x" * 200 + "\n\n## Middle\n" + "y" * 200 + "\n\n### Leaf\n" + "z" * 200
    paths = [c.heading_path for c in chunking.chunk_markdown(md, "doc.md")]
    assert paths == ["doc.md > Top", "doc.md > Top > Middle", "doc.md > Top > Middle > Leaf"]


def test_a_sibling_heading_pops_the_stack():
    md = ("# Top\n" + "x" * 200 + "\n\n## One\n" + "y" * 200 + "\n\n## Two\n" + "z" * 200)
    paths = [c.heading_path for c in chunking.chunk_markdown(md, "doc.md")]
    assert paths[-1] == "doc.md > Top > Two"


def test_hash_inside_a_code_fence_is_not_a_heading():
    """A shell comment in a fenced block must not start a new section, or the
    fence gets split across chunks and both halves become unreadable."""
    md = "# Setup\n" + "x" * 150 + "\n\n```bash\n# install everything\nnpm ci\n```\n"
    chunks = chunking.chunk_markdown(md, "doc.md")
    assert len(chunks) == 1
    assert chunks[0].text.count("```") == 2


def test_long_sections_split_at_paragraph_boundaries():
    para = "word " * 120  # ~600 chars
    md = "# Big\n" + "\n\n".join([para] * 6)
    chunks = chunking.chunk_markdown(md, "doc.md")
    assert len(chunks) > 1
    assert all(len(c.text) <= chunking.MAX_CHARS for c in chunks)
    # every part keeps the heading it came from
    assert {c.heading_path for c in chunks} == {"doc.md > Big"}


def test_tiny_sections_are_merged_not_retrieved_alone():
    md = "# A\ntiny.\n\n# B\n" + "y" * 300
    chunks = chunking.chunk_markdown(md, "doc.md")
    assert len(chunks) == 1
    assert "tiny." in chunks[0].text


def test_embed_text_carries_the_heading_path():
    md = "# Prompt store\n" + "x" * 200
    c = chunking.chunk_markdown(md, "api/README.md")[0]
    assert c.embed_text.startswith("api/README.md > Prompt store")
    assert c.text in c.embed_text


def test_empty_document_yields_nothing():
    assert chunking.chunk_markdown("", "doc.md") == []
    assert chunking.chunk_markdown("\n\n   \n", "doc.md") == []
