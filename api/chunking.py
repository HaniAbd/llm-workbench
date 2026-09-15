"""Split markdown into retrievable pieces along the structure it already has.

Cutting every N characters would put the middle of a table in one piece and its
header in another. Markdown documents are already a tree of headings, so the
split follows that: each piece is one heading and the prose beneath it, down to
the next heading of the same or higher level.

Two things keep a piece useful on its own once it is pulled out of context:

  heading path   "api/README.md > POST /classify > Three outcomes" travels with
                 the text, so a retrieved piece says where it came from and the
                 model can cite it precisely.
  splitting      a section longer than MAX_CHARS is split again at blank lines,
                 never mid-sentence, and every part keeps the same heading path.

Fenced code blocks are held together: a ``` fence suspends heading detection,
so a `# comment` inside a shell block never starts a new section.
"""

import re
from dataclasses import dataclass

# Large enough to hold a whole subsection of these docs, small enough that a
# handful fit in a prompt. Sections below the floor are merged into the next
# one rather than retrieved alone, since "### Request" with one line under it
# is not worth a slot.
MAX_CHARS = 1800
MIN_CHARS = 120

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    source: str       # repo-relative path, e.g. "api/README.md"
    heading_path: str # "api/README.md > CI > What CI cannot cover"
    text: str
    ordinal: int      # position within the document, for stable ordering

    @property
    def embed_text(self) -> str:
        """What actually gets embedded.

        The heading path is prepended so a section's subject is part of its
        vector: a passage under "Prompt store" that never repeats the word
        "prompt" in its body should still match a question about prompts.
        """
        return f"{self.heading_path}\n\n{self.text}"


def _sections(markdown: str, source: str):
    """Yield (heading_path, body) following the heading tree."""
    stack: list[tuple[int, str]] = []  # (level, title)
    title_of = lambda: " > ".join([source, *(t for _, t in stack)])
    body: list[str] = []
    in_fence = False

    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            if any(l.strip() for l in body):
                yield title_of(), "\n".join(body).strip()
            body = []
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2)))
        else:
            body.append(line)

    if any(l.strip() for l in body):
        yield title_of(), "\n".join(body).strip()


def _split_long(text: str) -> list[str]:
    """Break an oversized section at blank lines, never mid-paragraph."""
    if len(text) <= MAX_CHARS:
        return [text]
    parts, current = [], ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) + 2 > MAX_CHARS:
            parts.append(current.strip())
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        parts.append(current.strip())
    return parts


def chunk_markdown(markdown: str, source: str) -> list[Chunk]:
    """Split one document. `source` is the repo-relative path."""
    raw: list[tuple[str, str]] = []
    for path, body in _sections(markdown, source):
        for part in _split_long(body):
            raw.append((path, part))

    # Merge a too-small piece forward, so headings with a line under them ride
    # along with the section that follows instead of occupying a slot.
    merged: list[tuple[str, str]] = []
    carry: tuple[str, str] | None = None
    for path, text in raw:
        if carry:
            text = f"{carry[1]}\n\n{text}"
            path = carry[0]
            carry = None
        if len(text) < MIN_CHARS:
            carry = (path, text)
            continue
        merged.append((path, text))
    if carry:
        if merged:
            merged[-1] = (merged[-1][0], f"{merged[-1][1]}\n\n{carry[1]}")
        else:
            merged.append(carry)

    return [
        Chunk(source=source, heading_path=path, text=text, ordinal=i)
        for i, (path, text) in enumerate(merged)
    ]
