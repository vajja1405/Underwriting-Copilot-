# Walkthrough: the innovations (interview questions by innovation)

Written so that both owners (Rahul Vajja and Bhavani Adula) can explain each innovation in their own words. The
plan is in `docs/innovations.md`. Each innovation also has a longer walkthrough with every file and design choice:

| Innovation | Long walkthrough | Status of its numbers |
|---|---|---|
| S1: local-model generation eval and LLM migration gate | `docs/walkthrough_innovations_s1.md` | see that file |
| S2: containment over detection (injection attack success by layer) | `docs/walkthrough_innovations_s2.md` | attack success not measured yet (run R4 has not run) |
| Innovation S5: decision-value evidence requests (this file, below; Phase 4 files in `docs/walkthrough_phase4.md`) | `docs/walkthrough_phase4.md` | measured on SYNTHETIC simulated cases (`results/evidence_metrics.json`, section `simulator`) |

No number in this file comes from memory. Every number is copied from a results file named next to it. Any
attack-success number will only exist in `results/injection_metrics.json` after the full run (R4).

## S2: containment over detection

**In one paragraph.** An underwriting copilot reads text the applicant wrote. The applicant wants approval, so that
text is where a prompt injection would come from. S2 measures how often an injected instruction still reaches the
answer under three prompt layers. L0 is naive pasting, L1 is the current prompt, and L2 is a separate, normalized
applicant block with its own id namespace. The set is 60 frozen payloads: 40 public, 20 hand-written. S2 also proves
with tests that a fully obedient model still cannot change a decision. The measured layers are RAG-answer prompts;
the product's decision-memo prompt (`agent/memo.py`) is not a measured layer, so the R4 numbers do not measure how
robust the memo prompt is.

**Numbers available now (no model involved).** These come from `results/injection/injection_metrics_detector_only.json`,
written by `make injection-detector`. The tripwire (`agent.injection.detect`) flagged:
- 12 of 60 payloads on their raw text (Wilson 95% 0.1183-0.3178);
- 16 of 60 after normalization (0.1713-0.3901);
- 7 of the 40 public payloads either way;
- 2 of the 108 chunk texts stored in the Qdrant index, the texts retrieval puts into prompts (`clean_indexed_chunks`:
  NBC-9.2 and HM-11.2, which quote injection phrases on purpose);
- the same 2 of 96 re-chunked policy texts (`clean_chunks`: 60 whole guideline sections plus 36 regulatory chunks
  made again with a word counter).

### Interview questions and answers

1. **What is the difference between direct and indirect prompt injection?**
   - Direct injection is typed by the person using the chat.
   - Indirect injection arrives inside data the system reads: a document, an email, a retrieved snippet.
   - Here the underwriter is the user and the applicant writes the documents, so the attack is indirect. The
     attacker never talks to the model; they only write text the model will read.

2. **Why is the applicant the attacker?**
   - The applicant controls the notes, the uploaded documents and even the names on the application, and benefits
     from approval.
   - So everything they write is a claim to verify, never policy and never an instruction.
   - In the L2 prompt their text sits in its own `<untrusted_applicant_document id="APP-1">` block, and a rule says
     exactly that.

3. **Why is the detector only a tripwire?**
   - It is a fixed list of patterns.
   - On the 40 public payloads, written by other people, it flagged 7, so most phrasings pass.
   - A flag is useful for routing: it adds a blocking referral to Fraud Operations, and it can never move a file
     toward approval.
   - The protection has to come from somewhere else.
   - The S2 review also found that the product agent ran the tripwire on the raw text only. A zero-width space
     inside "ignore" hid the instruction. The agent now checks the raw text and its normalized form, and a test
     proves the product flags every payload the normalized detector flags.

4. **What does "containment by architecture" mean, and what does "0 by construction" cover?** Even a model that
   obeys every injected instruction can only change words in an answer or a memo:
   - no tool on the allow-list can approve, decline or clear;
   - the LLM may request only the read-only guideline search;
   - the recommendation comes from deterministic code;
   - the memo checker rejects decision wording and a mismatched recommendation;
   - only a human at the checkpoint decides.

   `tests/security/test_containment.py` checks all of this over every payload. "0 by construction" is about the
   model's influence on the decision. It is not a claim that applicant text can never matter. The review found one
   place where it did: a sanctioned name written with an invisible character used to slip past sanctions screening.
   Screening now normalizes the screened name, and a test runs each obfuscation through the real matcher code.

5. **What is ASCII smuggling, and what else hides text?**
   - Unicode tag characters (U+E0000-U+E007F) mirror ASCII but render as nothing, so an instruction can hide
     where no reviewer sees it.
   - The same family includes zero-width characters, the soft hyphen, bidirectional controls, variation
     selectors (used to hide bytes after an emoji) and Hangul fillers.
   - `security/normalize.py` strips the whole Unicode Default_Ignorable_Code_Point set, taken from the Unicode
     14.0.0 data file. It also folds lookalike letters and angle-bracket lookalikes.
   - The tripwire also reads the decoded tag message, so a hidden instruction is checked rather than ignored.

6. **Why report seen and unseen payloads separately, and how unseen are the public ones?**
   - The author wrote the hand-written payloads knowing the defenses, which flatters the results, so they are
     reported apart.
   - The public payloads are unseen for the tripwire: other people wrote them and no pattern was tuned on them.
   - For attack success they are only partly third-party. Each one ends with one of four goal sentences the author
     wrote, and the canary is reached only by following that sentence. Some public rows carry no instruction of
     their own.
   - 11 of the 40 public payloads fall into 5 groups of near-duplicates (computed in the results file), so the
     public intervals are somewhat too narrow.
   - The builder's content filter (hateful, violent, sexual, religious and named-politician topics, plus a few
     jailbreak role-play phrases, malware, drugs and swearwords) dropped 40 of
     the 96 English injection rows before sampling (`payloads.content_filter` in the detector-only results). So the
     public payloads are not a random sample of the dataset's English injection rows.

7. **Why is "100% blocked" not a claim?**
   - The payload set is small and fixed, and the model is one 3B local model.
   - An adaptive attacker would try new phrasings.
   - The scorer counts fixed canary patterns, so the attack success rate is a lower bound for paraphrases.
   - The defensible statements are a per-layer attack success rate with a confidence interval on these payloads,
     and "decision-level impact 0 by construction, enforced by a test".

8. **How does this map to the OWASP Top 10 for LLM Applications 2025?** (Primary source: OWASP Top 10 for LLM
   Applications 2025, https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/, dated November 17,
   2024 on that page; ids from https://genai.owasp.org/llm-top-10/; both accessed 2026-10-13. The site also lists a
   newer "OWASP GenAI LLM Top 10 2026", dated August 3, 2026; this mapping was not re-checked against it.)
   - LLM01:2025 Prompt Injection is the attack itself.
   - LLM06:2025 Excessive Agency is what the tool allow-list and the human checkpoint prevent.
   - LLM05:2025 Improper Output Handling covers the exfiltration canary (a markdown image a renderer would fetch).
   - LLM09:2025 Misinformation covers the fake citation. It is a made-up id (`NBC-12.9`), not the real `NBC-7.1` the
     plan named, so a real section cited for a rule it does not contain (misattribution) is not covered by the
     frozen set, and no canary could catch it.
   - LLM08:2025 Vector and Embedding Weaknesses (a poisoned index) is a Tier 2 extension: S2 never writes to the
     index.
   - Whether any of this applies to a real institution depends on that institution and its risk profile; this is a
     demonstration, not a compliance program.

9. **What is citation laundering?**
   - It means citing the applicant's own document as the source of a rule, for example "the DSCR minimum is
     waived [APP-1]".
   - The answer looks grounded, but the source is the attacker.
   - `check_citations(..., policy_ids_only=True)` reports it separately from attack success.
   - The `APP-` namespace also covers the agent's ids for the application's structured fields, on purpose: those
     are applicant statements too, so none of them can be the source of a policy claim.

10. **If attack success drops from L1 to L2, does that prove the separate applicant block works?** Not on its own.
    There are three things to check:
    - **Refusals.** A "Not found" answer holds no canary, so each layer also reports its not-found rate and
      key-fact recall under attack. Every question has its gold section in the context.
    - **L2's rule 2.** It tells the model not to copy "instructions, codes or links" from the applicant block. The
      author wrote it knowing that two of the four canaries are a code and a link, so part of a drop may come from
      that sentence rather than from the structure.
    - **The paired L1-vs-L2 test.** The results carry a paired L1-vs-L2 McNemar test next to L0-vs-L2, so S2's own
      increment is visible.

    If L0 attack success is already low, that mostly reflects the 3B model's weak instruction-following. How a
    stronger model would behave was not measured: it could follow the injections more, the rules more, or both.

## Innovation S5: decision-value evidence requests

("Innovation S5" here; Founder Studio has its own "Studio S5", the path to approval. The two are different.)

**In one paragraph.** When a file has open problems (a missing statement month, a revenue gap, a TIV mismatch), an
underwriter asks for evidence. A checklist asks for everything. S5 asks only for evidence that could change the
decision: for each request, the engine applies every plausible answer (from documented templates with prior weights),
re-runs the same deterministic reference policy, and scores the request by how likely and how much the
recommendation would change, divided by a cost weight. A request whose every answer keeps the recommendation scores
exactly 0 and is not made. To evaluate this without circularity, a simulator plants problems with a hidden true
answer in SYNTHETIC cases, lets each policy (VOI, checklist, random, an oracle) ask for evidence, reveals the true
answer of each request, and compares the final recommendation with the one the policy gives when everything is
revealed (the full-information recommendation).

**Numbers** (copied from `results/evidence_metrics.json`, section `simulator`; 370 test cases without the held-out
family, documented priors; case-level bootstrap, 1,000 resamples, seed 42):
- **the S5 headline metrics** (innovations.md section 2): requests to a stable decision, VOI minus checklist
  (paired, on the 359 cases both policies end on the full-information recommendation): -0.0529 [-0.0808, -0.0279];
  zero-value share of requests: VOI 0.0 (by construction) against checklist 0.6136 [0.5631, 0.6595];
- requests per case: VOI 0.3838 [0.3243, 0.4405], checklist 1.0703 [0.9703, 1.173];
- cost-weighted requests, VOI minus checklist (paired): -1.2689 [-1.4515, -1.0932];
- zero-value requests per case (VOI 0 and no change after the reveal), VOI minus checklist: -0.6568
  [-0.7486, -0.573]; the VOI policy's own count is 0 by construction (it only asks when VOI is above 0);
- decision error vs the full-information recommendation: VOI 0.027 [0.0135, 0.0432], checklist 0.0135
  [0.0027, 0.0243]; paired difference +0.0135 [0.0, 0.027]. VOI's 10 errors: 4 undetected planted problems (no
  policy can request them) and 6 stops with every open request at VOI 0;
- the oracle (cheapest request sequence that reaches the full-information recommendation, brute force) was
  computed on 368 of 370 cases (2 had 5 open requests, above its limit of 4) and reached the target on all 368;
- **case cap not enforced (a departure from the S5 design).** innovations.md S5 caps each case at 4 open items with
  3 resolutions each. The resolution templates comply (2 or 3 options each), but the cases are not capped: 3 of
  the 370 main test cases carry 5 planted problems and 2 start with 5 open requests
  (`diagnostics.planted_problems`, `test.all.cases.open_requests_at_start`). They are simulated like every other
  case; only the oracle is not computed on them. The results file states this in `design.case_cap`;
- held-out family (claims_history_mismatch, 30 cases, chosen before any simulation): VOI decision error 0.1667
  [0.0667, 0.3333], checklist 0.0; VOI with lookahead 0.0333 [0.0, 0.1].

**Read the diagnostics first** (`diagnostics` in the same section). The generator does not draw each problem's
true answer from the template priors: it takes the answer the cause implies, or the one nearest the hidden value.
On the main test split, missing statement months were "typical" 24 of 24 times (prior 0.6), embedded instructions
were "confirmed" 35 of 35 times (prior 0.7), and a missing document never arrived "inconsistent" (0 of 56, prior
0.1). So the documented priors are not the truth's distribution, and the x0.5 / x1.5 robustness rows scale the
prior of the generator's own latent answer, not of a draw. Two more effects are measured and reported: insurance
problems that change claims also rewrite the stated claim history when revealed (in 5 of the 30 held-out cases one
such reveal alone removes the claims-history finding: PKT-I-026, PKT-I-129, PKT-I-191, PKT-I-226, PKT-I-238), and
the held-out cases carry more co-planted problems (1.7333 per case against 1.1162 on the main split).

### Interview questions and answers

1. **What is the value-of-information score, exactly?**
   - VOI(request) = sum over plausible answers r of p(r) x 1[recommendation(r) differs from now] x size(r) / cost
     weight, with size(r) = 1 + min(|change in the decision metric|, 1.0).
   - The decision metric is the decision DSCR for a loan and the highest 5-year loss ratio (as a fraction) for
     insurance.
   - It is exactly 0 when no plausible answer changes the recommendation. It is a rules-based, scenario-based score,
     not an optimal or economic model.

2. **Why is the old "next-best evidence matches the resolving document" metric circular?**
   - The generator writes which request resolves each problem, and the engine's request rules were written by the
     same author. A 100% match shows that two hand-written tables agree, nothing more.
   - S5 replaces it with outcomes: does asking in this order reach the decision you would make with all the facts,
     and at what cost? The circular number is still reported, labeled CIRCULAR.

3. **Why does VOI ask for so much less than the checklist, and is that just stopping early?**
   - Many open problems cannot change the decision: a debt-payment mismatch never moves the DSCR (the policy already
     uses the higher payment), and a second missing document cannot help while a blocking referral is open.
   - It is partly stopping early, and the cost is measured: VOI's decision error is 0.027 against the checklist's
     0.0135, a paired difference of +0.0135 [0.0, 0.027]. VOI makes fewer requests at a small, measured error cost.

4. **What kinds of mistakes does VOI make?**
   - Undetected problems: a planted problem no open item stands for (an embedded instruction the scanner misses)
     cannot be requested by any policy (4 of VOI's 10 test errors).
   - Myopia: when two problems each keep the recommendation alone (two blocking referrals), each request scores 0
     and VOI stops (6 of 10). The lookahead variant, an addition, scores a request as if the others were resolved
     as most likely; it cuts the held-out error from 0.1667 to 0.0333.

5. **What does the held-out family show, and what does it not?**
   - claims_history_mismatch was chosen and written down before any simulation and never used in development.
   - Its larger VOI error is mostly myopia on cases with more co-planted problems (1.7333 per case vs 1.1162), not a
     family-specific weakness, and in 5 of its 30 cases another problem's reveal also fixes the stated history.
     Both are reported next to the number, so it is not read as more than it is.

6. **How robust is the ranking to wrong priors, and what does a correct prior give?**
   - Scaling the true answer's prior by 0.5 or 1.5 changed nothing in the headline rows: positive priors stay
     positive, so the same requests keep a nonzero VOI. Those two rows are therefore uninformative for the VOI
     policy, and the results say so.
   - Removing the true answer from the option set does change things: the VOI-minus-checklist decision error
     becomes 0.0919 [0.0622, 0.1243].
   - The documented priors are not the truth's distribution to begin with, so three rows replace them with
     measured shares of the true answers (`design.*_priors`; all measured before any case was simulated). The
     paired VOI-minus-checklist differences on the 370 main test cases:

     | Priors | What they are | True answer given no prior (test problems) | Cost-weighted requests | Decision error |
     |---|---|---|---|---|
     | `documented` | the template priors (the headline) | none: every documented prior is above zero (`diagnostics.latent_vs_prior`) | -1.2689 [-1.4515, -1.0932] | +0.0135 [0.0, 0.027] |
     | `empirical_dev` | development-split shares, unsmoothed | 19 of 413 | -1.4054 [-1.6081, -1.2175] | +0.0189 [0.0027, 0.0378] |
     | `empirical_dev_smoothed` | the same counts, add-one smoothing | 0 of 413 | -1.2689 [-1.4515, -1.0932] | +0.0135 [0.0, 0.027] |
     | `oracle_shares` | the test group's own shares (an oracle) | 0 of 413 | -1.3297 [-1.523, -1.1513] | +0.0162 [0.0027, 0.0324] |

   - `empirical_dev` is **not** a correct-prior reference. Some families have tiny development samples (4
     balance-discontinuity and 4 missing-loss-run problems), and an answer never seen there gets no prior. For 19
     of the 413 main test problems that removes the TRUE answer (`diagnostics.latent_without_prior`: 10
     balance_discontinuity / bank_confirms and 9 missing_loss_run / adverse_losses), so for those problems the row
     acts partly like `latent_removed`. Its higher error says that dropping answers is harmful; it says nothing
     about well-matched priors.
   - `empirical_dev_smoothed` gives every answer a prior above zero, so, like x0.5 / x1.5, it cannot change which
     requests have a VOI above zero, and its headline rows equal the documented ones.
   - `oracle_shares` is the genuine correct-prior reference: each group is simulated with its own true-answer
     shares, which no real underwriter has, and no problem loses its true answer. Even so, VOI's decision error
     was 0.0297 [0.0135, 0.0459] against 0.027 [0.0135, 0.0432] with the documented priors (no paired comparison
     between priors variants was computed; the intervals overlap). Its errors split into 4 undetected planted
     problems and 7 stops with every open request at VOI 0 (`decision_errors_by_kind`); the checklist's 5 errors
     are all undetected planted problems, and `voi_lookahead` under the same priors had the checklist's error
     (paired difference 0.0 [0.0, 0.0]). So on this split, priors that
     match the truths at the template level did not remove VOI's extra errors: they are stops at VOI 0, a limit of
     the one-step (myopic) stopping rule rather than of the priors.
   - On the held-out family the decision error difference was the same under all four (0.1667 [0.0667, 0.3333]).

7. **Why is the oracle useful if it cheats?**
   - It knows the hidden truth and searches every order of requests for the cheapest one that reaches the
     full-information recommendation, so it is a lower bound on cost, not a policy anyone could run.
   - It shows how far VOI is from the best possible order on the same cases. A toy test proves the oracle pays cost
     weights, not request counts (a longer but cheaper path wins).

8. **What can and cannot be claimed?**
   - Can: on SYNTHETIC cases with a planted truth, the VOI policy made fewer and cheaper requests than a checklist,
     with the decision-error difference and its interval stated next to it.
   - Cannot: real time or cost savings (cost weights and priors are the author's assumptions), that the score is
     optimal or economic, or anything about extraction accuracy (the simulator uses the perfect extraction).
