# Walkthrough: innovation S1b (frozen generation eval and LLM migration gate)

Written so that both owners (Rahul Vajja and Bhavani Adula) can explain this part of the project. It covers the
files added for S1b, the design choices, and likely interview questions. It contains no measured generation numbers:
those appear only in `results/generation_metrics.json` and `results/llm_migration_*.json` after the full runs. All
guidelines and questions are SYNTHETIC; the regulatory documents are public (SR 26-2, NY DFS Circular Letter 2024-7).

## What problem this solves

A RAG system has two parts that can change: retrieval and generation. If you swap the model and re-run everything,
any change in quality could come from either part. S1b freezes retrieval, so a model or prompt change is measured
on exactly the same documents. Then a "migration gate" decides, with a pre-registered statistical rule, whether a
challenger configuration may replace the champion.

## Files

| File | What it does |
|---|---|
| `eval/rag_key_facts.jsonl` | Frozen test set: for 57 answerable guideline questions, the short facts a correct answer must state (a number, a role such as "Senior Underwriter", or a named term). A role or term fact may list `alternates` (other wordings of the same fact). Written before any model ran; corrected once after review, before R1 (see below). |
| `eval/rag_unanswerable_extra.jsonl` | Frozen test set: 20 new unanswerable questions (10 near-misses about products or lines the guidelines do not cover, 10 plausible but out of scope). With the original 10, 30 unanswerables. |
| `tests/rag/test_key_facts.py` | Proves every key fact appears in its gold section's text (numbers through the SIG number normalizer), that restating the question never satisfies a fact, and that for a question with alternative gold sections each fact holds in every alternative. |
| `config/release_gate.yaml` | The `migration:` margins table, committed before any challenger run. |
| `src/uwcopilot/rag/prompts.py` | Prompt registry: `rag_answer_v1` points at the existing prompt constants; `prompt_sha256(id)` hashes them; `prompt_template_sha256(id)` hashes the full prompt built for a fixed canary question, so a change to the template code is visible too; `request_sha256` hashes one request. |
| `src/uwcopilot/rag/bm25.py` | Pure-Python BM25 over guideline sections; only the offline fallback retriever. |
| `src/uwcopilot/rag/frozen_hits.py` | The frozen-hits file format (one JSON line per question) and its reader. |
| `scripts/freeze_retrieval.py` | Runs hybrid_rerank retrieval once per question and writes `eval/generation/frozen_hits.jsonl` plus a manifest. |
| `src/uwcopilot/rag/generation_eval.py` | Pure scoring functions: key-fact recall, abstention, asserted absence, citations, format, untraceable numbers. |
| `scripts/eval_generation.py` | Runs one named config on the frozen hits through Ollama, writes per-question rows and aggregate metrics; `--reproducibility 20` is run R5. |
| `src/uwcopilot/governance/migration_gate.py` | The non-inferiority rule, input checks (frozen inputs, prompt hashes, request hashes), freeze checks, the R5 validity check and the certificate renderer. |
| `src/uwcopilot/governance/freeze_proof.py` | Freeze proofs: for an input file (or the `migration:` section of the margins file), the first commit holding its current content, with the author date. Recorded when a run starts. |
| `scripts/llm_migration_gate.py` | Compares two measured configs and writes the gate JSON and the Markdown certificate. |
| `tests/rag/test_generation_eval.py`, `tests/governance/test_migration_gate.py`, `tests/governance/test_freeze_proof.py` | Offline tests (FakeLLM, synthetic paired data, a throwaway git repository; no server). |

## How a run flows

1. `make freeze-retrieval` (once): hybrid_rerank top-5 hits for all 133 questions. Before searching, the script checks
   that the Qdrant index holds exactly the chunks of the current source files, so the recorded corpus checksum is
   true. The manifest records the mode, the corpus checksum, the question-file hashes and the hits-file hash.
2. `make eval-generation CONFIG=qwen3b_v1_k3` (R1) and `CONFIG=qwen15b_v1_k3` (R2): each question is answered from its
   first k=3 frozen hits with `rag.answer.answer_from_hits`, under the Ollama queue lock, through the LLM cache.
3. `make eval-generation-repro CONFIG=qwen3b_v1_k3` (R5): 20 seeded questions re-run with the cache off.
4. `make llm-migration-gate`: refuses unless both runs used identical frozen inputs, the same prompt text and
   byte-identical requests; re-scores every stored answer with the current scoring code and refuses if any score
   differs; checks the freeze proofs; then a paired bootstrap per gated metric and the non-inferiority decision;
   writes `results/llm_migration_<A>_vs_<B>.json` and renders `docs/governance/migration_certificates/<A>_vs_<B>.md`
   from that JSON only.

## Design choices worth defending

- **Frozen hits are committed** (`eval/generation/`), not regenerated: anyone can re-run the generation eval
  without the index, and the file hash proves which documents were in the prompt.
- **The freeze default is hybrid_rerank, not "auto"**: replacing the committed hits with fallback hits must be an
  explicit choice; the script refuses to overwrite hits made with a different mode without `--force`.
- **Errors are not scored**: a question refused by the context guard or failed over HTTP gets an error row, and the
  gate refuses to compare runs whose scored questions differ. An answer cut off at `num_predict` is scored on the
  text produced so far and counted separately (`errors.length_truncated`).
- **Every answer keeps its real generation time and latency**: a cached answer reports the time and latency stored
  in its cache entry when it was generated, never the instant cache lookup. So a resumed run still reports the
  latency of every answer, and re-running from the cache cannot make old answers look new.
- **Freeze proofs are about content and answer times**: when a run starts it records, for the key facts, the
  question files, the frozen hits and the `migration:` section of `config/release_gate.yaml`, the first commit that
  holds the current content (author date, which survives amend and rebase). The gate refuses (exit 3) if any of
  them was committed after the run's earliest answer was generated, or if the margins changed since the challenger
  run. Adding a Phase 6 section or a comment to the margins file does not count as a change.
- **Numbers from the question are not "untraceable"**: a correct answer applies the rule to the user's own figures
  ("a DSCR of 1.18 is below 1.25 [NBC-4.2]"). Those numbers cannot be in a guideline but were not invented, so they
  are listed as `numbers_from_question` and not counted. The cost: a model that presents the user's number as a
  policy value is not caught by this metric (key-fact recall still misses the real value).
- **"12 consecutive months" is read as 12 months.** S1b first did this with a small local fold before number
  extraction, because the shared SIG normalizer read it as a count of 12. On 2026-10-13 the normalizer itself learned
  a closed list of duration qualifiers (consecutive, most recent, complete, full, policy, tax, fiscal, trailing)
  between a number and days/months/years, so the local fold was removed and the generation eval uses the shared
  normalizer directly. That change altered SIG's seeded mutation draws, so `eval/provenance/mutations.jsonl` was
  regenerated with `scripts/make_mutations.py` (seed 42) and `results/provenance_metrics.json` re-run.
- **The gate re-scores instead of trusting stored scores**: R1 and R2 run hours apart while other code changes; if
  the scoring code changed in between, the stored scores were computed by different rules. The gate recomputes every
  score from the stored answer and refuses on any difference; re-running the eval from the cache re-scores without
  calling a model.

## Key-fact corrections before R1 (2026-10-13)

A review found facts that misread correct answers. The file was corrected before R1; no measured run had used it
(only a 3-question smoke, whose one keyed question, NB14, was not edited). Changes: NB25 and HM24 dropped a role
that the question itself names ("Compliance", "Catastrophe Management") for "resolved" (with alternates such as
"cleared"), and "in writing" gained the alternate "written"; NB26 dropped "Band E", which is not in its alternative
gold section NBC-7.3; NB09 gained alternates that hold in NBC-3.5; NB23 and NB30 gained common paraphrases. Tests now
guard against facts satisfied by the question and against any_of facts missing from an alternative. The owner review
of 10 random rows is still required before R1.

## SIG checker fixes from the S1b review (2026-10-13)

S1b scores numbers with the SIG checker (`src/uwcopilot/provenance/`), so the review read real answers against
real guideline text and found five places where the checker misread a correct sentence or let a wrong one pass.
Each fix has a regression test built from the guideline sentence (`tests/provenance/`).

| Problem | Example | Rule now |
|---|---|---|
| Qualified durations read as counts | "12 consecutive months" (NBC-3.5), "60 consecutive days" (HM-2.2) | A closed list of qualifier words may sit between a number and days, months or years. |
| A table header's comparator was out of reach | "Minimum decision DSCR" over 1.25 (NBC-4.2) | Source tables are read cell by cell; a cell takes its column header's (else its row label's) comparator when that label has exactly one comparator class and no number. |
| A comparator went to the wrong number | "five-year loss ratio is more than 55.0%" (HM-4.2) | Each comparator phrase belongs to the closest number; "or ..." phrases read backwards on a tie, others forwards. |
| The second of two coordinated values lost its comparator | "limits above $2,000,000 / $4,000,000" (HM-7.1) | A number with no comparator of its own takes the one of the number right before it, when both have the same unit and only "/", "and", "or" or "and/or" joins them. |
| Fraction words before a scale word were dropped | "two and a half million dollars" read as $2,000,000 | Scale words after a fraction multiply it: 2,500,000; "half a million" is 500,000. |

Two things to be able to explain. First, the mutation set (`eval/provenance/mutations.jsonl`) was regenerated once
(seed 42) because the duration rule changed how section numbers are read, which changed the seeded draws; the last
two fixes did not change it (byte-identical). So SIG results from before and after 2026-10-13 are on different
mutation sets and cannot be compared as a checker-only change. Second, one trade-off is open for the owners:
comparator classes are compared strictly, so a correct answer that states a limit from its other side ("a premium
above $150,000 needs the Underwriting Manager" against "Maximum annual premium") is still a mismatch, and at NBC-7.2
the header ("Maximum total exposure", LE) plus the prose ("above $500,000", GT) makes those values unverifiable, so
a flipped comparator on them ("below $500,000") is no longer caught. Counting a limit and its complement as one threshold would change
both; it should be decided on principle, not tuned on the frozen set.

## Interview questions and answers

1. **Why freeze retrieval?** So a model or prompt change is measured on the same documents. Otherwise a
   difference could come from retrieval randomness or an index rebuild, not from the model. The hits file hash ties
   every result to exactly what was in the prompt.
2. **Why key facts instead of an LLM judge?** A judge is another model whose errors we would have to validate. Key
   facts are written once, before any run, and checked deterministically: numbers through the same normalizer the
   numeric-provenance checker uses ("$500k" equals "$500,000"), terms as normalized substrings. A test proves every
   fact is in its gold section. The cost: it is a proxy for faithfulness, not a measure of it, and the same author
   wrote the questions, the guidelines and the facts.
3. **Non-inferiority vs superiority?** A migration (for example to a smaller, cheaper model) does not need to be
   better; it must not be worse by more than an agreed margin. So we ask whether the CI of (challenger - champion)
   stays above -m (or below +m for error rates), not whether it excludes zero.
4. **Why does INCONCLUSIVE block?** If the CI straddles the margin, we cannot rule out a harmful drop. Treating
   "not proven harmful" as "safe" would let regressions through whenever the sample is small.
5. **Why are the CIs wide?** About 30-133 questions per metric. A paired bootstrap helps (both models answer the
   same questions), but small n still gives wide intervals. That is why INCONCLUSIVE is an honest, common outcome.
6. **What is a migration certificate for?** It is the audit record of a model change: which models (digests),
   runtime version, licenses, prompt hashes, frozen-input hashes, git SHA, load averages, n, and the per-metric
   decisions. It is rendered only from the gate's JSON, so it cannot say anything the computation did not produce.
7. **What is the Qwen license point?** The Docker Hub artifacts we imported carry an Apache-2.0 license file, but the
   upstream Hugging Face card for Qwen2.5-3B-Instruct says "Qwen Research License", while the 1.5B card says
   Apache-2.0. We record both sources and do not pick one; a real institution would choose an Apache-2.0 or
   enterprise-hosted model after legal review.
8. **Why Ollama's native API and not its OpenAI-compatible endpoint?** The native `/api/chat` takes `num_ctx`,
   `seed` and `num_predict` per request and returns token counts and durations. The context size matters most: it
   decides how much prompt the model sees, and the OpenAI-compatible endpoint has no per-request way to set it.
9. **Why were the margins committed before the challenger run?** So nobody can pick margins after seeing the
   challenger's answers. Each run records the first commit holding the current `migration:` section; the gate
   compares its author date with the earliest time a challenger answer was generated (from the rows, including
   answers served from the cache), and refuses if the section changed since. Git dates can be set by hand, so this
   is a process control against honest mistakes and casual tuning, not a tamper-proof seal.
10. **Why is the challenger not a clean "size only" comparison?** The only 1.5B artifact available was F16 while the
    3B is Q4_K_M, so size and quantization differ together; the config file says so, and a result cannot be
    attributed to size alone.

## What cannot be claimed

Anything about GPT, Claude, Gemini or Azure quality; production latency; faithfulness in general; regulatory
compliance; or that small-model results transfer to other models. This is a demonstration on SYNTHETIC data, not
a compliance program.
