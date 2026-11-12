/**
 * Test helpers for mocking the HTTP boundary (global fetch).
 *
 * Component and API tests replace `fetch` only; everything above it
 * (API client, response parsing, hooks, components) runs for real.
 */

import { vi } from 'vitest'
import type { ParsedPaymentIntent } from '../api/intent'
import type { Payment, PaymentAction } from '../api/payments'
import type { PolicyDecision, RiskAssessment, RiskLevel, TransactionDetails } from '../api/risk'

export const API_URL = 'http://localhost:8000/api/v1/risk/assess'
export const INTENT_URL = 'http://localhost:8000/api/v1/intent/parse'

export const VALID_DETAILS: TransactionDetails = {
  merchant_name: 'Acme Cloud',
  amount: '49.99',
  currency: 'USD',
  description: 'Monthly hosting invoice',
  purpose: 'Infrastructure hosting',
}

/** A response shaped exactly like the backend RiskAssessment contract. */
export function makeAssessment(overrides: Partial<RiskAssessment> = {}): RiskAssessment {
  return {
    transaction: { ...VALID_DETAILS },
    risk_score: 0,
    risk_level: 'LOW',
    decision: 'ALLOW',
    reasons: ['No risk rules were triggered. Merchant identity has not been independently verified.'],
    assessed_at: '2026-11-12T06:30:00Z',
    ...overrides,
  }
}

export const DECISION_CASES: {
  decision: PolicyDecision
  level: RiskLevel
  score: number
  title: string
  reasons: string[]
}[] = [
  {
    decision: 'ALLOW',
    level: 'LOW',
    score: 0,
    title: 'Allowed by policy',
    reasons: ['No risk rules were triggered. Merchant identity has not been independently verified.'],
  },
  {
    decision: 'REVIEW',
    level: 'HIGH',
    score: 50,
    title: 'Human review required',
    reasons: [
      'Amount 2,500.00 USD is at or above the human-review threshold of 2,000.00 USD.',
      "Urgency or pressure language 'urgent' found in description.",
    ],
  },
  {
    decision: 'BLOCK',
    level: 'CRITICAL',
    score: 80,
    title: 'Blocked by policy',
    reasons: [
      'Amount 3,000.00 USD is at or above the human-review threshold of 2,000.00 USD.',
      "High-risk payment terms 'bitcoin' found in description.",
      "Urgency or pressure language 'urgent' found in description.",
    ],
  },
]

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Install a fetch mock and return it for assertions. */
export function mockFetch(impl: (url: string, init: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl)
  vi.stubGlobal('fetch', fn)
  return fn
}

export function respondWith(status: number, body: unknown) {
  return mockFetch(async () => jsonResponse(status, body))
}

/** A fetch that stays pending until resolved, or rejects when its signal aborts. */
export function deferredFetch() {
  let resolve: (response: Response) => void = () => {}
  const fn = mockFetch(
    (_url, init) =>
      new Promise<Response>((res, rej) => {
        resolve = res
        init.signal?.addEventListener('abort', () =>
          rej(new DOMException('The operation was aborted.', 'AbortError')),
        )
      }),
  )
  return { fetch: fn, resolve: (response: Response) => resolve(response) }
}

/** Parse the JSON body of the n-th fetch call. */
export function sentBody(fn: ReturnType<typeof mockFetch>, call = 0): unknown {
  const init = fn.mock.calls[call][1]
  return JSON.parse(String(init.body))
}

export const EXAMPLE_REQUEST = 'Pay 250 USD to ABC Technologies for software services.'

/** A response shaped exactly like the backend ParsedPaymentIntent contract (EXAMPLE_REQUEST). */
export function makeParsedIntent(overrides: Partial<ParsedPaymentIntent> = {}): ParsedPaymentIntent {
  const base: ParsedPaymentIntent = {
    merchant_name: 'ABC Technologies',
    amount: '250',
    currency: 'USD',
    description: 'software services',
    purpose: null,
    missing_fields: ['purpose'],
    clarification_needed: true,
    ...overrides,
  }
  return base
}

/** A complete intent: every detail found, no clarification needed. */
export function makeCompleteIntent(overrides: Partial<ParsedPaymentIntent> = {}): ParsedPaymentIntent {
  return makeParsedIntent({
    purpose: 'Q3 tooling budget',
    missing_fields: [],
    clarification_needed: false,
    ...overrides,
  })
}

type Handler = (init: RequestInit) => Promise<Response> | Response

/**
 * Fetch mock that routes by endpoint. A call to an endpoint without a handler
 * rejects (surfacing as a network error) and is still recorded, so tests can
 * assert with `calledUrls` that e.g. no risk assessment was requested.
 */
export function routeFetch(handlers: { intent?: Handler; risk?: Handler }) {
  return mockFetch(async (url, init) => {
    const handler = url === INTENT_URL ? handlers.intent : url === API_URL ? handlers.risk : undefined
    if (!handler) throw new Error(`Unexpected request to ${url}`)
    return handler(init)
  })
}

/** URLs of every fetch call made so far. */
export function calledUrls(fn: ReturnType<typeof mockFetch>): string[] {
  return fn.mock.calls.map(([url]) => String(url))
}

// ---------------------------------------------------------------- payments API

export const PAYMENTS_URL = 'http://localhost:8000/api/v1/payments'
export const TXN = 'txn_0123456789abcdef01234567'
export const SANDBOX_APPROVAL_URL = 'https://www.sandbox.paypal.com/checkoutnow?token=ORD00000000000001'

/** A response shaped exactly like the backend PaymentRecordResponse (an ALLOW payment). */
export function makePayment(overrides: Partial<Payment> = {}): Payment {
  return {
    transaction_id: TXN,
    status: 'AWAITING_BUYER_APPROVAL',
    merchant_name: 'Acme Cloud',
    amount: '49.99',
    currency: 'USD',
    description: 'Monthly hosting invoice',
    purpose: 'Infrastructure hosting',
    risk: {
      risk_score: 0,
      risk_level: 'LOW',
      decision: 'ALLOW',
      reasons: ['No risk rules were triggered. Merchant identity has not been independently verified.'],
      assessed_at: '2026-11-12T06:30:00Z',
    },
    paypal: {
      order_id: 'ORD00000000000001',
      approval_url: SANDBOX_APPROVAL_URL,
      order_status: 'PAYER_ACTION_REQUIRED',
      authorization_id: null,
      authorization_status: null,
      capture_id: null,
      capture_status: null,
    },
    review: null,
    reviewer_actions: [],
    last_error: null,
    next_actions: ['buyer_approves_on_paypal', 'authorize'],
    created_at: '2026-11-12T06:30:00Z',
    updated_at: '2026-11-12T06:30:00Z',
    ...overrides,
  }
}

/** Payment in a given workflow status, with matching PayPal fields and next_actions. */
export function paymentIn(status: Payment['status'], overrides: Partial<Payment> = {}): Payment {
  const base = makePayment()
  const noPayPal = { ...base.paypal, order_id: null, approval_url: null, order_status: null }
  const authorized = {
    ...base.paypal, approval_url: null, order_status: 'COMPLETED',
    authorization_id: 'AUT00000000000002', authorization_status: 'CREATED',
  }
  const presets: Record<string, Partial<Payment>> = {
    BLOCKED: {
      paypal: noPayPal, next_actions: [],
      risk: { ...base.risk, risk_score: 80, risk_level: 'CRITICAL', decision: 'BLOCK',
        reasons: ["High-risk payment terms 'bitcoin' found in description."] },
    },
    PENDING_REVIEW: {
      paypal: noPayPal, next_actions: ['approve', 'reject'], amount: '2500.00',
      risk: { ...base.risk, risk_score: 35, risk_level: 'MEDIUM', decision: 'REVIEW',
        reasons: ['Amount 2,500.00 USD is at or above the human-review threshold of 2,000.00 USD.'] },
    },
    REJECTED: { paypal: noPayPal, next_actions: [] },
    APPROVED_FOR_PAYMENT: { paypal: noPayPal, next_actions: ['create_order'] },
    AWAITING_BUYER_APPROVAL: {},
    AUTHORIZATION_PENDING: {
      paypal: { ...authorized, authorization_status: 'PENDING' }, next_actions: ['authorize'],
    },
    AUTHORIZED: { paypal: authorized, next_actions: ['capture', 'void'] },
    PENDING_CAPTURE: { paypal: authorized, next_actions: ['capture'] },
    CAPTURE_PENDING: {
      paypal: { ...authorized, authorization_status: 'CAPTURED', capture_id: 'CAP00000000000003',
        capture_status: 'PENDING' }, next_actions: [],
    },
    CAPTURED: {
      paypal: { ...authorized, authorization_status: 'CAPTURED', capture_id: 'CAP00000000000003',
        capture_status: 'COMPLETED' }, next_actions: [],
    },
    PENDING_VOID: { paypal: authorized, next_actions: ['void'] },
    VOIDED: { paypal: { ...authorized, authorization_status: 'VOIDED' }, next_actions: [] },
    FAILED: { paypal: noPayPal, next_actions: [] },
  }
  return makePayment({ status, ...presets[status], ...overrides })
}

export function paymentList(items: Payment[], total = items.length, limit = 10, offset = 0) {
  return { items, total, limit, offset }
}

type RouteHandler = (init: RequestInit, url: URL) => Response | Promise<Response>

/**
 * Fetch mock keyed by "METHOD /path" (path without query). Unknown routes
 * reject (surfacing as a network error) and are still recorded, so tests can
 * assert with `calls` that e.g. no capture request was ever made. Honours
 * AbortSignal like real fetch (needed to reproduce React StrictMode behaviour).
 */
export function routeApi(routes: Record<string, RouteHandler>) {
  const fn = mockFetch(async (url, init) => {
    const parsed = new URL(url)
    const key = `${init.method ?? 'GET'} ${parsed.pathname}`
    const handler = routes[key]
    if (!handler) throw new Error(`Unexpected request: ${key}`)
    const response = await handler(init, parsed)
    // Like real fetch: a request aborted before it settles rejects with AbortError.
    await new Promise((resolve) => setTimeout(resolve, 0))
    if (init.signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError')
    return response
  })
  return Object.assign(fn, {
    calls: () => fn.mock.calls.map(([url, init]) => `${init.method ?? 'GET'} ${new URL(String(url)).pathname}`),
    headersOf: (index: number) => new Headers(fn.mock.calls[index][1].headers as HeadersInit),
  })
}

export const ok = (body: unknown, status = 200) => () => jsonResponse(status, body)

const CLIENT_NEXT_ACTIONS = ['create_order', 'buyer_approves_on_paypal', 'authorize']

/** The minimal PaymentActionResponse the backend derives from a full record. */
export function actionOf(payment: Payment): PaymentAction {
  return {
    transaction_id: payment.transaction_id,
    status: payment.status,
    merchant_name: payment.merchant_name,
    amount: payment.amount,
    currency: payment.currency,
    approval_url: payment.status === 'AWAITING_BUYER_APPROVAL' ? payment.paypal.approval_url : null,
    next_actions: payment.next_actions.filter((a) => CLIENT_NEXT_ACTIONS.includes(a)),
    last_error: payment.last_error
      ? { code: payment.last_error.code, retry_safe: payment.last_error.retry_safe }
      : null,
  }
}

/** Minimal action view in a given workflow status. */
export function actionIn(status: Payment['status'], overrides: Partial<Payment> = {}): PaymentAction {
  return actionOf(paymentIn(status, overrides))
}
