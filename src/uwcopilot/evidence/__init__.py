"""Evidence-to-Action Engine (Phase 4): case state, evidence ledger, financial calculations, stress tests and
next-best-evidence. The ledger is shared with Founder Studio (one ledger for both sides of the project).

Core modules:
- state.py: CaseState (application + document checks + injection flags + screening), evaluate(), apply(),
  from_packet(), from_application();
- ledger.py (models in ledger_models.py, fact and check claims in ledger_facts.py, assertions in
  ledger_assertions.py, static tables in ledger_tables.py): Claim, Assertion, Ledger, the builder API
  (Ledger.builder()) and build_ledger();
- calculations.py: the agent's formulas re-exported, plus parameterized stress tests and the monthly and yearly
  views the engine needs (shown to reviewers through supplementary.py).
Imports stay explicit (from uwcopilot.evidence.ledger import ...); this package file imports nothing.
"""
