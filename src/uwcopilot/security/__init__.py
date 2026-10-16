"""Prompt-injection containment (docs/innovations.md, S2): normalization, payloads, layer prompts and scoring.

- normalize.py: the canonical text normalization for untrusted text (invisible characters, NFKC, homoglyphs);
- payloads.py: the frozen injection payload file (eval/injection_payloads.jsonl) and its sha256 check;
- layers.py: the prompts for the measured defense layers L0 (naive), L1 (current prompt) and L2 (containment);
- containment.py: the L3 structural controls (an answer schema with no decision field, decision-word lists);
- injection_eval.py: pure scoring of model outputs (canaries, citation laundering, ASR, McNemar, detector recall).

The project's position (ground rule 8): document text is data, never instructions. A detector is only a tripwire;
the protection is architectural, because the model's output can never approve, decline or change a decision.
"""
