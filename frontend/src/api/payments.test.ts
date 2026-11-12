import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
import {
  authorizePayment,
  createPayment,
  createPaymentOrder,
  getPayment,
  isSandboxApprovalUrl,
  listPayments,
  parsePaymentAction,
  parsePayment,
  reviewerAction,
} from './payments'
import {
  TXN,
  actionOf,
  VALID_DETAILS,
  jsonResponse,
  makePayment,
  ok,
  paymentIn,
  paymentList,
  routeApi,
  sentBody,
} from '../test/http'

const TOKEN = 'FAKE-reviewer-token-' + 'x'.repeat(32)

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (err) {
    expect(err).toBeInstanceOf(ApiError)
    return err as ApiError
  }
  throw new Error('expected failure')
}

describe('payments API: requests', () => {
  it('creates a payment with exactly the five TransactionDetails fields', async () => {
    const api = routeApi({ 'POST /api/v1/payments': ok(actionOf(makePayment()), 201) })
    const extra = { ...VALID_DETAILS, risk_score: 0, decision: 'ALLOW' } as typeof VALID_DETAILS
    const payment = await createPayment(extra)
    expect(api.calls()).toEqual(['POST /api/v1/payments'])
    expect(sentBody(api)).toEqual(VALID_DETAILS)
    expect(payment.status).toBe('AWAITING_BUYER_APPROVAL')
  })

  it('lists with clamped pagination', async () => {
    const api = routeApi({ 'GET /api/v1/payments': ok(paymentList([makePayment()])) })
    await listPayments({ limit: 500, offset: -3 }, TOKEN)
    const url = new URL(String(api.mock.calls[0][0]))
    expect(url.searchParams.get('limit')).toBe('100')
    expect(url.searchParams.get('offset')).toBe('0')
    expect(api.mock.calls[0][1].method).toBe('GET')
    expect(api.mock.calls[0][1].body).toBeUndefined()
  })

  it.each([
    ['createPaymentOrder', () => createPaymentOrder(TXN), `POST /api/v1/payments/${TXN}/order`],
    ['authorizePayment', () => authorizePayment(TXN), `POST /api/v1/payments/${TXN}/authorize`],
  ])('%s calls the right endpoint without an Authorization header', async (_name, call, route) => {
    const api = routeApi({ [route]: ok(actionOf(makePayment())) })
    await call()
    expect(api.calls()).toEqual([route])
    expect(api.headersOf(0).has('authorization')).toBe(false)
  })

  it.each([
    ['listPayments', () => listPayments({ limit: 10, offset: 0 }, TOKEN), 'GET /api/v1/payments'],
    ['getPayment', () => getPayment(TXN, TOKEN), `GET /api/v1/payments/${TXN}`],
  ])('%s sends the reviewer token only in the Authorization header', async (_name, call, route) => {
    const api = routeApi({
      'GET /api/v1/payments': ok(paymentList([makePayment()])),
      [`GET /api/v1/payments/${TXN}`]: ok(makePayment()),
    })
    await call()
    const [url, init] = api.mock.calls[0]
    expect(api.calls()).toEqual([route])
    expect(api.headersOf(0).get('authorization')).toBe(`Bearer ${TOKEN}`)
    expect(String(url)).not.toContain(TOKEN)
    expect(init.body).toBeUndefined()
  })

  it('a rejected token produces a fixed message that never contains the token', async () => {
    routeApi({ 'GET /api/v1/payments': () => jsonResponse(401, { code: 'REVIEWER_AUTH_INVALID', message: 'x' }) })
    const error = await failure(listPayments({ limit: 10, offset: 0 }, TOKEN))
    expect(error.status).toBe(401)
    expect(error.message).toContain('reviewer token was rejected')
    expect(error.message).not.toContain(TOKEN)
  })

  it('authorize sends no client-side approval flags', async () => {
    const api = routeApi({ [`POST /api/v1/payments/${TXN}/authorize`]: ok(actionOf(paymentIn('AUTHORIZED'))) })
    await authorizePayment(TXN)
    expect(sentBody(api)).toEqual({})
  })

  it.each(['approve', 'reject', 'capture', 'void'] as const)(
    'reviewer %s sends the token only in the Authorization header',
    async (action) => {
      const api = routeApi({ [`POST /api/v1/payments/${TXN}/${action}`]: ok(paymentIn('PENDING_REVIEW')) })
      await reviewerAction(TXN, action, TOKEN, { reviewer_name: ' Asha ', comment: '  ok  ' })
      const [url, init] = api.mock.calls[0]
      expect(api.headersOf(0).get('authorization')).toBe(`Bearer ${TOKEN}`)
      expect(String(url)).not.toContain(TOKEN)
      expect(String(init.body)).not.toContain(TOKEN)
      expect(sentBody(api)).toEqual({ reviewer_name: 'Asha', comment: 'ok' })
    },
  )

  it('omits empty reviewer fields', async () => {
    const api = routeApi({ [`POST /api/v1/payments/${TXN}/approve`]: ok(paymentIn('PENDING_REVIEW')) })
    await reviewerAction(TXN, 'approve', TOKEN, { reviewer_name: '  ', comment: '' })
    expect(sentBody(api)).toEqual({})
  })

  it.each(['../risk/assess', 'txn 1', 'a/b', ''])('rejects unsafe transaction id %j before any request', async (id) => {
    const api = routeApi({})
    await failure(getPayment(id, TOKEN))
    expect(api).not.toHaveBeenCalled()
  })
})

describe('payments API: errors', () => {
  it.each([
    [409, 'PAYMENT_INVALID_STATE', "isn't allowed for the payment's current status"],
    [409, 'PAYMENT_BUYER_APPROVAL_REQUIRED', 'Approve it on PayPal first'],
    [409, 'PAYMENT_OPERATION_IN_PROGRESS', 'in progress'],
    [404, 'PAYMENT_NOT_FOUND', 'not found'],
    [422, 'PAYPAL_CURRENCY_NOT_SUPPORTED', 'never converted'],
    [504, 'PAYPAL_TIMEOUT', 'did not respond in time'],
    [401, 'REVIEWER_AUTH_INVALID', 'reviewer token was rejected'],
    [503, 'REVIEWER_AUTH_NOT_CONFIGURED', 'not configured on the server'],
  ])('maps HTTP %i %s to a fixed message', async (status, code, text) => {
    routeApi({ [`GET /api/v1/payments/${TXN}`]: () =>
      jsonResponse(status, { code, message: 'backend text FAKE-raw-paypal' }) })
    const error = await failure(getPayment(TXN, TOKEN))
    expect(error.code).toBe(code)
    expect(error.message).toContain(text)
    expect(error.message).not.toContain('FAKE-raw-paypal')
  })

  it('keeps safe details and adds a retry hint', async () => {
    routeApi({ 'POST /api/v1/payments': () => jsonResponse(504, {
      code: 'PAYPAL_TIMEOUT', message: 'x',
      details: { transaction_id: TXN, status: 'APPROVED_FOR_PAYMENT', retry_safe: true, secret: 'nope' },
    }) })
    const error = await failure(createPayment(VALID_DETAILS))
    expect(error.details).toEqual({ transaction_id: TXN, status: 'APPROVED_FOR_PAYMENT', retry_safe: true })
    expect(error.message).toContain('safe to try the same action again')
  })

  it('names the payment service when the backend is unreachable', async () => {
    const error = await failure(getPayment(TXN, TOKEN))  // default unmocked fetch rejects
    expect(error.kind).toBe('network')
    expect(error.message).toContain('payment service')
  })
})

describe('parsePayment', () => {
  it('accepts every status preset', () => {
    for (const status of ['BLOCKED', 'PENDING_REVIEW', 'AUTHORIZED', 'CAPTURED', 'VOIDED'] as const) {
      expect(parsePayment(paymentIn(status)).status).toBe(status)
    }
  })

  it.each<[string, unknown]>([
    ['unknown status', { ...makePayment(), status: 'PAID' }],
    ['risk decision invalid', { ...makePayment(), risk: { ...makePayment().risk, decision: 'APPROVED' } }],
    ['risk score out of range', { ...makePayment(), risk: { ...makePayment().risk, risk_score: 101 } }],
    ['numeric amount', { ...makePayment(), amount: 49.99 }],
    ['non-Sandbox approval url', { ...makePayment(), paypal: { ...makePayment().paypal,
      approval_url: 'https://www.paypal.com/checkoutnow?token=X' } }],
    ['phishing approval url', { ...makePayment(), paypal: { ...makePayment().paypal,
      approval_url: 'https://attacker.example/www.sandbox.paypal.com' } }],
    ['http approval url', { ...makePayment(), paypal: { ...makePayment().paypal,
      approval_url: 'http://www.sandbox.paypal.com/checkoutnow' } }],
    ['next_actions missing', { ...makePayment(), next_actions: undefined }],
    ['not an object', 'payment'],
  ])('rejects a response with %s', (_label, data) => {
    expect(() => parsePayment(data)).toThrowError(ApiError)
  })

  it('isSandboxApprovalUrl only allows https www.sandbox.paypal.com', () => {
    expect(isSandboxApprovalUrl('https://www.sandbox.paypal.com/checkoutnow?token=A')).toBe(true)
    expect(isSandboxApprovalUrl('https://u:p@www.sandbox.paypal.com/x')).toBe(false)
    expect(isSandboxApprovalUrl('https://www.sandbox.paypal.com:8443/x')).toBe(false)
    expect(isSandboxApprovalUrl('javascript:alert(1)')).toBe(false)
  })
})


describe('parsePaymentAction (minimal buyer view)', () => {
  it('accepts the minimal shape', () => {
    const action = actionOf(makePayment())
    expect(parsePaymentAction(action)).toEqual(action)
    expect(Object.keys(parsePaymentAction(action)).sort()).toEqual(
      ['amount', 'approval_url', 'currency', 'last_error', 'merchant_name', 'next_actions', 'status', 'transaction_id'])
  })

  it.each<[string, Record<string, unknown>]>([
    ['risk data', { risk: makePayment().risk }],
    ['reviewer data', { review: { reviewer_name: 'Asha', comment: 'x' } }],
    ['reviewer actions', { reviewer_actions: [] }],
    ['PayPal IDs', { paypal: makePayment().paypal }],
    ['description', { description: 'x' }],
  ])('rejects a response that adds %s', (_label, extra) => {
    expect(() => parsePaymentAction({ ...actionOf(makePayment()), ...extra })).toThrowError(ApiError)
  })

  it.each<[string, Record<string, unknown>]>([
    ['a reviewer action in next_actions', { next_actions: ['capture'] }],
    ['a non-Sandbox approval URL', { approval_url: 'https://www.paypal.com/checkoutnow?token=X' }],
    ['an error message', { last_error: { code: 'X', retry_safe: true, message: 'raw' } }],
    ['a missing field', { status: undefined }],
  ])('rejects %s', (_label, change) => {
    const data = { ...actionOf(makePayment()), ...change }
    if (change.status === undefined && 'status' in change) delete (data as Record<string, unknown>).status
    expect(() => parsePaymentAction(data)).toThrowError(ApiError)
  })
})
