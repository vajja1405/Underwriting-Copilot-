"""Map a packet extraction to the agent's application JSON (uwcopilot.agent.applications), with provenance.

to_application(extraction, checks=None) -> (application_dict, provenance, unknowns)
- application_dict: a CreditApplication / InsuranceApplication as JSON. A value the extraction does not hold is
  None (it is never guessed), and its path is listed in unknowns; such a dict will not validate until a person or
  a later step fills it.
- provenance: application path -> list of {doc_id, page, line} the value came from (a computed value such as a
  month's deposits cites the documents it was computed from; documents_provided cites each document's first page).
- unknowns: the application paths that could not be filled, in the order they were met.

The field-by-field rules are in to_application_credit.py and to_application_insurance.py. They are the rules the
SYNTHETIC packet generator documents for application_as_documented, so mapping the generator's perfect (oracle)
extraction gives exactly the as-documented application (tests/docintel check this on JSON-only packets).
checks (optional): the packet's check results; for a credit packet documents_provided is then read from the
required_documents check instead of being recomputed.
"""

from uwcopilot.docintel.check_models import CheckResult
from uwcopilot.docintel.schemas import LoanPacket, PacketExtraction
from uwcopilot.docintel.to_application_common import Provenance
from uwcopilot.docintel.to_application_credit import credit_application
from uwcopilot.docintel.to_application_insurance import insurance_application


def to_application(
    extraction: PacketExtraction, checks: list[CheckResult] | None = None
) -> tuple[dict, Provenance, list[str]]:
    """The agent application dict, its provenance and the unknown paths (see the module docstring)."""
    if isinstance(extraction, LoanPacket):
        return credit_application(extraction, checks)
    return insurance_application(extraction)
