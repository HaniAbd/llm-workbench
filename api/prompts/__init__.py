"""Prompts live in files here, identified by a hash of the text that was sent.

Why a hash and not a version number: the identity is *derived* from the text,
so editing a prompt cannot silently keep its old id. There is no convention to
remember and no way to get it wrong. A result recorded today and one recorded
after an edit carry different ids by construction, so a difference between them
can be attributed to the prompt rather than guessed at.

Adding a prompt means adding a `.md` file next to this one and calling
`get("that_name")`. There is no registry to update.

The digest covers the *rendered* text, not the template. A prompt whose
variables change — a new category in the classification enum, say — therefore
gets a new identity too, because the model genuinely saw something different.
The id always describes what was actually sent.
"""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from string import Template

_DIR = Path(__file__).parent

# Twelve hex characters of SHA-256. Long enough that a collision is not a
# practical concern, short enough to read in a log line.
DIGEST_LENGTH = 12


@dataclass(frozen=True)
class Prompt:
    """A rendered prompt and the identity of the exact text it contains."""

    name: str
    text: str
    digest: str

    @property
    def id(self) -> str:
        """e.g. `classify_ticket@a3f9c1d2e8b4` — what callers record."""
        return f"{self.name}@{self.digest}"


class PromptNotFound(FileNotFoundError):
    """No `.md` file for the requested name."""


def get(name: str, **variables: object) -> Prompt:
    """Load `<name>.md`, render it, and identify it by its content.

    Read from disk on every call rather than cached at import. Editing a prompt
    then takes effect on the next request, which is the point of the exercise:
    uvicorn's `--reload` watches `.py` only, so a cached prompt would keep
    serving the old text after an edit with no indication that it had.
    A file read is microseconds against an LLM call of seconds.

    Placeholders are `$name` (`string.Template`), not `{name}`, because these
    prompts discuss JSON and brace syntax would collide with it. A literal `$`
    in a prompt must be written `$$`.
    """
    path = _DIR / f"{name}.md"
    try:
        template = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PromptNotFound(f"no prompt file at {path}") from exc

    text = Template(template).substitute(**variables) if variables else template

    # Normalised before hashing so that an editor adding or removing a trailing
    # newline does not read as a changed prompt.
    text = text.strip()

    return Prompt(
        name=name,
        text=text,
        digest=sha256(text.encode("utf-8")).hexdigest()[:DIGEST_LENGTH],
    )


def available() -> list[str]:
    """Every prompt name on disk."""
    return sorted(p.stem for p in _DIR.glob("*.md"))
