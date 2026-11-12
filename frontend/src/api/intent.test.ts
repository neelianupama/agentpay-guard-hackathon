import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { INTENT_TIMEOUT_MS, parsePaymentIntent, parseParsedPaymentIntent } from './intent'
import {
  EXAMPLE_REQUEST,
  INTENT_URL,
  deferredFetch,
  makeCompleteIntent,
  makeParsedIntent,
  mockFetch,
  respondWith,
  sentBody,
} from '../test/http'

async function parseError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (err) {
    expect(err).toBeInstanceOf(ApiError)
    return err as ApiError
  }
  throw new Error('expected parsePaymentIntent to fail')
}

describe('parsePaymentIntent: request', () => {
  it('POSTs only request_text to /api/v1/intent/parse', async () => {
    const fetch = respondWith(200, makeParsedIntent())
    await parsePaymentIntent(EXAMPLE_REQUEST)

    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe(INTENT_URL)
    expect(init.method).toBe('POST')
    expect(sentBody(fetch)).toEqual({ request_text: EXAMPLE_REQUEST })
  })

  it('returns the parsed intent unchanged, including nulls', async () => {
    respondWith(200, makeParsedIntent())
    await expect(parsePaymentIntent(EXAMPLE_REQUEST)).resolves.toEqual(makeParsedIntent())
  })

  it('accepts a complete intent', async () => {
    respondWith(200, makeCompleteIntent())
    const result = await parsePaymentIntent(EXAMPLE_REQUEST)
    expect(result.missing_fields).toEqual([])
    expect(result.clarification_needed).toBe(false)
  })

  it('returns non-USD currencies as stated', async () => {
    respondWith(200, makeParsedIntent({ amount: '5000', currency: 'INR' }))
    const result = await parsePaymentIntent('Pay ₹5000 to ABC Technologies for software services.')
    expect(result.currency).toBe('INR')
    expect(result.amount).toBe('5000')
  })
})

describe('parsePaymentIntent: errors', () => {
  it.each([
    [429, 'INTENT_RATE_LIMITED', 'Too many parsing requests right now.'],
    [502, 'INTENT_EXTRACTION_FAILED', 'The AI could not read this request.'],
    [503, 'INTENT_SERVICE_UNAVAILABLE', 'AI parsing is not available right now.'],
    [503, 'INTENT_MODEL_UNAVAILABLE', 'AI parsing is not available right now.'],
    [504, 'INTENT_TIMEOUT', 'The AI took too long to read the request.'],
  ])('maps HTTP %i %s to a fixed message', async (status, code, message) => {
    respondWith(status, { code, message: 'backend text with provider detail gsk_secret' })
    const error = await parseError(parsePaymentIntent(EXAMPLE_REQUEST))
    expect(error.kind).toBe('http')
    expect(error.code).toBe(code)
    expect(error.status).toBe(status)
    expect(error.message).toContain(message)
    expect(error.message).not.toContain('gsk_secret')
    expect(error.message).not.toContain('backend text')
  })

  it('uses a generic message for unknown error codes and plain 500s', async () => {
    respondWith(500, { detail: 'Internal Server Error: provider exploded' })
    const error = await parseError(parsePaymentIntent(EXAMPLE_REQUEST))
    expect(error.message).toBe('AI parsing failed (HTTP 500). You can enter the details manually.')
  })

  it('ignores malformed error codes', async () => {
    respondWith(503, { code: '<script>', message: 'x' })
    const error = await parseError(parsePaymentIntent(EXAMPLE_REQUEST))
    expect(error.code).toBeUndefined()
    expect(error.message).toContain('HTTP 503')
  })

  it('maps 422 to request_text field errors', async () => {
    respondWith(422, {
      detail: [
        { type: 'string_too_short', loc: ['body', 'request_text'], msg: 'String should have at least 1 character' },
      ],
    })
    const error = await parseError(parsePaymentIntent(' '))
    expect(error.kind).toBe('validation')
    expect(error.fieldErrors).toEqual({ request_text: 'String should have at least 1 character' })
  })

  it('names the AI parsing service when the backend is unreachable', async () => {
    mockFetch(async () => {
      throw new TypeError('Failed to fetch')
    })
    const error = await parseError(parsePaymentIntent(EXAMPLE_REQUEST))
    expect(error.kind).toBe('network')
    expect(error.message).toBe(
      'Could not reach the AI parsing service at http://localhost:8000. Check that the backend is running.',
    )
  })

  it('waits longer than risk assessment before timing out', async () => {
    vi.useFakeTimers()
    deferredFetch()
    let settled = false
    const outcome = parseError(parsePaymentIntent(EXAMPLE_REQUEST)).then((error) => {
      settled = true
      return error
    })

    await vi.advanceTimersByTimeAsync(15_000)
    expect(settled).toBe(false) // still waiting after the risk endpoint's 15 s budget
    await vi.advanceTimersByTimeAsync(INTENT_TIMEOUT_MS - 15_000)

    const error = await outcome
    expect(INTENT_TIMEOUT_MS).toBeGreaterThan(15_000)
    expect(error.kind).toBe('timeout')
    expect(error.message).toContain('The AI took too long to read the request.')
  })

  it('passes cancellation through', async () => {
    deferredFetch()
    const controller = new AbortController()
    const outcome = parseError(parsePaymentIntent(EXAMPLE_REQUEST, { signal: controller.signal }))
    controller.abort()
    expect((await outcome).kind).toBe('aborted')
  })
})

describe('parseParsedPaymentIntent: contract checks', () => {
  const valid = makeParsedIntent()

  it.each<[string, unknown]>([
    ['not an object', 'ABC'],
    ['an array', [valid]],
    ['a risk decision', { ...valid, decision: 'ALLOW' }],
    ['a risk score', { ...valid, risk_score: 0 }],
    ['an approval flag', { ...valid, approved: true }],
    ['a missing key', (({ purpose: _p, ...rest }) => rest)(valid)],
    ['a numeric amount', { ...valid, amount: 250 }],
    ['an empty-string detail', { ...valid, merchant_name: '' }],
    ['a non-boolean clarification flag', { ...valid, clarification_needed: 'yes' }],
    ['missing_fields not matching null details', { ...valid, missing_fields: [] }],
    ['missing_fields listing a present detail', { ...valid, missing_fields: ['amount', 'purpose'] }],
    ['missing details without clarification', { ...valid, clarification_needed: false }],
    ['missing_fields out of order', { ...valid, amount: null, missing_fields: ['purpose', 'amount'] }],
  ])('rejects a response with %s', (_label, data) => {
    expect(() => parseParsedPaymentIntent(data)).toThrowError(ApiError)
    try {
      parseParsedPaymentIntent(data)
    } catch (err) {
      expect((err as ApiError).kind).toBe('invalid_response')
    }
  })

  it('accepts ambiguity flagged on a complete intent', () => {
    const data = makeCompleteIntent({ clarification_needed: true })
    expect(parseParsedPaymentIntent(data)).toEqual(data)
  })
})
