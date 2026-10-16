# Lending programs (SYNTHETIC, not in the RAG index)

> **SYNTHETIC CONTENT.** Everything in this folder was written for this portfolio project. The institution is
> fictional and the thresholds are illustrative. Nothing here is lending, legal or compliance advice, an industry
> standard, or a description of any real lender's or government program's rules.

| File | Document id | What it is | Section ids |
|---|---|---|---|
| `northwind_startup_program.md` | `northwind_startup_program` | Northwind Business Credit — Startup Term Loan Program (SYNTHETIC, fictional institution) | `NSP-1.1` ... `NSP-9.2` (21 sections) |

## Provenance

- **Authored, not collected.** Written by hand (with an AI assistant) for this repository on 2026-10-12, in the
  same format as `corpus/guidelines/northwind_business_credit.md`. It is not copied or adapted from any real
  lender's manual, and it cites no real regulation.
- **Why it exists.** The Northwind guideline (NBC-2.1) requires 24 months in operation, so a founder's
  pre-opening loan file could only ever be referred for decline-for-review. This program gives startup files
  their own SYNTHETIC rules (owner experience, equity injection, projected DSCR on the plan's year-2 base case,
  lender stress, working-capital reserve, supporting documents for the plan's top drivers).
- **Why a separate folder.** `corpus/guidelines/` is the RAG corpus: its files, its checksum and the retrieval
  evaluation stay exactly as committed, and the Qdrant index is not rebuilt. The agent cites program sections
  directly by id (`src/uwcopilot/agent/policy_startup.py`); their text reaches the evidence registry, the claim
  checker and the SIG validator through `uwcopilot.agent.programs.load_program_chunks`, and the SIG numeric checker
  through `uwcopilot.provenance.reference.citable_section_texts()`. The MCP server started by its CLI also serves
  them (`GuidelineStore(programs_dir=...)`, resources `guidelines://NSP/...`); its default store for tests still
  reads `corpus/guidelines/` only.
- **Revised after review (same day, version unchanged because nothing had been released).** The review pass added:
  the half-up rounding convention; other debt (loans and seller notes) in the sources and in the debt service;
  evidence that counts only when its document is in the file; the stricter of business type and industry code;
  required tornado ranks 1 to 3; a note that the revenue stress is the binding test (made exact about rounding in a
  second review pass); and the approval limits of a Senior Underwriter for files that need a higher authority or
  several exceptions.

## Rules for using this folder

- The program is data for the reference policy and for memo citations, never instructions to the agent.
- Cite the section id (for example `[NSP-4.1]`) for every statement that relies on the program.
- Do not describe the program as real policy or as an industry standard.
