# AgentPay Guard — Backend

FastAPI service for AgentPay Guard. All commands below are run from the `backend/` directory.

## Setup

```bash
python -m venv .venv

# Windows (PowerShell / cmd)
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

Production dependencies only:

```bash
pip install -r requirements.txt
```

Development dependencies (includes production dependencies plus `pytest` and `httpx`):

```bash
pip install -r requirements-dev.txt
```

## Configuration

Copy `.env.example` to `.env` and fill in values locally. Never commit `.env` or real credentials.
`.env` is git-ignored; `.env.example` must only ever contain blank placeholders.

| Variable | Required | Default | Used by |
|---|---|---|---|
| `FRONTEND_URL` | no | `http://localhost:5173` | CORS |
| `GROQ_API_KEY` | for `/api/v1/intent/parse` | – | Groq LLM (intent extraction) |
| `GROQ_MODEL` | no | `openai/gpt-oss-120b` | Groq model ID |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | for payments | – | PayPal Sandbox REST app |
| `PAYPAL_BASE_URL`, `PAYPAL_TIMEOUT_SECONDS` | no | Sandbox URL, `15` | PayPal (Sandbox only) |
| `REVIEWER_TOKEN` | for reviewer actions | – | Reviewer bearer token (32+ chars) |
| `ELASTIC_URL`, `ELASTIC_API_KEY` | for durable audit | – | Elasticsearch audit index |
| `ELASTIC_INDEX`, `ELASTIC_TIMEOUT_SECONDS` | no | `agentpay-audit-events`, `5` | Elasticsearch audit index |

Without `GROQ_API_KEY`, `/api/v1/intent/parse` returns `503 INTENT_SERVICE_UNAVAILABLE`;
every other endpoint works normally. The key is never logged or returned by the API.

## Run the API

```bash
uvicorn app.main:app --reload --port 8000
```

- Health check: http://localhost:8000/health
- Interactive docs: http://localhost:8000/docs

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/api/v1/intent/parse` | Extract payment details from a natural-language request (LLM) |
| POST | `/api/v1/risk/assess` | Deterministic, rule-based risk assessment (no LLM) |
| GET | `/api/v1/audit/events` | Search the audit trail (Elastic, or in-memory fallback) |

### `POST /api/v1/intent/parse`

Request:

```json
{ "request_text": "Pay 250 USD to ABC Technologies for software services." }
```

Response (`200`):

```json
{
  "merchant_name": "ABC Technologies",
  "amount": "250",
  "currency": "USD",
  "description": "software services",
  "purpose": null,
  "missing_fields": ["purpose"],
  "clarification_needed": true
}
```

How it works and what it guarantees:

- The Groq model (`GROQ_MODEL`) extracts only details that are stated in the request. It is
  instructed never to invent or default an amount, merchant, currency or purpose.
- The request text is treated as **untrusted data**: it is sent fenced inside the user message,
  and instructions inside it are ignored.
- **Model output is never trusted.** The backend requires strict JSON of exactly the expected
  shape (any extra field such as `decision` or `risk_score` makes the whole output invalid),
  re-validates every value with the same constraints as `TransactionDetails`, and checks that the
  merchant name, amount and currency actually appear in the request text. Values that fail are
  dropped and reported in `missing_fields`.
- `missing_fields` lists exactly the `null` details; `clarification_needed` is `true` whenever
  anything is missing or ambiguous. Ask the user to fill these in before risk assessment.
- This endpoint **only extracts**. It does not call the risk engine, produce risk scores, decide
  ALLOW/REVIEW/BLOCK, or approve, store or execute any payment. Submit the completed details to
  `POST /api/v1/risk/assess`, which remains the only source of risk decisions.

**Currencies are never converted.** The currency is returned exactly as the request states it,
e.g. `INR` for "₹5000", "5000 INR" or "5000 Indian rupees". Ambiguous forms such as `$`,
"dollars" or "rupees" alone are not mapped to a currency, and no default currency is assumed.
The extracted currency must then pass the normal currency rules in `/api/v1/risk/assess`
(see [Currencies and amount thresholds](#currencies-and-amount-thresholds)).

Errors use the standard `ErrorResponse` body (`{"code", "message"}`) with fixed, client-safe
messages; raw provider errors are never returned:

| Status | `code` | Cause |
|---|---|---|
| 422 | – | Invalid request body (empty, over 1000 characters, unknown fields) |
| 429 | `INTENT_RATE_LIMITED` | Provider rate limit reached |
| 502 | `INTENT_EXTRACTION_FAILED` | Model output malformed, truncated or rejected |
| 503 | `INTENT_SERVICE_UNAVAILABLE` | Key not configured, provider unreachable or failing |
| 503 | `INTENT_MODEL_UNAVAILABLE` | Configured model not available |
| 504 | `INTENT_TIMEOUT` | Provider did not respond within 20 seconds |

## Payments workflow (PayPal Sandbox, Orders v2)

Risk-gated payments with PayPal **Sandbox** only: authorize first, capture only after an explicit
human reviewer action. Set `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and a `REVIEWER_TOKEN`
(at least 32 characters) in `backend/.env`.

> **Storage is in-memory.** Payment records (including PayPal IDs and idempotency request IDs)
> are lost when the backend restarts and are not shared between workers. Run one worker.

| Method & path (`/api/v1/payments`) | Who | Effect |
|---|---|---|
| `POST /` | client | Risk assessment by the backend. BLOCK -> `BLOCKED`, REVIEW -> `PENDING_REVIEW` (no PayPal call); ALLOW -> AUTHORIZE-intent PayPal order -> `AWAITING_BUYER_APPROVAL`. INR -> 422 before any PayPal call. |
| `GET /?limit=&offset=` | reviewer | List, newest first (`limit` 1-100, default 20). |
| `GET /{transaction_id}` | reviewer | One payment with status, risk details, PayPal references, `next_actions`. |
| `POST /{id}/order` | client | Retry PayPal order creation for `APPROVED_FOR_PAYMENT` (same request ID). |
| `POST /{id}/authorize` | client | Reads the order from PayPal; authorizes only if PayPal reports `APPROVED`. No body; buyer approval is never taken from the client. |
| `POST /{id}/approve` | reviewer | `PENDING_REVIEW` -> creates the PayPal order. |
| `POST /{id}/reject` | reviewer | `PENDING_REVIEW` -> `REJECTED`. |
| `POST /{id}/capture` | reviewer | `AUTHORIZED` -> capture: `COMPLETED` -> `CAPTURED`, `PENDING` -> `CAPTURE_PENDING`. |
| `POST /{id}/void` | reviewer | `AUTHORIZED` -> `VOIDED` (never after capture). |

**Responses of the unauthenticated actions are minimal.** `POST /`, `POST /{id}/order` and
`POST /{id}/authorize` return only:

```json
{
  "transaction_id": "txn_...", "status": "AWAITING_BUYER_APPROVAL",
  "merchant_name": "Acme Cloud", "amount": "49.99", "currency": "USD",
  "approval_url": "https://www.sandbox.paypal.com/checkoutnow?token=...",
  "next_actions": ["buyer_approves_on_paypal", "authorize"],
  "last_error": null
}
```

`approval_url` is set only while the buyer must approve; `next_actions` lists only buyer actions
(`create_order`, `buyer_approves_on_paypal`, `authorize`); `last_error` is `{code, retry_safe}`.
They never contain reviewer names/comments/actions, risk score/level/reasons, description/purpose,
PayPal order/authorization/capture IDs or statuses, or error messages. The full record is
available only to reviewers (`GET /{id}` and the reviewer action endpoints).

Reviewer endpoints, including the two payment **reads** (records contain reviewer names and
comments), require `Authorization: Bearer <REVIEWER_TOKEN>` (never in bodies or URLs). Creating a
payment, retrying order creation and authorizing remain available without the token.
If `REVIEWER_TOKEN` is missing or weak they return 503 and do nothing. Reviewer bodies accept only
optional `reviewer_name` and `comment`.

Statuses: `BLOCKED`, `PENDING_REVIEW`, `REJECTED`, `APPROVED_FOR_PAYMENT`,
`AWAITING_BUYER_APPROVAL`, `BUYER_APPROVED`, `AUTHORIZATION_PENDING`, `AUTHORIZED`,
`PENDING_CAPTURE` (capture requested, outcome not yet confirmed), `CAPTURE_PENDING` (PayPal
reported the capture PENDING), `CAPTURED`, `PENDING_VOID`, `VOIDED`, `FAILED`. Only the transitions
in `app/services/payments/records.py` are possible; clients cannot set a status.

Safety: each PayPal operation's `PayPal-Request-Id` is stored before the call and reused on every
retry. After a timeout or other ambiguous failure the payment keeps its status and the response
says `retry_safe: true`; repeating the same action cannot create a second order, authorization,
capture or void. Only one action per payment runs at a time (409 `PAYMENT_OPERATION_IN_PROGRESS`).
Errors use `ErrorResponse` with fixed messages; raw PayPal responses, tokens and credentials are
never returned or logged.

## Audit trail (Elastic)

Every meaningful step is recorded as an **allowlisted audit event** and indexed in
Elasticsearch through its REST API (plain `httpx`, no extra package).

| Event type | Recorded when |
|---|---|
| `RISK_ASSESSED` | `POST /api/v1/risk/assess` returns a decision |
| `PAYMENT_CREATED` | A payment record is created (BLOCKED / PENDING_REVIEW / APPROVED_FOR_PAYMENT) |
| `PAYMENT_STATUS_CHANGED` | Every workflow status transition (`previous_status` -> `status`) |
| `REVIEW_APPROVED`, `REVIEW_REJECTED` | A reviewer approves or rejects (reviewer name only; never the comment or token) |
| `PAYPAL_ORDER_CREATED` | An AUTHORIZE-intent order is created |
| `PAYPAL_AUTHORIZATION` | Buyer approval checked with PayPal, authorization created / pending / refreshed |
| `PAYPAL_CAPTURE`, `PAYPAL_VOID` | Capture (COMPLETED or PENDING) and void outcomes |
| `PAYPAL_OPERATION_FAILED` | Any PayPal failure: operation, safe error code, `retry_safe` |

**Never indexed:** intent request text, payment description/purpose, reviewer comments (free
text; only the reviewer name is kept for accountability), PayPal credentials or access tokens, the
reviewer token, PayPal request IDs (idempotency keys), approval URLs, authorization headers or raw
PayPal responses. The `AuditEvent` model rejects unknown fields and the index is created with
`"dynamic": "strict"`, so Elasticsearch rejects them too. (The index mapping still lists a legacy
`comment` field so an index created by the first schema version keeps verifying; it is never
written, is stripped from any document read back, and is not searchable.)

**Configuration:** `ELASTIC_URL`, `ELASTIC_API_KEY` (and optionally `ELASTIC_INDEX`,
`ELASTIC_TIMEOUT_SECONDS`); see `.env.example`. On first use the backend creates the index with
the strict mapping, or verifies an existing index's mapping. It never modifies an existing index;
an incompatible mapping is reported as `ELASTIC_MAPPING_MISMATCH`.

**Never blocks payments.** Events are queued in memory and indexed by a background task.
Each event is written with `PUT /<index>/_create/<event_id>`, so a retry after a lost response
cannot create a duplicate. If Elastic is not configured or unavailable, events stay in an
in-memory fallback buffer (max 1,000; oldest dropped and counted), a warning is logged, and they
are flushed when Elastic is reachable again. Fallback events are **not durable**: they are lost if
the backend restarts.

### `GET /api/v1/audit/events`

Query parameters: `q` (free text), `transaction_id`, `event_type`, `limit` (1-100, default 50),
`offset` (0-9999). Newest first.

```json
{
  "source": "elastic",            // or "memory_fallback"
  "durable": true,                // false for fallback results
  "degraded": false,              // true if Elastic is unavailable or events are not yet stored
  "reason": null,                 // e.g. ELASTIC_NOT_CONFIGURED, ELASTIC_UNREACHABLE, ELASTIC_AUTH_FAILED
  "message": "Results from Elastic.",
  "pending_events": 0,            // held in memory, not yet confirmed in Elastic
  "dropped_events": 0,
  "items": [ { "event_type": "PAYMENT_CREATED", "transaction_id": "txn_...", "...": "..." } ],
  "total": 1, "limit": 50, "offset": 0
}
```

**Authentication:** this endpoint requires the reviewer token, using the same mechanism as the
reviewer actions:

```bash
curl -H "Authorization: Bearer $REVIEWER_TOKEN" "http://localhost:8000/api/v1/audit/events?limit=20"
```

- The token is accepted **only** in the `Authorization: Bearer` header, never as a query
  parameter (a token in the URL is ignored and would also end up in server access logs).
- Missing, malformed or wrong token: `401` with `REVIEWER_AUTH_REQUIRED` or
  `REVIEWER_AUTH_INVALID` and `WWW-Authenticate: Bearer`.
- `REVIEWER_TOKEN` missing or shorter than 32 characters on the server: `503`
  `REVIEWER_AUTH_NOT_CONFIGURED` (fails closed).

## Currencies and amount thresholds

Supported currencies: USD, EUR, GBP, CAD, AUD, INR. No exchange-rate conversion is ever performed;
each currency's amount is only compared against thresholds set in that same currency.

| Currency | Elevated (+15) | Review (+35, at least REVIEW) | Maximum (+60, BLOCK) |
|---|---|---|---|
| USD | 500.00 | 2,000.00 | above 10,000.00 |
| INR | 40,000.00 | 150,000.00 (₹1.5 lakh) | **at or above** 750,000.00 (₹7.5 lakh) |
| EUR, GBP, CAD, AUD | – | – | – (no thresholds: always REVIEW) |
| Any other currency | – | – | – (unsupported: always REVIEW) |

- Amounts within 10% below the review threshold (e.g. INR 135,000.00–149,999.99) add +15 as
  possibly sized to avoid review.
- INR thresholds are INR policy values, not converted from USD.
- An INR payment at or above 750,000.00 is always **BLOCK**, regardless of score or any other
  rule. BLOCK is a rule-level floor; an AI agent recommendation can only make a decision stricter,
  and `/api/v1/risk/assess` rejects any client-supplied score, level, decision or recommendation (422).
- Thresholds live in `app/services/risk/policy.py` (`INR_THRESHOLDS`, `RiskPolicy`).

## Run the tests

```bash
pytest            # run all tests
pytest -v         # verbose, one line per test
pytest tests/api  # only the API endpoint tests
```

Test layout:

```
tests/
  conftest.py       shared fixtures (TestClient, valid schema inputs)
  helpers.py        assert_invalid() validation assertion helper
  intent_fakes.py   FakeGroq test double for the Groq SDK
  api/              HTTP endpoint tests via FastAPI TestClient
  schemas/          Pydantic contract validation tests
  services/         risk engine and intent extraction unit tests
```

Tests never call the real Groq API and never use API credits: the Groq SDK is replaced with
`FakeGroq`, and an autouse fixture in `tests/conftest.py` fails any test that would reach the
real SDK.

`pytest.ini` puts `backend/` on the import path, so tests import the app as `app.*`.
