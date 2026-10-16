# Risk scoring service: the client contract and the domain gap (Phase 5, part C)

This note explains how the agent, the MCP server and the tool registry talk to the Phase 1 risk scoring service,
what was wrong with that link, how it is tested now, and why the agent still records **no** model score for the
commercial applications in this repository. All data in the tests is SYNTHETIC. This is a demonstration, not a
production credit or insurance system, and a person makes every decision.

## Two contracts and one translator

There are two request/response contracts, and they are different on purpose.

| | Tool contract | Service contract |
| --- | --- | --- |
| Where | `src/uwcopilot/tools/schemas.py`: `RiskScoreRequest`, `RiskScoreResponse` | `src/uwcopilot/risk/service.py`: `ScoreRequest`, `ScoreResponse` |
| Who uses it | the agent's `score_risk` tool, the MCP `score_application` tool, the tool registry | the FastAPI app (`POST /score`) |
| Request | `product`, `features`, `overrides`, plus a `scenario` label (default `baseline`) | `product`, `features`, `overrides`; any other field is refused (HTTP 422) |
| Response | one assessment: `prediction`, `risk_band`, `reason_codes[{feature, contribution, description}]`, `model_version`, `scenario` (the label), `unknown_features` | baseline `prediction`, `risk_band`, `reason_codes[{feature, value, contribution, reason}]`, plus `prediction_name`, `prediction_unit`, `model`, `model_version`, `risk_band_definition`, `note`, and with overrides a `scenario` block with baseline and scenario side by side |

`src/uwcopilot/risk/client.py` (`RiskApiClient`) is the only code that sends HTTP to the service, and it translates:

- **On the way out** (`service_body`): only `product`, `features` and, when not empty, `overrides`. The `scenario`
  label never leaves the client.
- **On the way back** (`ServiceScore`, then `tool_response`): the body must have exactly the service's fields
  (unknown fields such as `approved` are refused). Without overrides the answer is the baseline; with overrides it is
  the scenario assessment, labelled with the caller's `scenario`. `reason` becomes `description`. Features the caller
  sent as `None` are listed in `unknown_features`, so a score based on incomplete data is never shown as complete.
- **Errors** (every failure is a `RiskApiError` with a fixed code; no score is ever made up):

| What happened | Code | Agent tool status |
| --- | --- | --- |
| `UWC_RISK_API_URL` not set | `risk_api_not_configured` | `unavailable` |
| nothing listening / timed out | `risk_api_unreachable` / `risk_api_timeout` | `unavailable` |
| HTTP 4xx (for example 422 `invalid_features`, `invalid_override`, `leakage_guard`) | `risk_api_rejected_request` | `error` |
| HTTP 5xx (including 503 `model_unavailable` when a model artifact is missing) | `risk_api_server_error` | `error` |
| any other non-200 status, or a 200 body that is not exactly a service response | `risk_api_invalid_response` | `error` |

The client quotes at most 300 characters of the service's error detail (`MAX_DETAIL_CHARS`) and never shows
credentials or query strings in the endpoint.

## The mismatch that was fixed

Until commit `548afa3` the client posted `RiskScoreRequest.model_dump()` as-is and validated the answer as a
`RiskScoreResponse`. Reproduced in Phase 5 against the real app with tiny SYNTHETIC models, using the pre-fix client
from `548afa3^`:

- the request was refused with HTTP 422, `extra_forbidden` at `body.scenario`;
- a correct 200 answer from the service failed `RiskScoreResponse` validation: five extra top-level fields
  (`prediction_name`, `prediction_unit`, `model`, `risk_band_definition`, `note`), an extra `value` and `reason` and
  a missing `description` in every reason code, and a `scenario` that is the service's scenario block (null without
  overrides) instead of a label. That is six errors plus three per reason code, so the count depends on the policy;
  the test pins the kinds of error and that formula.

So no score could ever succeed. The unit tests did not notice because they mocked the transport with the tool
contract on both sides. Commit `548afa3` made the client translate, as described above. Phase 5 adds the tests that
keep the two contracts from drifting apart again.

## How it is tested

`tests/risk/test_client_service_integration.py` wires the real `RiskApiClient` to the real `create_app()` through
`AppTransport` (an httpx transport that hands each request to FastAPI's `TestClient`, so no socket is opened and
nothing touches the network). The models are tiny SYNTHETIC ones trained once per session in
`tests/risk/conftest.py` (builders in `tests/risk/risk_service_helpers.py`); their predictions mean nothing.

- **The mismatch, reproduced:** the tool request sent as-is gets 422 at `body.scenario`; a real 200 body is not a
  `RiskScoreResponse`, with exactly the error kinds listed above.
- **Drift guards (no HTTP):** the client's copies (`ServiceScore`, `ServiceAssessment`, `ServiceScenario`,
  `ServiceReason`) have exactly the service models' fields; the tool request has the service request's fields plus
  `scenario` only; both product lists are the same; the body on the wire validates as a service `ScoreRequest`.
- **Round trips:** insurance frequency and severity baselines, an insurance scenario (label kept), empty overrides,
  credit with a missing value (listed as unknown), and a credit scenario. Each is compared with a direct call to the app.
- **Refusals:** `invalid_features`, `invalid_override` and `leakage_guard` become `risk_api_rejected_request`
  (HTTP 422); a product without a model becomes `risk_api_server_error` (HTTP 503).
- **An answer for another product:** a real 200 body for `insurance_severity`, replayed through an
  `httpx.MockTransport` for an `insurance_frequency` request, is refused as `risk_api_invalid_response` instead of
  being relabelled (`tool_response` copies the product from the request, so only the client's product check stops
  it). Removing that check makes this test fail.
- **The domain gap:** see the next section. It is checked at three levels: the planner's feature names against the
  models' features, the planned request against the service, and a full agent run (the real LangGraph graph, a
  committed credit file and a committed insurance file, `score_risk` wired to the real app, search and screening
  stubbed as unavailable). The full run reaches the human checkpoint with no decision record, the one `score_risk`
  call is recorded as an `error` with no output, and for credit the policy lists NBC-8.1 as not evaluated.

A one-off check (not a committed test) patched the pre-fix `post`/`score` methods back into the client: 16 of the
25 tests the file had then fail (before the product test above was added) (every round trip, every refusal test and both full agent runs). The 9 that still pass are
the reproduction, the drift guards and the feature-name check, none of which goes through the client's send/receive
path.

## The domain gap: why the agent still records no model score

The Phase 1 models were trained on public data about **other kinds of risk**:

- `credit`: 63 features of **consumer** Lending Club loans (for example `annual_inc`, `dti`, `fico_range_low`,
  `revol_util`), and a lifetime probability of default;
- `insurance_frequency` / `insurance_severity`: the nine freMTPL2 **French motor** rating features (`VehPower`,
  `VehAge`, `DrivAge`, `BonusMalus`, `Density`, `Area`, `VehBrand`, `VehGas`, `Region`).

The agent's applications are SYNTHETIC **commercial** files: small-business loans and commercial property and general
liability submissions. Its planner (`agent/tool_plan.py`) sends what those files contain:

- credit: `product`, `industry`, `requested_amount`, `term_months`, `note_rate_pct`, `stated_annual_revenue`,
  `months_in_business`, `min_owner_credit_score`, `new_loans_last_90_days`, `nsf_events_last_6_months`;
- insurance: `class_code`, `coverage`, `total_insured_value`, `years_in_business`, `location_count`,
  `oldest_year_built`, `all_sprinklered`, `worst_protection_class`, `annual_revenue`.

The only shared name is `term_months`, and even that has a different population. The credit model treats it as a
category with the two Lending Club values, 36 and 60. The committed credit applications use 12, 48, 60, 72 and 84 months.

We do **not** map one set onto the other. Renaming `stated_annual_revenue` to `annual_inc`, or inventing a `dti` or a
`BonusMalus` for a business, would feed a consumer or motor model with made-up inputs. The result would be a number
with no validation behind it for this population. The service refuses unknown or missing features (it never fills in
or clips values), and the project's rules forbid invented evidence. Packets (Phase 5 part A, built later) arrive
through `docintel.to_application` as the same application dict, so they meet the same planner and the same gap.

What the agent records, by configuration (tested in `test_client_service_integration.py`):

| Configuration | What happens | Agent record |
| --- | --- | --- |
| `UWC_RISK_API_URL` unset (the default, CI, and the committed evaluation) | nothing is sent | `score_risk` `unavailable`. `results/agent_metrics.json`: `tool_runs.score_risk_unavailable` = 65, `score_risk_ok` = 0 |
| URL set, service running, no credit artifact (a fresh checkout: `models/` is gitignored) | credit gets HTTP 503 `model_unavailable` | `score_risk` `error`, no output; PD not available |
| URL set, models loaded | HTTP 422 `invalid_features`: unknown features (the commercial fields) and missing features (the model's own) | `score_risk` `error`, no output; PD not available |
| Startup credit line | the planner does not call `score_risk` (NSP-1.1) | PD recorded as not applicable, with the reason |

In every case no score reaches the policy and nothing is estimated. For credit files the policy skips NBC-8.1 with
"Model PD not available: <reason>". The insurance rules do not use a model score. The wording differs by
configuration, though: with a service running, the record says the call was refused (an `error` holding the
service's 422 or 503 reason) rather than "not applicable".

**Not done in this part (agent files are owned by the later part A workflow):** have the planner skip `score_risk`
for the commercial lines with an explicit "not applicable: model domain" reason, as it already does for startups.
Then the record would say *not applicable* in every configuration. Closing the gap for real would need a model trained
and validated on commercial small-business loan and commercial property data. No such public dataset is used in this
repository.

**Also not done (a small client hardening, held back because it changes an MCP test owned elsewhere):** when a
request has overrides, the client should refuse an answer that has no `scenario` block, or one whose
`scenario.overrides` differ from what was sent. Today `tool_response` falls back to the baseline in that case and
labels it with the caller's scenario name. The real service always returns the block for non-empty overrides, so
this cannot happen against it, but a different or broken service could make an unstressed number look like a stress
result. `tests/mcp/test_mcp_server_tools.py::test_score_success_passes_the_service_answer_through` mocks exactly such
an answer (overrides sent, `scenario: null`), so its mock must return a scenario block when the check is added.

## Files

| File | What it does |
| --- | --- |
| `src/uwcopilot/risk/client.py` | The shared HTTP client and the translation between the two contracts (unchanged in Phase 5; fixed in `548afa3`). |
| `src/uwcopilot/risk/service.py` | The scoring API. Phase 5 changed only wording: the missing-credit-model message no longer says the model was never trained (`results/risk_metrics.json` now holds a measured credit section; the artifacts are simply not committed). |
| `tests/risk/risk_service_helpers.py` | SYNTHETIC tiny-model builders, JSON-ready feature rows, and `AppTransport`. |
| `tests/risk/conftest.py` | Session fixtures: the models (trained once), their folders, and the app with and without a credit model. |
| `tests/risk/test_service.py` | The API's own rules. It now uses the shared fixtures; its single client round-trip test moved to the integration file. |
| `tests/risk/test_client_service_integration.py` | The client against the real app: reproduction, drift guards, round trips, refusals, domain gap (down to a full agent run). |

## Questions you may be asked

1. **Why not just make the tool contract equal to the service contract?** The tool contract is what the agent, the
   MCP server and the registry share. It is deliberately small (one assessment, a scenario label, the unknown
   features). The service returns more (units, band definition, both sides of a scenario). Keeping them separate and
   translating in one place means only `client.py` changes if the service changes, and the drift-guard tests say
   exactly which field moved.
2. **How did the bug survive the earlier tests?** Those tests mocked the HTTP transport and returned the *tool*
   response, so the client was only ever tested against itself. The fix is a test that runs the real client against
   the real app. That is what `AppTransport` does, without opening a socket.
3. **Why refuse unknown fields in the response (`extra="forbid"`)?** If the service started sending something like
   `approved`, the client should fail loudly instead of passing a new meaning along unnoticed. The drift-guard test
   catches it first.
4. **Why does a 503 count as an `error` and not `unavailable`?** The service answered. Only "not configured",
   "unreachable" and "timeout" mean the service is not there. Either way no PD is produced and the policy says so.
   Changing the mapping would change MCP and agent tests owned elsewhere, so it is noted, not changed.
5. **Why not translate the commercial fields into Lending Club fields?** It would be inventing inputs for a model
   validated on a different population (consumer loans, French motor policies). The number would look precise and
   mean nothing, because no validation stands behind such a mapping.
6. **Is the agent's behavior honest when the service is running?** Yes. It gets a 422 naming the unknown and missing
   features and records the call as an error without output. For credit the policy then reports the PD as not
   available. What could be clearer is the wording ("not applicable" instead of an error); see "Not done in this part".
7. **What would it take to score these applications for real?** A model trained and validated on commercial
   small-business loans (and, for insurance, commercial property losses), with its own data checks, out-of-time test
   and bands. Until then the PD is UNKNOWN, by design.
8. **Do the tests touch the network or real data?** No. They use SYNTHETIC models trained in the test session,
   temporary folders, and an in-process transport.
