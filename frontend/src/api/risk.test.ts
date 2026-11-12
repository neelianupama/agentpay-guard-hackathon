import { describe, expect, it } from 'vitest'
import {
  API_URL,
  VALID_DETAILS,
  deferredFetch,
  jsonResponse,
  makeAssessment,
  mockFetch,
  respondWith,
  sentBody,
} from '../test/http'
import { API_BASE_URL, ApiError, parseValidationErrors } from './client'
import { assessRisk, parseRiskAssessment, type TransactionDetails } from './risk'

async function expectApiError(promise: Promise<unknown>, kind: ApiError['kind']): Promise<ApiError> {
  const error = await promise.then(
    () => {
      throw new Error('expected the request to fail')
    },
    (e: unknown) => e,
  )
  expect(error).toBeInstanceOf(ApiError)
  expect((error as ApiError).kind).toBe(kind)
  return error as ApiError
}

describe('assessRisk request', () => {
  it('uses VITE_API_BASE_URL', () => {
    expect(API_BASE_URL).toBe('http://localhost:8000')
  })

  it('POSTs JSON to /api/v1/risk/assess', async () => {
    const fetch = respondWith(200, makeAssessment())
    await assessRisk(VALID_DETAILS)

    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe(API_URL)
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' })
    expect(sentBody(fetch)).toEqual(VALID_DETAILS)
  })

  it('never sends fields outside the TransactionDetails contract', async () => {
    const fetch = respondWith(200, makeAssessment())
    const tampered = {
      ...VALID_DETAILS,
      risk_score: 0,
      risk_level: 'LOW',
      decision: 'ALLOW',
      agent_recommendation: 'ALLOW',
    } as unknown as TransactionDetails

    await assessRisk(tampered)

    expect(Object.keys(sentBody(fetch) as object).sort()).toEqual([
      'amount',
      'currency',
      'description',
      'merchant_name',
      'purpose',
    ])
  })

  it('returns the backend assessment unchanged', async () => {
    const assessment = makeAssessment({ risk_score: 35, risk_level: 'MEDIUM', decision: 'REVIEW' })
    respondWith(200, assessment)
    await expect(assessRisk(VALID_DETAILS)).resolves.toEqual(assessment)
  })
})

describe('assessRisk failures', () => {
  it('maps HTTP 422 to field errors', async () => {
    respondWith(422, {
      detail: [
        { type: 'greater_than', loc: ['body', 'amount'], msg: 'Input should be greater than 0' },
        { type: 'string_pattern_mismatch', loc: ['body', 'currency'], msg: "String should match pattern '^[A-Z]{3}$'" },
      ],
    })
    const error = await expectApiError(assessRisk(VALID_DETAILS), 'validation')
    expect(error.status).toBe(422)
    expect(error.fieldErrors).toEqual({
      amount: 'Input should be greater than 0',
      currency: "String should match pattern '^[A-Z]{3}$'",
    })
  })

  it('reports an unreachable backend as a network error', async () => {
    mockFetch(async () => {
      throw new TypeError('Failed to fetch')
    })
    const error = await expectApiError(assessRisk(VALID_DETAILS), 'network')
    expect(error.message).toBe(
      'Could not reach the risk service at http://localhost:8000. Check that the backend is running.',
    )
  })

  it('times out a request that never completes', async () => {
    deferredFetch()
    const error = await expectApiError(assessRisk(VALID_DETAILS, { timeoutMs: 20 }), 'timeout')
    expect(error.message).toBe('The risk service took too long to respond.')
  })

  it('reports caller cancellation as aborted', async () => {
    deferredFetch()
    const controller = new AbortController()
    const pending = assessRisk(VALID_DETAILS, { signal: controller.signal })
    controller.abort()
    await expectApiError(pending, 'aborted')
  })

  it('reports other HTTP errors with their status', async () => {
    respondWith(500, { detail: 'Internal Server Error' })
    const error = await expectApiError(assessRisk(VALID_DETAILS), 'http')
    expect(error.status).toBe(500)
    expect(error.message).toBe('The risk service returned an error (HTTP 500).')
  })

  it('rejects a non-JSON success response', async () => {
    mockFetch(async () => new Response('<html>oops</html>', { status: 200 }))
    await expectApiError(assessRisk(VALID_DETAILS), 'invalid_response')
  })
})

describe('parseRiskAssessment', () => {
  it.each([
    ['unknown decision', { decision: 'MAYBE' }],
    ['unknown level', { risk_level: 'SEVERE' }],
    ['score above 100', { risk_score: 101 }],
    ['negative score', { risk_score: -1 }],
    ['fractional score', { risk_score: 12.5 }],
    ['empty reasons', { reasons: [] }],
    ['non-string reason', { reasons: [42] }],
    ['invalid timestamp', { assessed_at: 'yesterday' }],
    ['missing transaction', { transaction: undefined }],
  ])('rejects a response with %s', (_label, patch) => {
    const bad = { ...makeAssessment(), ...patch }
    expect(() => parseRiskAssessment(bad)).toThrow(ApiError)
  })

  it('accepts a valid response', () => {
    const assessment = makeAssessment()
    expect(parseRiskAssessment(JSON.parse(JSON.stringify(assessment)))).toEqual(assessment)
  })
})

describe('parseValidationErrors', () => {
  it('assigns non-field errors to _form and keeps the first message per field', () => {
    expect(
      parseValidationErrors({
        detail: [
          { loc: ['body'], msg: 'Field required' },
          { loc: ['body', 'amount'], msg: 'first' },
          { loc: ['body', 'amount'], msg: 'second' },
        ],
      }),
    ).toEqual({ _form: 'Field required', amount: 'first' })
  })

  it('returns no errors for an unexpected body', () => {
    expect(parseValidationErrors({ detail: 'nope' })).toEqual({})
    expect(parseValidationErrors(null)).toEqual({})
  })

  it('ignores a 422 response without JSON', async () => {
    mockFetch(async () => new Response('bad', { status: 422 }))
    const error = await expectApiError(assessRisk(VALID_DETAILS), 'validation')
    expect(error.fieldErrors).toEqual({})
  })

  it('jsonResponse helper produces readable JSON', async () => {
    await expect(jsonResponse(200, { a: 1 }).json()).resolves.toEqual({ a: 1 })
  })
})
