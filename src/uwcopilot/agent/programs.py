"""Load the SYNTHETIC lending-program documents in corpus/programs/: one citable section per "## <ID> <title>".

Program documents (today only corpus/programs/northwind_startup_program.md, sections NSP-1.1 ... NSP-9.2) use the
same markdown format as the guidelines in corpus/guidelines/, so they are parsed with the same function
(uwcopilot.rag.guidelines.parse_guideline_markdown).

They live in a separate folder on purpose:
- the RAG corpus (corpus/guidelines/), its checksum and the retrieval evaluation stay exactly as committed;
- the Qdrant index is not rebuilt, so a vector search never returns a program section. The agent does not search
  for program sections: the startup policy cites them directly by id, and evidence.guideline_sections() reads
  their text from this loader so memos can cite them and the claim checker can resolve them.
"""

from pathlib import Path

from uwcopilot.common.paths import CORPUS_DIR
from uwcopilot.rag.chunks import Chunk
from uwcopilot.rag.guidelines import parse_guideline_markdown

PROGRAMS_DIR: Path = CORPUS_DIR / "programs"
STARTUP_PROGRAM_DOC_ID = "northwind_startup_program"


def load_program_chunks(programs_dir: Path = PROGRAMS_DIR) -> list[Chunk]:
    """Every section of every *.md program document (sorted by file name; README.md is not a program).

    An absent folder gives [].
    """
    chunks: list[Chunk] = []
    for path in sorted(programs_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        source_file = f"corpus/programs/{path.name}"
        chunks.extend(parse_guideline_markdown(path.read_text(encoding="utf-8"), source_file))
    return chunks
