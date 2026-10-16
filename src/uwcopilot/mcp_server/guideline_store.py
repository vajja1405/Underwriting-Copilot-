"""The SYNTHETIC guideline documents as MCP resources, and exact lookup of one section by id.

The documents are the markdown files in corpus/guidelines/ (the same files the RAG index is built from). They are
read with the RAG module's own parser, so a section here has exactly the id, title and text that the index cites.
Long sections are NOT split here: get_guideline_section returns the whole section even when the index stores it
in parts (for example HM-9.3).

The SYNTHETIC lending programs in corpus/programs/ (the startup program, sections NSP-1.1 ... NSP-9.2) can be added
with programs_dir: they are not in the RAG index, but startup memos cite them, so the server the CLI starts serves
them too (cli.py). The default store reads corpus/guidelines/ only.

Each document has a short code taken from its section ids ("NBC" for NBC-3.2, "HM" for HM-9.3). Resource URIs:
- guidelines://NBC            the whole markdown document, SYNTHETIC banner included
- guidelines://NBC/NBC-3.2    one section, with a one-line SYNTHETIC notice and its citation on top
"""

from dataclasses import dataclass
from pathlib import Path

from uwcopilot.mcp_server.models import GuidelineSection
from uwcopilot.rag.chunks import Chunk
from uwcopilot.rag.guidelines import GUIDELINES_DIR, GuidelineFormatError, parse_guideline_markdown

RESOURCE_SCHEME = "guidelines"


class UnknownSectionError(LookupError):
    """Raised for a section id that is not in any guideline document."""


@dataclass(frozen=True)
class GuidelineDocument:
    """One guideline file: its short code, ids, the full markdown, and its sections in file order."""

    code: str
    doc_id: str
    doc_title: str
    source_file: str
    markdown: str
    sections: tuple[Chunk, ...]

    @property
    def resource_uri(self) -> str:
        """URI of the whole document, for example guidelines://NBC."""
        return f"{RESOURCE_SCHEME}://{self.code}"


def section_code(section_id: str) -> str:
    """The document code of a section id: "NBC" for "NBC-3.2"."""
    return section_id.split("-", 1)[0]


def section_resource_uri(section_id: str) -> str:
    """URI of one section, for example guidelines://NBC/NBC-3.2."""
    return f"{RESOURCE_SCHEME}://{section_code(section_id)}/{section_id}"


def load_document(path: Path, folder: str = "corpus/guidelines") -> GuidelineDocument:
    """Parse one guideline (or program) file. All its section ids must share one code (the part before the dash)."""
    markdown = path.read_text(encoding="utf-8")
    source_file = f"{folder}/{path.name}"
    sections = parse_guideline_markdown(markdown, source_file)
    codes = {section_code(section.chunk_id) for section in sections}
    if len(codes) != 1:
        raise GuidelineFormatError(f"{source_file}: section ids use more than one prefix: {sorted(codes)}.")
    first = sections[0]
    return GuidelineDocument(
        code=codes.pop(),
        doc_id=first.doc_id,
        doc_title=first.doc_title,
        source_file=source_file,
        markdown=markdown,
        sections=tuple(sections),
    )


class GuidelineStore:
    """All guideline documents (and, when asked, the lending programs), indexed by code and by section id."""

    def __init__(self, guidelines_dir: Path = GUIDELINES_DIR, programs_dir: Path | None = None) -> None:
        """Load every *.md file in guidelines_dir (sorted by name), then the programs in programs_dir if given.

        In programs_dir, README.md describes the folder and is not a program. A document code or a section id may
        appear only once across both folders.
        """
        paths = sorted(guidelines_dir.glob("*.md"))
        if not paths:
            raise FileNotFoundError(f"No guideline markdown files in {guidelines_dir}.")
        self.documents: dict[str, GuidelineDocument] = {}
        self._sections: dict[str, tuple[GuidelineDocument, Chunk]] = {}
        for path in paths:
            self.add_document(load_document(path))
        if programs_dir is not None:
            for path in sorted(programs_dir.glob("*.md")):
                if path.name != "README.md":
                    self.add_document(load_document(path, folder="corpus/programs"))

    def add_document(self, document: GuidelineDocument) -> None:
        """Index one document; refuse a repeated document code or section id."""
        if document.code in self.documents:
            raise GuidelineFormatError(f"{document.source_file}: document code {document.code} is used twice.")
        repeated = [section.chunk_id for section in document.sections if section.chunk_id in self._sections]
        if repeated:
            raise GuidelineFormatError(f"{document.source_file}: section ids already loaded: {repeated}.")
        self.documents[document.code] = document
        for section in document.sections:
            self._sections[section.chunk_id] = (document, section)

    def section_ids(self) -> list[str]:
        """Every section id, document by document, in file order."""
        return [section.chunk_id for document in self.documents.values() for section in document.sections]

    def get_section(self, section_id: str) -> GuidelineSection:
        """The whole section with this id (case-insensitive). Raises UnknownSectionError."""
        key = section_id.strip().upper()
        if key not in self._sections:
            codes = ", ".join(sorted(self.documents))
            raise UnknownSectionError(
                f"No guideline section {key!r}. Section ids look like NBC-3.2 or HM-9.3 (documents: {codes}); "
                "regulatory excerpts have no sections and are returned in full by the search tool."
            )
        document, section = self._sections[key]
        return GuidelineSection(
            section_id=section.chunk_id,
            title=section.title,
            source_doc=document.doc_id,
            doc_title=document.doc_title,
            text=section.text,
            citation=f"[{section.chunk_id}]",
            resource_uri=section_resource_uri(section.chunk_id),
            source_file=document.source_file,
            synthetic=section.synthetic,
        )

    def section_markdown(self, section_id: str) -> str:
        """A section as a small markdown page: SYNTHETIC notice and citation, heading, body."""
        section = self.get_section(section_id)
        notice = (
            f'> SYNTHETIC demo guideline section from "{section.doc_title}" (fictional institution; not lending, '
            f"insurance, legal or compliance advice). Cite as {section.citation}."
        )
        return f"{notice}\n\n## {section.title}\n\n{section.text}\n"
