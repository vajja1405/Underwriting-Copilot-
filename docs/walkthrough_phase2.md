# Phase 2 walkthrough: document intelligence

Written on 2026-10-14 so that both Rahul Vajja and Bhavani Adula can explain this phase in their own words.

Phase 2 reads a packet of business documents (one PDF) and turns it into checked, cited facts. A credit packet holds
a loan application, up to 12 monthly bank statements, a P&L, a balance sheet, a debt schedule, photo IDs, a credit
report, a formation document, a tax return summary and sometimes a collateral valuation. An insurance packet holds
an application, loss runs from several carriers, a property schedule, a broker's cover email, an ownership statement
and sometimes inspection reports. Some pages are clean PDFs with a text layer; others look scanned (an image only).

The pipeline finds the documents in the packet, reads every value with a citation (document, page, line, box,
confidence), runs plain-Python checks on those values, turns disagreements into contradictions with a candidate
explanation, flags instruction-like text, and maps the result into the application JSON that the underwriting
agent already reads. Nothing in it approves, declines or decides. Every packet is **SYNTHETIC**: fictional
institutions, made-up people and businesses, and the footer "SYNTHETIC - for demonstration only" on every page.

## Honest status

- **Measured** on the 60 SYNTHETIC test packets (30 credit, 30 insurance) with the **rules extractor** only, and
  Tesseract 5.3.4 for pages without a text layer. No language model was called: 0 LLM calls, $0.00
  (`rules.cost` in the results file).
- **Provenance caveat.** `results/docintel_metrics.json` was written on 2026-10-14T00:58:15+00:00 by
  `scripts/eval_docintel.py` at commit `271ca4d` with `git_dirty: true`: the working tree had uncommitted changes.
  Commit `5cb6b97` committed these results together with the last review fixes, and its message asks for
  `make eval-docintel` to be re-run on the clean tree. **That clean-tree re-run is still pending.** Until it is done,
  a commit hash alone does not reproduce the numbers below; treat them as the current, not yet final, measurement.
- **Not measured:** rules extractor vs the hybrid (LLM) extractor. The results file says: "not measured (no
  --provider run has been done: this run used the rules extractor only; `scripts/eval_docintel.py --provider ollama`
  would measure it)". The LLM extractor exists and is tested with a fake LLM only.
- **Not used:** Docling, named in the original build plan (`docs/build_prompts.md`, Prompt 2), did not run in this
  repo. The pipeline uses pdfplumber for the text layer and Tesseract for scanned pages.
- **Review history** (from the `5cb6b97` commit message): the code landed in WIP checkpoints `191e996` ... `271ca4d`.
  A review with three lenses (truth, fidelity, evaluation) followed. Of its findings, 25 were fixed with tests,
  1 was deferred and documented (see Limitations), and 1 is the clean-tree re-run above.

## How one packet is processed

```
packet.pdf  (SYNTHETIC; some pages are image-only "scans")
   |
 1 text_layer      pdfplumber reads each page's words and boxes; a page with fewer than 20 characters
   |               has no text layer
   |
 2 ocr             only pages without a text layer: render at 300 DPI, 3x3 median filter, deskew, run Tesseract
   |               twice (--psm 6 and --psm 4) and keep the reading with the higher mean word confidence
   |
 3 classify        keyword scores per document type -> page type; "Page i of n" footers -> documents D01, D02, ...
   |
 4 extract         rules extractor: label/value lines and tables -> LoanPacket or InsuranceSubmission
   |               every value is Cited: document, page, line, box, confidence (1.0 text layer, Tesseract's on scans)
   |
 5 injection_scan  every line, and every pair of adjacent lines, through the agent's FIXED injection patterns
   |
 6 checks          deterministic checks, no LLM (11 per line, 17 check ids in all): required documents,
   |               name / FEIN / owners, statement months, balance continuity, revenue vs deposits,
   |               debt payments, TIV, loss-run lines and totals, ...
   |
 7 contradictions  each issue of a failing check -> a contradiction with evidence on both sides and a CANDIDATE
                   explanation (benign / unsupported / unknown), then an OCR review; networkx graph -> plain JSON

 then  to_application  -> the agent's CreditApplication / InsuranceApplication JSON, with provenance per field
                          and a list of "unknowns" (fields that could not be read are None, never guessed)
```

`pipeline.run_packet()` runs stages 1 to 7 and times each one. `extractor="hybrid"` adds an LLM reading of the
key-value documents after stage 4 (not measured, see above).

## Every file, what it does and why

### The SYNTHETIC packet generator (`src/uwcopilot/docintel/synthetic/`)

The extraction pipeline never imports it: `tests/docintel/test_docintel_import_isolation.py` checks every docintel
module outside `synthetic/`, `truth.py` and `evaluation.py` (below). The scripts that make and evaluate packets
(`scripts/make_packets.py`, `scripts/eval_docintel.py`) and tests use it. No test limits which code outside
`src/uwcopilot/docintel/` may import it.

| File(s) | What it does | Why it is built this way |
|---|---|---|
| `__init__.py`, `packet.py`, `generate.py` | `build_packet()` makes one packet PDF and its ground truth; `generate_packets()` makes a seeded set, a seeded train/test split within each line, and `manifest.json` with every PDF and truth hash plus `test_truth_sha256` (the version of the evaluation set). | Each packet's seed comes from the master seed and its id, so a packet does not change when the set grows. The manifest lets anyone check a regeneration byte for byte. |
| `common.py` | Seeded names, ids, dates and money formats. Invented institution names; FEINs with the never-issued `00-` prefix; account numbers starting `99`; `example.com` / `example.org` e-mail addresses. | No real institution or person. Two card-processor names used in v1 turned out to be real companies and were replaced in v2. |
| `credit_build.py`, `credit_case.py`, `credit_finance.py`, `credit_transactions.py`, `credit_generate.py`, `credit_application.py`, `credit_packet.py`, `credit_records.py` | The loan case: draws (business, request, owners, debts), 12 months of transactions with their TRUE category, financials worked backwards from a target DSCR, the documents in page order, the "as documented" application, and the problem records. | The generator knows the truth before anything is printed, so every printed value and every injected problem has a known answer. |
| `insurance_build.py`, `insurance_case.py`, `insurance_generate.py`, `insurance_application.py`, `insurance_packet.py`, `insurance_records.py` | The same for an insurance submission: locations and TIV, 3-5 fictional prior carriers, premiums, claims, owners, the cover email. | Same reason. Five fictional carriers with five loss-run layouts make the table parsers work for real. |
| `problems.py`, `problems_credit.py`, `problems_insurance.py`, `injections.py` | Choose each packet's problems first, build the case so they can happen, apply them, and record each one: type, true cause, benign or unsupported, the evidence request that would resolve it. `injections.py` holds the planted instructions. | The planted instructions were written after the detector's patterns were fixed and were not used to tune them. About half use wording the patterns do not know ("novel_" techniques), so recall is not trivially 100%. |
| `render_base.py`, `render_helpers.py`, `render_credit.py`, `render_statement.py`, `render_insurance.py`, `render_loss_run.py`, `documents.py` | reportlab renderers. `PageWriter` records every printed line and the box of every printed value, so the generator also writes a perfect ("oracle") extraction with citations. | The oracle uses the same schema as the pipeline, so the evaluation compares like with like. |
| `scan.py` | The "scanned look": rasterize at 150 DPI, rotate 0.3-1.8 degrees, blur, noise and speckles, JPEG, back into an image-only PDF page. | Seeded per document, so the same seed gives byte-identical scans. |
| `docintel/truth.py` (next to the package) | Ground truth models: page text lines, field truth rows, injected problems, the planted instructions, `application_as_documented` and `application_latent`. | Read only by evaluation code and tests. |

### Reading pages

| File | What it does | Why |
|---|---|---|
| `layout.py` | `Word`, `Line`, `PageText`: words and lines with boxes in PDF points (top-left origin) and a confidence. | One page model for the text layer and OCR, so the extractor does not care where text came from. |
| `pdftext.py` | Reads the text layer with pdfplumber and groups words into numbered lines. | A page with fewer than 20 characters is treated as having no text layer and goes to OCR. |
| `ocr.py` | Renders with pypdfium2, median filter, deskew by projection profile, runs Tesseract with two page-segmentation modes in parallel, keeps the more confident run, drops noise words. | Each step fixes an observed failure: specks read as digits (median filter), tilted table rows split or merged (deskew). Raises `OcrUnavailable` with an explanation when Tesseract is missing. |
| `normalize.py` | `canonical_name()` and `canonical_fein()` for comparisons, plus a re-export of the canonical text normalization of `security/normalize.py` (S2 handover, 2026-10-14): `normalize_text()` removes the whole Unicode Default_Ignorable set (zero-width, bidirectional controls, variation selectors, tag characters), applies NFKC, folds homoglyphs and angle-bracket lookalikes. | Attackers can hide instructions with characters a reader never sees. One implementation serves the document pipeline and the injection controls; the same module makes name comparisons fair. |
| `parsing.py` | Parsers for money, dates, percentages, integers, yes/no, FEINs, codes. An OCR digit fix only inside numeric tokens (`O` -> `0`, `l` -> `1`). | Parsers never guess: an ambiguous value (`S1,234.56` could be `$1,234.56` or `51,234.56`) returns None. |
| `classify.py` | Page type from weighted keyword phrases (fuzzy match only on OCR pages); documents from the "Page i of n" footer, else from type changes, statement periods and loss-run carriers. | Simple, explainable rules. A continuation page takes its document's type, which corrects a misclassified page. |

### Extraction

| File | What it does | Why |
|---|---|---|
| `schemas.py` | `LoanPacket` and `InsuranceSubmission` built from `Cited[T]` values (`extra="forbid"`). `flatten_fields()` turns any extraction into (field, key, value, citation) rows and defines the row keys (a transaction is `date\|D or W\|amount`, a claim is its claim number, ...). | One schema for the rules extractor, the LLM extractor and the oracle. Row keys let the evaluation match table rows even when an extractor misses or adds one. |
| `extract_rules.py` | Sends each document to the reader for its type and validates the assembled extraction against the schema once more. | The entry point of the rules extractor. |
| `rules_common.py`, `rules_tables.py` | Shared helpers: label/value lines (exact on text layer, fuzzy prefix on OCR), citations, owner tables, two-slot premium rows. | Rows are parsed from word types (dates, amounts, Yes/No) more than fixed positions, so small OCR shifts do not break them. |
| `rules_credit.py`, `rules_statements.py` | Readers for every credit document; bank statements with their transaction tables. | A deposit or withdrawal is decided by the running balance first (exact to the cent), then by column position. A lost minus sign is restored only when the arithmetic proves it. |
| `rules_insurance.py`, `rules_schedule.py`, `rules_loss_runs.py` | Readers for the insurance documents, the property schedule, and each carrier's loss run. | Each carrier has its own layout, so claim amounts are matched to paid / reserve / incurred by the header order, or by arithmetic when no header was read. |
| `extract_llm.py`, `extract_llm_answers.py`, `extract_llm_fields.py`, `extract_llm_specs.py` | The hybrid extractor: an LLM reads the key-value documents; rules keep the long tables. Document text goes in a delimited, escaped `<document>` block; answers must be JSON `{"value", "cite"}`; a value not printed on its cited line is rejected. | The extension point exists and is tested with a fake LLM. Its quality is **not measured**. The budget's `BudgetExceeded` error is never caught, so a run that would exceed the cost cap stops (ground rule 4). |

### Checks and contradictions (no LLM anywhere)

| File | What it does | Why |
|---|---|---|
| `checks.py`, `check_models.py` | `run_checks()` runs every check of the packet's line in a fixed order. `CheckResult`, `Evidence` (document, page, line, field, value), `CheckIssue`, severities and guideline references. | A check that lacks the values it needs returns `not_evaluated` instead of guessing. Every value it used is cited. |
| `checks_identity.py` | Business name, FEIN and owner comparisons for both lines (majority group vs odd documents). | Messages say which documents disagree, never the names (names are untrusted text; they stay in the evidence). |
| `checks_credit.py`, `checks_statements.py`, `checks_revenue.py`, `deposits.py` | Credit: required documents (NBC-3.1), identity, tax return vs P&L, statement months, balance continuity, account conduct, revenue vs deposits, debt payments. `deposits.py` classifies each transaction by documented keyword rules. | Formulas come from the agent (`agent/credit_calculations.py`) and thresholds from the SYNTHETIC Northwind guidelines, so the checks and the agent cannot disagree. |
| `checks_insurance.py`, `checks_property.py`, `checks_loss_runs.py`, `checks_loss_run_coverage.py` | Insurance: required documents (HM-3.1), identity, TIV (HM-5.2 bands), claim lines (paid + reserve = incurred), loss-run totals, claims history, coverage, valuation age. | Same idea with `agent/insurance_calculations.py` and the SYNTHETIC Harborline guidelines. |
| `injection_scan.py` | Scans page lines (and adjacent pairs) and free-text fields with the agent's fixed patterns after `normalize_text()`. | A tripwire: it only records where instruction-like text is, so the file goes to a human. It never follows the text. |
| `contradictions.py` | Turns every issue of a failing check into a `Contradiction` (type, sides, supporting evidence, explanation) and builds a networkx graph (entity, document, page and value nodes; `conflicts_with` edges), exported as plain JSON. | The graph shows which values on which pages disagree, for a reviewer or a UI. |
| `contradiction_rules.py` | Deterministic explanation rules per contradiction type: benign (with a code such as `non_revenue_deposits_counted`, `transcription_error`, `rule_resolved`), unsupported (`no_supporting_document`, ...) or unknown. | Every explanation is a CANDIDATE for a human reviewer, never a decision. "Unknown" is a valid answer. |
| `contradiction_ocr.py` | Downgrades an explanation to `unknown / possible_ocr_misread` (and one severity level) when an OCR misread could explain it: a cited OCR line below confidence 0.80, a loss run read by OCR said to miss a claim, an OCR owners table that does not add up, an owner name that differs only by an extra OCR word. | A disagreement built on a misread is not evidence of tampering. The 0.80 cut-off was chosen on the dev split only. |

### Pipeline, mapping to the agent, evaluation

| File | What it does | Why |
|---|---|---|
| `pipeline.py` | `run_packet(pdf, packet_id, line, extractor="rules", ocr="auto")`: stages 1-7 with timings. `ocr="force"` sends every page to OCR (used to measure the OCR effect on clean pages). | One function a script, a test or a later web route can call. |
| `to_application.py`, `to_application_common.py`, `to_application_credit.py`, `to_application_insurance.py` | Extraction -> the agent's application JSON, with provenance (which document, page and line each value came from) and `unknowns`. | Nothing is filled in silently. Where the agent's model has no room for None, the default is written AND listed in `unknowns`. |
| `levers.py` | Applies resolution substitutions to an application dict (set a field, append to a list, rebalance verified revenue, scale the schedule TIV). | Shared with the Phase 4 evidence engine, so it must not import the generator. |
| `evaluation.py`, `evaluation_values.py`, `evaluation_citations.py`, `evaluation_problems.py` | Per packet, counts: fields (per type, per clean / scanned page, per document type), table rows, citations, page types, documents, checklist, check statuses, contradictions matched to injected problems, injection hits. | Counts, not metrics, per packet; so the bootstrap can resample packets. |
| `evaluation_stats.py`, `evaluation_intervals.py`, `evaluation_summary.py` | Micro-averaged metrics, packet-level percentile bootstrap (1,000 resamples, seed 42), Clopper-Pearson exact intervals, paired differences, and the report sections with a "note" where a number is easy to misread. | A 0% or 100% metric has a zero-width bootstrap interval; the exact interval shows the real uncertainty. |
| `scripts/make_packets.py` | `make packets`: 150 credit + 150 insurance SYNTHETIC packets, seed 20261013, 30 + 30 in the test split, into `data/processed/packets/`; copies the manifest to `eval/packets/manifest.json` (committed). | One command reproduces the evaluation set. `--json-only` makes truth files without PDFs for the Phase 4 simulator. |
| `scripts/eval_docintel.py` | `make eval-docintel`: checks every test packet against the manifest hashes, runs the rules pipeline, then the forced-OCR run, and writes `results/docintel_metrics.json` and `results/docintel_per_packet.jsonl` with a provenance block. `--split dev` is for error analysis; `--provider` adds the hybrid run. | Nothing runs on a packet set whose hashes do not match. Error analysis belongs on the dev split; the test split is for the final measurement. |
| `scripts/run_docintel.py` | Runs the pipeline on one packet and prints documents, checks, contradictions and injection hits (as short excerpts). | A demo and a debugging aid. |
| `tests/docintel/` (32 files) | Unit tests per module; determinism (`test_same_seed_gives_identical_pdf_and_truth`); every field value printed on its page; import isolation (`test_docintel_import_isolation.py`: statically and in a fresh process, the pipeline never loads `synthetic` or `truth`); a 3-packet end-to-end smoke test where a FakeLLM answers from the oracle (plumbing only, not quality); the review fixes. | CI installs Tesseract, so the OCR tests run for real on SYNTHETIC scanned pages; no test needs a network or an API key. |
| `Makefile` | `packets`, `eval-docintel`. | Ground rule 3: every step has a target. |

## Design choices worth defending

1. **Rules first, LLM optional.** The rules extractor is deterministic, free and explainable line by line. The LLM
   path is an extension point with the same schema and the same citation rules. Whether it helps is an empirical
   question, and it has not been measured yet.
2. **Every value carries a citation.** A reviewer can open the page and see the line. The evaluation checks
   citations too: does the cited page print the extracted value, and is the cited line the true line?
3. **One schema, `extra="forbid"`.** The rules extractor, the LLM extractor and the oracle all produce the same
   models, so anything outside the schema is rejected and the evaluation compares like with like.
4. **Checks in plain Python, with the agent's own formulas.** A check is arithmetic and a threshold from the
   guidelines. It is reproducible, cheap, and testable with hand-made cases. When values are missing it says
   `not_evaluated`.
5. **"Unknown" is allowed, and OCR doubt is visible.** An explanation is a candidate. When OCR may explain a
   disagreement, the explanation becomes `possible_ocr_misread` and the severity drops one level. The failing check
   itself stays failed: the documents disagree as read, and a person confirms against the page image.
6. **Repair only what arithmetic proves.** A lost minus sign is restored only when the balances prove it; a row
   amount goes to deposits or withdrawals by the running balance before the column position; otherwise the value is
   left out. A wrong number is worse than a missing one, because a missing one is listed as unknown.
7. **The pipeline cannot see the answers.** It never imports the generator or the truth. A test checks the import
   graph statically and in a fresh Python process.
8. **Document text is data.** The injection scan reuses the agent's fixed patterns; no pattern was added or tuned
   on the packets. A hit routes the file to a human (Fraud Operations / the SIU in the guidelines); it never changes
   what the pipeline does.
9. **Test-split hygiene.** The v1 packets (seed 42) and their test split were inspected during the Phase 2
   review. So v2 was generated with a new seed (20261013), error analysis for v2 used the dev split only, and the
   test split was run once.
10. **Intervals that do not overclaim.** Packet-level bootstrap keeps values of one packet together. Next to it,
    every ratio has a Clopper-Pearson interval, a flag when the bootstrap interval has zero width, and a note when
    the count is below 10.

## What was measured

All numbers are copied from `results/docintel_metrics.json` (test split, 60 SYNTHETIC packets: 30 credit,
30 insurance). Square brackets are 95% packet-level percentile bootstrap intervals (1,000 resamples, seed 42);
"exact" is the Clopper-Pearson 95% interval. Averages are micro-averages: counts are summed over packets. Remember
the provenance caveat above (dirty tree at `271ca4d`, clean-tree re-run pending).

The test split has 949 pages (790 with a text layer, 159 scanned), and 57 of the 60 packets have at least one
scanned page.

### Fields

| What | Value [95% bootstrap] | Counts |
|---|---|---|
| Field F1, all values | 0.9936 [0.9889, 0.9971] | 36,093 correct, 195 false positives, 268 misses |
| Field precision / recall | 0.9946 [0.9906, 0.9977] / 0.9926 [0.9874, 0.9965] | |
| Field F1 on text-layer pages | 1.0 (no error; exact precision interval [0.9999, 1.0]) | 33,571 correct |
| Field F1 on scanned pages | 0.9159 [0.8736, 0.9566] | 2,522 correct, 195 false positives, 268 misses |
| Field F1, credit / insurance packets | 0.9985 [0.9973, 0.9994] / 0.9681 [0.9457, 0.9884] | |
| Lowest field types | integer 0.9136 [0.8558, 0.9726]; code 0.9765 [0.9586, 0.9916] | |
| Table rows: transactions / claims / locations (F1) | 0.9992 [0.9983, 0.9998] / 0.9793 [0.943, 1.0] / 0.8382 [0.7167, 0.9535] | |

A wrong value counts as one false positive AND one miss. The results file warns that bank-statement transaction rows
are most of the values, so the overall F1 is mostly a transaction-table metric; per document type and per field
type are in the file. Every error sits on a scanned page.

### Citations, pages, documents, checklist

| What | Value |
|---|---|
| Extracted values whose cited page prints them (correct or not) | 0.9984 [0.9975, 0.9992], 36,230 of 36,288 |
| Correct values cited on their true page | 1.0, 36,093 of 36,093 (a sanity check: the rules extractor cites the page it read) |
| Correct values cited on their true line | 1.0, 35,953 of 35,953 (140 values not scored: too few points to align their scanned page) |
| Page type | 949 of 949 (exact lower bound 0.9961); scanned pages 159 of 159 (exact lower bound 0.9771) |
| Documents found with the exact pages and type | 870 of 870 (exact lower bound 0.9958) |
| Checklist codes right | 0.9965 [0.991, 1.0], 568 of 570; whole packet right: 58 of 60 |

### Contradictions, explanations, injections

| What | Value [95% bootstrap] | Exact interval |
|---|---|---|
| Contradiction recall (injected problems found) | 0.9412 [0.8723, 1.0], 48 of 51 | [0.8376, 0.9877] |
| Contradiction precision (contradictions that match a problem) | 0.75 [0.6027, 0.8868], 51 of 68 | [0.6302, 0.8471] |
| Recall / precision counting only evidence-correct matches | 0.902 [0.8135, 0.9773] (46 of 51) / 0.6912 [0.5479, 0.8182] (47 of 68) | |
| Benign vs unsupported accuracy ("unknown" counts as wrong) | 0.9 [0.7872, 0.9768], 36 of 40 | [0.7634, 0.9721] |
| Same, without the 1 designed abstention | 0.9231 [0.8181, 1.0], 36 of 39 | |
| Injection recall (planted instructions found on their page) | 0.5, 3 of 6 (bootstrap [0.0, 1.0]; "n < 10: interval not informative") | [0.1181, 0.8819] |
| Packets with a false injection hit | 0 of 60 | [0.0, 0.0596] |

What these say, read from the same file:

- The 3 missed problems are all planted instructions. Every other problem type was found every time (recall 1.0
  per type, each on a small count).
- All 17 contradictions that matched no problem came from the 57 packets with scanned pages (precision 46 of 63
  there; 5 of 5 in the 3 packets without scanned pages). Owner mismatches (7 of 12) and balance discontinuities
  (4 of 8) had the most unmatched ones.
- Explanations never confused benign with unsupported. All 4 errors were "unknown": 3 `possible_ocr_misread`
  downgrades and 1 `insufficient_evidence` (an unexplained TIV difference inside the 15% band, unknown by design).
- Injections by technique: `approval_claim` 2 of 2, `ignore_instructions` 1 of 1, `novel_output_steering` 0 of 2,
  `novel_software_note` 0 of 1. The fixed patterns catch the wording they know and miss new wording.

### OCR effect and speed

| What | Value |
|---|---|
| Same clean pages read from the text layer vs forced through OCR (F1) | 1.0 vs 0.9878 [0.9808, 0.9923]; paired difference 0.0122 [0.0077, 0.0192] |
| Clean minus scanned pages (F1 difference) | 0.0841 [0.0434, 0.1264], composition-confounded (scanned pages hold a different mix of document types) |
| Time per packet, p50 / p95 | 8,610.17 ms / 17,995.29 ms (p50 interval [5,481.35, 9,383.09]) |
| OCR time per page, p50 over the 57 packets with OCR (each packet's OCR time / its OCR pages) | 2,822.22 ms [2,769.59, 2,872.62]; 159 pages were OCR'd |
| LLM calls, tokens, cost | 0, 0, $0.00 |

The paired comparison is the clean one: it reads the same pages both ways. Timings are wall-clock on a shared
4-CPU machine, with one worker process in the rules run; the load averages before and after the run are in the
file, which says the timings "are not a stable product figure".

## What is not measured

- **Rules vs hybrid (LLM) extraction.** Not measured. `scripts/eval_docintel.py --provider ollama` would measure it.
- **`tax_return_vs_pnl` detection.** The check never ran on these packets (30 of 30 credit packets
  `not_evaluated`): it needs a calendar-year P&L, and the generator prints a trailing-twelve-month P&L ending
  mid-year. Only hand-built tests exercise it.
- **NBC-6.2 name and date of birth on the photo ID vs the credit report.** Not implemented: the schemas carry no
  date of birth (`coverage_notes.not_implemented`).
- **Any real-world accuracy.** These are simulated documents. The numbers say how the pipeline handles this
  generator, not how it would handle real bank statements or loss runs.
- **Docling:** not run (see Honest status).

## Limitations

- **SYNTHETIC documents, my own generator.** The layouts, the scan noise and the problems are designed here. Real
  documents have far more layouts, handwriting, stamps, low-quality phone photos and multi-column pages.
- **Clean pages are easy by construction.** The text layer prints exactly what the generator wrote, so F1 1.0 on
  clean pages is expected. The scanned-page numbers are the informative ones.
- **Small counts for some metrics.** 6 planted instructions, 1 FEIN mismatch, 1 paid/reserve mismatch. Intervals
  on those are wide or "not informative".
- **The injection tripwire is a keyword detector.** It found 0 of 3 planted instructions written in new wording.
  This is why a hit routes to a human and why nothing downstream follows document text: detection is a signal,
  not the protection.
- **Seed history.** The seed-42 (v1) test split was inspected during the review, so it can no longer serve as a
  clean test. v2 (seed 20261013) is the evaluation set; its test split was run once, after tuning on dev.
- **Provenance from a dirty tree** (see Honest status). The clean-tree re-run is pending.
- **Deferred review finding.** The latent ("real world") outcome of each problem is the one its cause implies, not
  a draw from the resolution templates' priors (the note at the top of `synthetic/credit_records.py`). That matters
  for Phase 4, which uses the latents; it is documented there and not changed in Phase 2.
- **Latency** comes from one shared machine with other jobs running. Treat it as an order of magnitude.

## How to run

```bash
make packets         # 300 SYNTHETIC packets -> data/processed/packets/, eval/packets/manifest.json
make eval-docintel   # test split, rules run + forced-OCR run -> results/docintel_metrics.json (slow: OCRs every page)
uv run python scripts/run_docintel.py --packet PKT-C-001     # one packet, printed
uv run python scripts/eval_docintel.py --split dev --out <outside results/> --per-packet-out <outside results/>
uv run pytest tests/docintel -q                               # needs Tesseract (CI installs it)
```

## Likely interview questions

**1. What goes wrong with OCR here, and what did you do about each failure?**
The code handles four kinds of OCR failure on the scanned pages. Specks are read as stray characters or digits:
a 3x3 median filter removes single-pixel specks. A tilted scan splits or merges table rows: the page is deskewed
before OCR. Digits
are read as letters (`O` for `0`): fixed only inside numeric tokens. A thin minus sign is lost: restored only when
the running balances prove it. What is left still shows: on the same clean pages, forced OCR gives F1 0.9878
against 1.0 from the text layer, and every field error in the test run sits on a scanned page.

**2. Why does every value carry a citation?**
An underwriter has to trust a number before acting on it. With a document, page, line and box, they can check it
in seconds. Citations also make errors measurable: 36,230 of 36,288 extracted values (0.9984) are printed on the
page they cite; a value that OCR misread fails this test, because no line prints it. For an LLM extractor,
citations matter even more, because a
model can cite a page it never read; the hybrid extractor rejects a value that is not printed on its cited line.

**3. How did you design the schema?**
Every value is a `Cited[T]`: the value plus document, page, line, box, confidence and source text. The models
forbid extra fields. The extractors and the generator's oracle use the same models, and `flatten_fields()` defines
each field path and row key once (a transaction is keyed by date, direction and amount). That one decision makes
evaluation simple: flatten both sides and compare rows.

**4. How do you evaluate extraction?**
Match predicted rows to truth rows on (field, key), compare normalized values, and count. A wrong value is one
false positive and one miss. Micro-average over all values, and also report per field type, per document type and
clean vs scanned, because transactions dominate the total. Bootstrap by packet, since values in one packet are not
independent. Add exact intervals, because a 100% metric has a zero-width bootstrap interval that overstates
certainty.

**5. Why deterministic checks instead of asking the LLM?**
A check like "opening + deposits - withdrawals = closing within $1.00" or "paid + reserve = incurred" is
arithmetic. Python does it exactly, the same way every time, for free, and a test can prove it. An LLM can get
arithmetic wrong and cannot be audited line by line. The checks reuse the agent's own formulas, so the documents
and the agent cannot disagree on a definition.

**6. What is the difference between a benign and an unsupported discrepancy?**
Benign means the documents explain it: stated revenue is higher than verified deposits because transfers or loan
proceeds were counted, or a name differs by a typo on one document. Unsupported means no document backs the
higher figure. The rules give a candidate explanation from the numbers, or "unknown". On the test split they
were right 36 of 40 times, never confused the two classes, and answered "unknown" 4 times. "Unknown" is
deliberate: when OCR could explain the gap, a human should look.

**7. How do you handle prompt injection inside documents?**
Three layers. Document text is never treated as an instruction: the rules extractor only copies values into typed
fields, and the LLM prompt puts the document in an escaped data block. A tripwire flags instruction-like text after
normalizing hidden characters and homoglyphs, and a hit routes the file to a human. And nothing in the pipeline or
the agent can approve or decline. The tripwire caught 3 of 6 planted instructions, with 0 false hits in 60
packets. It missed all 3 written in new wording. That is why it is a signal, not the defense.

**8. How do you control cost?**
This run cost $0.00: the rules extractor makes no LLM calls. For the hybrid path, every LLM client is wrapped in a
response cache keyed by a hash of the whole request, so a re-run costs nothing, and a budget that checks a
worst-case cost before each call. A run that would exceed the cap stops (`BudgetExceeded` is never caught). Long
tables (bank statements, claim rows) stay with the rules, which keeps each prompt small.

**9. Why did you regenerate the packets with a new seed?**
During the review we looked at the seed-42 test split. Once you have looked at test data, design changes can
quietly fit it. So v2 was generated with seed 20261013, error analysis used only the dev split, and the test split
was run once. The manifest's `test_truth_sha256` names that exact evaluation set.

**10. Your field F1 is 0.9936. Is the extractor that good?**
On these SYNTHETIC packets, yes, but read it carefully. The text layer is perfect by construction, so clean pages
score 1.0. On scanned pages F1 is 0.9159 [0.8736, 0.9566]. Most values are transaction rows, which dominate the
average. And the results come from a dirty working tree; the clean-tree re-run is pending. The honest claim is
"high accuracy on our own synthetic documents, weaker on scans", not "production-ready extraction".
