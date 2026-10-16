"""SYNTHETIC document packet generator (Phase 2): loan packets and insurance submissions with ground truth.

Everything this package makes is synthetic: fictional institutions, made-up people and businesses, and documents
that print "SYNTHETIC - for demonstration only" on every page. The extraction pipeline must never import this
package or the ground truth (docintel/truth.py). The only code allowed to import them is:
- scripts/make_packets.py (writes the packets) and scripts/eval_*.py (score against the truth);
- the Phase 4 evidence-request simulator (innovation S5), which is evaluation code: scripts/simulate_requests.py
  builds its JSON-only cases with this generator, and src/uwcopilot/evidence/simulator.py, simulator_cases.py and
  simulator_diagnostics.py read the hidden truth of those cases (no engine module imports them);
- tests.
tests/docintel/test_synthetic_imports.py holds that allow-list and fails on any other importer in src/ or scripts/.
"""

from uwcopilot.docintel.synthetic.generate import generate_packets
from uwcopilot.docintel.synthetic.packet import build_packet

__all__ = ["build_packet", "generate_packets"]
