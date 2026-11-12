import { describe, expect, it } from 'vitest'
import { parseAuditEvent, parseAuditSearch, searchAuditEvents } from './audit'
import { ApiError } from './client'
import { elasticResult, fallbackResult, makeAuditEvent } from '../test/audit'
import { jsonResponse, ok, routeApi } from '../test/http'

const TOKEN = 'FAKE-reviewer-token-' + 'q'.repeat(32)
const ROUTE = 'GET /api/v1/audit/events'

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (err) {
    expect(err).toBeInstanceOf(ApiError)
    return err as ApiError
  }
  throw new Error('expected failure')
}

describe('searchAuditEvents', () => {
  it('sends filters as query parameters and the token only in the Authorization header', async () => {
    const api = routeApi({ [ROUTE]: ok(elasticResult([makeAuditEvent()])) })
    await searchAuditEvents(
      { q: '  bitcoin ', transaction_id: 'txn_abc', event_type: 'PAYPAL_CAPTURE', limit: 25, offset: 50 }, TOKEN)

    const [url, init] = api.mock.calls[0]
    const params = new URL(String(url)).searchParams
    expect(Object.fromEntries(params)).toEqual({
      q: 'bitcoin', transaction_id: 'txn_abc', event_type: 'PAYPAL_CAPTURE', limit: '25', offset: '50' })
    expect(api.headersOf(0).get('authorization')).toBe(`Bearer ${TOKEN}`)
    expect(String(url)).not.toContain(TOKEN)
    expect(init.method).toBe('GET')
  })

  it('omits empty filters and clamps paging', async () => {
    const api = routeApi({ [ROUTE]: ok(elasticResult([])) })
    await searchAuditEvents({ q: ' ', transaction_id: '', event_type: '', limit: 500, offset: -4 }, TOKEN)
    expect(Object.fromEntries(new URL(String(api.mock.calls[0][0])).searchParams)).toEqual({ limit: '100', offset: '0' })
  })

  it('rejects an invalid transaction ID before any request', async () => {
    const api = routeApi({})
    const error = await failure(searchAuditEvents({ transaction_id: 'txn 1/../x', limit: 25, offset: 0 }, TOKEN))
    expect(error.kind).toBe('validation')
    expect(api).not.toHaveBeenCalled()
  })

  it.each([
    [401, 'REVIEWER_AUTH_INVALID', 'reviewer token was rejected'],
    [503, 'REVIEWER_AUTH_NOT_CONFIGURED', 'not configured on the server'],
  ])('maps HTTP %i %s to a fixed message without the token', async (status, code, text) => {
    routeApi({ [ROUTE]: () => jsonResponse(status, { code, message: 'x' }) })
    const error = await failure(searchAuditEvents({ limit: 25, offset: 0 }, TOKEN))
    expect(error.message).toContain(text)
    expect(error.message).not.toContain(TOKEN)
  })
})

describe('parseAuditSearch / parseAuditEvent', () => {
  it('accepts Elastic and fallback results', () => {
    expect(parseAuditSearch(elasticResult([makeAuditEvent()])).source).toBe('elastic')
    const fallback = parseAuditSearch(fallbackResult([makeAuditEvent()]))
    expect(fallback.durable).toBe(false)
    expect(fallback.degraded).toBe(true)
  })

  it('never accepts memory fallback results claimed as durable', () => {
    expect(() => parseAuditSearch({ ...fallbackResult([]), durable: true })).toThrowError(ApiError)
  })

  it.each<[string, Record<string, unknown>]>([
    ['a reviewer comment', { comment: 'free text' }],
    ['a raw request text', { request_text: 'Pay ...' }],
    ['a PayPal request ID', { capture_request_id: 'agentpay-capture-x' }],
    ['a description', { description: 'Monthly hosting' }],
  ])('rejects an event carrying %s', (_label, extra) => {
    expect(() => parseAuditEvent({ ...makeAuditEvent(), ...extra })).toThrowError(ApiError)
  })

  it.each<[string, Record<string, unknown>]>([
    ['unknown event type', { event_type: 'SOMETHING_ELSE' }],
    ['bad event id', { event_id: 'not-hex' }],
    ['bad timestamp', { occurred_at: 'yesterday' }],
    ['non-string status', { status: 5 }],
    ['out-of-range score', { risk_score: 120 }],
  ])('rejects an event with %s', (_label, change) => {
    expect(() => parseAuditEvent({ ...makeAuditEvent(), ...change })).toThrowError(ApiError)
  })

  it('accepts events with omitted optional fields', () => {
    const { reasons: _r, risk_score: _s, ...minimal } = makeAuditEvent({ event_type: 'PAYMENT_STATUS_CHANGED' })
    const event = parseAuditEvent(minimal)
    expect(event.reasons).toEqual([])
    expect(event.risk_score).toBeNull()
  })
})
