# AWS Bedrock provider (Converse API): off by default, never run

This page explains the `bedrock` provider in `src/uwcopilot/llm/bedrock_client.py`: what it sends and returns, how
to turn it on, what the AWS account needs, what was tested, and what has not been done.

**Status, stated plainly:** the client is built and tested against a **stubbed** `bedrock-runtime` client only.
**No request has ever been sent to AWS Bedrock from this repository.** When it was written (2026-10-23) the machine had
no AWS credentials (no `AWS_*` variables, no `~/.aws` folder), so no live call was attempted. No Bedrock latency,
cost or answer quality has been measured. Under CLAUDE.md rule 6, nothing here may be described as having run
against Bedrock until a live run is done and recorded.

## 1. What it does

One `complete()` call is one Bedrock Converse request through boto3 (`bedrock-runtime` client, `converse`). The shapes
below come from the `bedrock-runtime` service model shipped with botocore 1.43.118 (API version 2023-09-30), and the
tests check every request against that model with botocore's `Stubber`.

| Shared interface (`LLMClient.complete`) | Converse request |
|---|---|
| `system` (a string) | `system=[{"text": ...}]`; an empty system prompt is not sent |
| `messages` (`LLMMessage` role + content) | `messages=[{"role": ..., "content": [{"text": ...}]}]`, order kept |
| `max_tokens` | `inferenceConfig.maxTokens` |
| `temperature` | `inferenceConfig.temperature`, **only** when `UWC_BEDROCK_SEND_TEMPERATURE=1` (0 to 1); some models reject it, as current Claude models do on the Anthropic API |
| model | `modelId` (a model id or an inference profile id, from `UWC_BEDROCK_MODEL`) |

| Converse answer | `LLMResponse` |
|---|---|
| text blocks of `output.message.content` | `text` (joined in order; reasoning blocks are dropped, like thinking blocks in the Anthropic client) |
| `usage.inputTokens`, `usage.outputTokens` | `input_tokens`, `output_tokens` (an answer without them is refused: its cost could not be counted) |
| `stopReason` | `stop_reason`, mapped as in the next table; the original is kept in `metadata["bedrock_stop_reason"]` |
| `metrics.latencyMs`, `ResponseMetadata.RequestId` | `metadata["bedrock_latency_ms"]`, `metadata["request_id"]` |
| (not returned by Converse) | `model` is the id that was requested |

Stop reasons. The budget and cache layers treat an answer as unfinished when its `stop_reason` is one of
`base.INCOMPLETE_STOP_REASONS`, so Bedrock's reasons are mapped onto those names. This matters for the cache: a stored
guardrail stop must raise again on a cache hit (there is a test for exactly that).

| Bedrock `stopReason` | Shared `stop_reason` | Result |
|---|---|---|
| `end_turn`, `stop_sequence`, `tool_use` | unchanged | answer returned |
| `max_tokens`, `model_context_window_exceeded` | `max_tokens` | `IncompleteLLMResponse` (cut off) |
| `content_filtered`, `guardrail_intervened` | `content_filter` | `IncompleteLLMResponse` (withheld) |
| `malformed_model_output`, `malformed_tool_use`, any unknown reason | `refusal` | `IncompleteLLMResponse` (no usable answer; unknown reasons fail closed) |

Structured output. `BedrockOptions(output_schema=<JSON schema>)` sends one tool whose `inputSchema` is the schema and
`toolChoice` forcing the model to call it. The tool call's input is returned as JSON text in `response.text`, so a
caller parses it the same way as Ollama's `format` schema output. The schema's SHA-256 is part of the cache key.
Converse also has an `outputConfig.textFormat` JSON-schema option in this botocore version; it is not used here.

Errors and retries. Like the Anthropic and OpenAI clients (`max_retries=2`), botocore runs in `standard` retry mode
with 3 attempts in total, backing off on throttling and server errors. When they are used up:

| Situation | Error |
|---|---|
| `ThrottlingException` after the retries | `BedrockThrottled` |
| `AccessDeniedException` (IAM permission or model access missing) | `BedrockAccessDenied` |
| any other service error (`ValidationException`, `ResourceNotFoundException`, ...) | `BedrockError`, naming the error code |
| credentials missing or unusable at call time | `BedrockCredentialsMissing` |
| connection or other SDK failure | `BedrockError` |

Credentials never appear in errors, logs or the cache.

## 2. Why it is off, and the gates before any request

The provider is not in `llm.settings.PROVIDERS`, so `UWC_LLM_PROVIDER` cannot select it. A caller must ask for it by
name: `get_llm_client(provider="bedrock")`. Then, in this order, with no request sent if any gate fails:

1. `UWC_BEDROCK_ENABLED` must be `1`, `true` or `yes` (`BedrockDisabled`). Bedrock is billed per token.
2. `UWC_BEDROCK_REGION` must be set (`BedrockRegionMissing`). `AWS_REGION` and `AWS_DEFAULT_REGION` are never used,
   because the region decides where prompts are processed.
3. A model id: `UWC_BEDROCK_MODEL` or the `model` argument (`BedrockModelMissing`). There is no default model.
4. boto3 must be installed (`BedrockSDKMissing`, which names `uv sync --extra bedrock`). It is imported only here, so
   the rest of the LLM layer works without it.
5. Implicit SDK variables must be unset (`ImplicitSDKConfigError`): `AWS_ENDPOINT_URL` and
   `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` would send prompts to another endpoint, and `AWS_BEARER_TOKEN_BEDROCK` makes
   botocore switch to that Bedrock API key instead of the chosen credentials. Endpoint URLs in the shared AWS config
   file are ignored (`ignore_configured_endpoint_urls=True`).
6. AWS credentials must resolve (`BedrockCredentialsMissing`), from the profile in `UWC_BEDROCK_PROFILE`, or from
   boto3's default credential chain when no profile is named. Resolving them may contact the credential sources the
   chain uses (an SSO cache, instance metadata); it never contacts Bedrock.
7. The model id needs a verified price in `config/llm_pricing.yaml`. The factory wraps the client as
   `Cached(Budgeted(client))`, so without a verified price the budget layer raises `PriceNotVerified` before the call.
   **There is no Bedrock price in the price list today**, so every Bedrock call is refused before any request.

## 3. How to turn it on

1. Install the optional extra: `uv sync --extra bedrock`.
2. In the AWS account (check the current AWS documentation; these steps have changed over time):
   - pick a region where the model is offered;
   - make sure the account may use the model in that region (Bedrock console, model access or model catalog; some
     model providers ask for a one-time use-case form);
   - give the IAM identity permission for `bedrock:InvokeModel` on that model (Converse uses the `InvokeModel`
     permission). If the model is called through a cross-region inference profile, the permission must also cover
     the profile and the regions it routes to.
3. Set up credentials outside the repo, for example `aws configure sso` and `aws sso login --profile uw-dev`. Never put
   AWS keys in the repo or in `.env`.
4. Add the model's price to `config/llm_pricing.yaml`, keyed by the exact id you will use, copied from
   https://aws.amazon.com/bedrock/pricing/ for your region, with `source` and `checked_on` (the file has a template).
5. Set the variables (repo `.env` or environment; a non-empty `.env` value wins):

| Variable | Meaning | Default |
|---|---|---|
| `UWC_BEDROCK_ENABLED` | `1`, `true` or `yes` to opt in | off |
| `UWC_BEDROCK_REGION` | AWS region, for example `us-east-1` (validated) | none (required) |
| `UWC_BEDROCK_MODEL` | Bedrock model id or inference profile id | none (required) |
| `UWC_BEDROCK_PROFILE` | named AWS profile | unset: boto3's default credential chain |
| `UWC_BEDROCK_SEND_TEMPERATURE` | `1` to send `temperature` | off |

6. Call it by name:

```python
from uwcopilot.llm.base import LLMMessage
from uwcopilot.llm.factory import get_llm_client

client = get_llm_client("bedrock")  # Cached(Budgeted(BedrockClient)); raises before any request if a gate fails
answer = client.complete("Answer in one sentence.", [LLMMessage(role="user", content="...")], max_tokens=400)
```

`MAX_RUN_COST_USD` still caps the whole run, and the on-disk cache answers repeated requests for free.

## 4. What is tested

`tests/llm/test_bedrock_client.py`, 48 tests, all passing on 2026-10-23 (`pytest tests/llm` 139 passed, 2 deselected;
`ruff check src tests` clean). No test contacts AWS. Every test that builds a boto3 client runs with every `AWS_*`
variable removed, the AWS config files pointed at an empty temporary folder, instance metadata disabled and the fake
credentials `testing`; the other tests use a small fake object or call the pure functions.

- Settings: off by default; only an explicit flag enables it; the `.env` file wins; bad region names are refused;
  `AWS_REGION` is never used; `UWC_LLM_PROVIDER` cannot select `bedrock`; no Bedrock price is marked verified.
- Factory gates: disabled, no region, no model, no credentials, unknown profile, each implicit SDK variable, and a
  missing boto3 each raise before any request (a `Stubber` with nothing queued would fail the test otherwise).
- The built boto3 client: endpoint `https://bedrock-runtime.<region>.amazonaws.com`, retries
  `{"mode": "standard", "total_max_attempts": 3}`, read timeout 120 s.
- Budget and cache: an unpriced model is refused with `PriceNotVerified` before any request; with a TEST price (not
  a real one) the first call reaches the stub once and the second is a cache hit; a cached guardrail stop raises
  again on the hit.
- Request mapping, checked by `Stubber` against the service model: system, multi-turn messages, `maxTokens`,
  `temperature` only with the flag, temperature outside 0 to 1 refused.
- Answer mapping: text blocks joined, reasoning dropped, token counts, request id, latency; every stop reason in the
  table above, including an unknown one.
- Errors: throttling, access denied, validation and not-found errors name the code; throttling is retried twice
  (3 attempts, counted with a botocore `before-send` hook) and then reported; missing credentials and connection
  failures become this module's errors; an answer without usage or without a message is not used.
- Structured output: the forced tool is sent and its input comes back as JSON; an answer without the tool call is
  refused.
- Cache key: the same settings give the same key; region, temperature flag and schema each change it; the profile
  does not.

## 5. Not done

- No live call to Bedrock, so no measured latency, cost, token counts or answer quality from any Bedrock model.
- No Bedrock price in `config/llm_pricing.yaml`, so the provider refuses every call until someone verifies one.
- No streaming (`ConverseStream`), no Bedrock Guardrails configuration, no prompt caching (`cachePoint`), no
  `outputConfig.textFormat` structured output, no Bedrock API keys (bearer tokens are refused on purpose).
- No caller in the repo uses the provider yet (no script or eval selects `bedrock`). The `UWC_BEDROCK_*` variables
  are listed, commented out, in `.env.example`.
- CI installs no optional extras (`uv sync --locked`), so `tests/llm/test_bedrock_client.py` is skipped there
  (`pytest.importorskip("botocore")`); the 48 tests run only where the `bedrock` extra is installed.

## 6. Likely interview questions

1. **How did you add Bedrock without changing any caller?**
   The LLM layer has one small interface (`complete(system, messages, max_tokens, temperature)`) and one response type.
   `BedrockClient` maps that interface to Converse, and the factory wraps it in the same cost cap and cache as the
   other providers, so callers only change the provider name.

2. **Why Converse and not `InvokeModel`?**
   `InvokeModel` takes each model family's own JSON body. Converse is one request and answer shape across model
   families, with token usage and a stop reason in a fixed place, which is what a provider-neutral adapter needs.

3. **Why map Bedrock's stop reasons instead of passing them through?**
   The budget and cache layers decide "unfinished" from a fixed set of names. A raw `guardrail_intervened` would be
   cached and later served as a normal answer. Mapping it to `content_filter` makes a cache hit raise again, and the
   original reason stays in the metadata.

4. **Why refuse `AWS_ENDPOINT_URL` and `AWS_BEARER_TOKEN_BEDROCK`?**
   botocore applies them on its own. The first can send prompts to a different endpoint and the second switches the
   credentials. The project rule is that only explicitly chosen settings decide where data goes.

5. **How do you know it works if it never ran?**
   I do not claim it does against the real service. The tests show the requests match the botocore service model and
   that every answer, error and gate is handled. A live run, with a verified price and real credentials, is the
   missing step.
