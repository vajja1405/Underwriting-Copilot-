"""Founder Studio: the borrower's side of Underwriting Copilot.

A founder picks a metro, a business type and candidate sites. Studio scores the sites (S2), simulates possible
futures (S3) and prepares a lender-ready package. This package holds the shared pieces built in S1:

- studio.profiles: one YAML profile per business type (config/studio/business_types/*.yaml);
- studio.metros: one YAML config per metro (config/studio/metros/*.yaml) and the metro "mode";
- studio.data: public-data connectors, the response cache, provenance records, the SYNTHETIC city and the data
  fabric that S2 and S3 read from.

The labels below are shown on every Studio page and result.
"""

# Every value produced from the generated city carries this label (CLAUDE.md rule 2).
SYNTHETIC_LABEL = "SYNTHETIC"
# Shown on every results page (Studio addendum rule C).
DISCLAIMER = "Educational planning tool. Not financial, legal or lending advice. Lenders make their own decisions."
# Shown next to every probability (Studio addendum rule E).
SIMULATION_LABEL = "simulated, based on stated assumptions"
# The percentile levels of a demand distribution handed from SiteIQ (S2) to the simulator (S3): 0%, 1%, ..., 100%.
PERCENTILE_LEVELS: tuple[float, ...] = tuple(step / 100 for step in range(101))
