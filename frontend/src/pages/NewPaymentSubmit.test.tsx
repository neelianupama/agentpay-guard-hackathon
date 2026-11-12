/**
 * New Payment -> POST /api/v1/payments -> backend-driven next steps.
 * Only fetch is mocked (routeApi); capture is never reachable from this page.
 */
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import {
  DECISION_CASES,
  SANDBOX_APPROVAL_URL,
  TXN,
  VALID_DETAILS,
  deferredFetch,
  jsonResponse,
  makeAssessment,
  actionIn,
  ok,
  routeApi,
  sentBody,
} from '../test/http'
import { NewPaymentPage } from './NewPaymentPage'

type User = ReturnType<typeof userEvent.setup>

const PAY = 'POST /api/v1/payments'
const ASSESS = 'POST /api/v1/risk/assess'
const AUTHORIZE = `POST /api/v1/payments/${TXN}/authorize`
const ORDER = `POST /api/v1/payments/${TXN}/order`

const [ALLOW_CASE, REVIEW_CASE, BLOCK_CASE] = DECISION_CASES
const assessment = (c: (typeof DECISION_CASES)[number]) =>
  makeAssessment({ decision: c.decision, risk_level: c.level, risk_score: c.score, reasons: c.reasons })

function setup() {
  const user = userEvent.setup()
  render(<NewPaymentPage />)
  return { user }
}

const paymentPanel = () => screen.getByRole('region', { name: 'Payment' })

async function assess(user: User) {
  await user.type(screen.getByLabelText('Merchant name'), VALID_DETAILS.merchant_name)
  await user.type(screen.getByLabelText('Amount'), VALID_DETAILS.amount)
  await user.type(screen.getByLabelText('Description'), VALID_DETAILS.description)
  await user.type(screen.getByLabelText('Payment purpose'), VALID_DETAILS.purpose)
  await user.click(screen.getByRole('button', { name: 'Assess Risk' }))
  await within(screen.getByRole('region', { name: 'Risk assessment' })).findByText(/Policy decision/i)
}

function noCaptureOrVoid(api: ReturnType<typeof routeApi>) {
  expect(api.calls().some((c) => /\/(capture|void|approve|reject)$/.test(c))).toBe(false)
}

describe('New Payment: submitting the assessed payment', () => {
  it('shows no payment step before an assessment', () => {
    setup()
    expect(screen.queryByRole('region', { name: 'Payment' })).not.toBeInTheDocument()
  })

  it('ALLOW: creates the payment and shows the PayPal Sandbox approval link', async () => {
    const api = routeApi({ [ASSESS]: ok(assessment(ALLOW_CASE)), [PAY]: ok(actionIn('AWAITING_BUYER_APPROVAL'), 201) })
    const { user } = setup()
    await assess(user)

    expect(within(paymentPanel()).getByText(/authorize-only PayPal Sandbox order/)).toBeInTheDocument()
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submit payment' }))

    const link = await within(paymentPanel()).findByRole('link', { name: 'Approve this payment on PayPal Sandbox' })
    expect(link).toHaveAttribute('href', SANDBOX_APPROVAL_URL)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(within(paymentPanel()).getByText('Awaiting buyer approval')).toBeInTheDocument()
    expect(sentBody(api, 1)).toEqual(VALID_DETAILS)
    expect(api.calls()).toEqual([ASSESS, PAY])
    noCaptureOrVoid(api)
  })

  it('REVIEW: records the payment for review and creates no PayPal order', async () => {
    const api = routeApi({ [ASSESS]: ok(assessment(REVIEW_CASE)), [PAY]: ok(actionIn('PENDING_REVIEW'), 201) })
    const { user } = setup()
    await assess(user)

    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submit for review' }))

    expect(await within(paymentPanel()).findByText('Pending review')).toBeInTheDocument()
    expect(within(paymentPanel()).getByText(/Nothing is sent to PayPal until then/)).toBeInTheDocument()
    expect(within(paymentPanel()).getByText(/Reviewers approve, reject, capture or void/)).toBeInTheDocument()
    expect(within(paymentPanel()).queryByRole('link')).not.toBeInTheDocument()
    expect(within(paymentPanel()).queryByRole('button')).not.toBeInTheDocument()
    expect(api.calls()).toEqual([ASSESS, PAY])
  })

  it('BLOCK: records the blocked payment and offers no PayPal action', async () => {
    const api = routeApi({ [ASSESS]: ok(assessment(BLOCK_CASE)), [PAY]: ok(actionIn('BLOCKED'), 201) })
    const { user } = setup()
    await assess(user)

    expect(within(paymentPanel()).getByText(/it is never sent to PayPal/)).toBeInTheDocument()
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Record blocked payment' }))

    expect(await within(paymentPanel()).findByText('Blocked')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Risk assessment' })).getByText('BLOCK')).toBeInTheDocument()
    expect(within(paymentPanel()).queryByRole('button')).not.toBeInTheDocument()
    expect(within(paymentPanel()).queryByRole('link')).not.toBeInTheDocument()
    expect(api.calls()).toEqual([ASSESS, PAY])
  })

  it('shows backend risk from the risk assessment and only the status from the payment response', async () => {
    routeApi({ [ASSESS]: ok(assessment(REVIEW_CASE)), [PAY]: ok(actionIn('PENDING_REVIEW'), 201) })
    const { user } = setup()
    await assess(user)
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submit for review' }))

    const panel = paymentPanel()
    await within(panel).findByText('Pending review')
    // Backend risk is shown in the risk panel (from /risk/assess) ...
    const risk = screen.getByRole('region', { name: 'Risk assessment' })
    expect(within(risk).getByText(REVIEW_CASE.reasons[0])).toBeInTheDocument()
    // ... the payment panel (minimal action response) carries no risk or reviewer data.
    expect(within(panel).queryByText(REVIEW_CASE.reasons[0])).not.toBeInTheDocument()
    expect(within(panel).queryByText(/Risk score|Policy decision|Risk reasons/)).not.toBeInTheDocument()
  })

  it('blocks submitting when the form changed after the assessment', async () => {
    const api = routeApi({ [ASSESS]: ok(assessment(ALLOW_CASE)) })
    const { user } = setup()
    await assess(user)
    await user.type(screen.getByLabelText('Amount'), '9')

    expect(within(paymentPanel()).getByRole('status')).toHaveTextContent('Assess again')
    expect(within(paymentPanel()).getByRole('button', { name: 'Submit payment' })).toBeDisabled()
    expect(api.calls()).toEqual([ASSESS])
  })

  it('sends one create request for repeated clicks', async () => {
    const pending = deferredFetch()
    const { user } = setup()
    pending.fetch.mockImplementationOnce(async () => jsonResponse(200, assessment(ALLOW_CASE)))
    await assess(user)

    const button = within(paymentPanel()).getByRole('button', { name: 'Submit payment' })
    await user.click(button)
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submitting…' }))
    expect(within(paymentPanel()).getByRole('button', { name: 'Submitting…' })).toBeDisabled()
    expect(pending.fetch).toHaveBeenCalledTimes(2) // assess + one create

    await act(async () => pending.resolve(jsonResponse(201, actionIn('PENDING_REVIEW'))))
    expect(await within(paymentPanel()).findByText('Pending review')).toBeInTheDocument()
  })
})

describe('New Payment: buyer approval and authorization through the backend', () => {
  async function submitted(routes: Record<string, () => Response>) {
    const api = routeApi({ [ASSESS]: ok(assessment(ALLOW_CASE)), [PAY]: ok(actionIn('AWAITING_BUYER_APPROVAL'), 201), ...routes })
    const { user } = setup()
    await assess(user)
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submit payment' }))
    await within(paymentPanel()).findByText('Awaiting buyer approval')
    return { api, user }
  }

  it('authorizes via the backend and never captures automatically', async () => {
    const { api, user } = await submitted({ [AUTHORIZE]: ok(actionIn('AUTHORIZED')) })

    await user.click(within(paymentPanel()).getByRole('button', { name: 'Check PayPal approval & authorize' }))

    expect(await within(paymentPanel()).findByText('Authorized')).toBeInTheDocument()
    expect(within(paymentPanel()).getByText(/A reviewer must capture or void it/)).toBeInTheDocument()
    expect(within(paymentPanel()).queryByRole('button', { name: /capture|void/i })).not.toBeInTheDocument()
    expect(sentBody(api, 2)).toEqual({})
    expect(api.calls()).toEqual([ASSESS, PAY, AUTHORIZE])
    noCaptureOrVoid(api)
  })

  it('reports when PayPal has not seen buyer approval yet', async () => {
    const { api, user } = await submitted({
      [AUTHORIZE]: () => jsonResponse(409, { code: 'PAYMENT_BUYER_APPROVAL_REQUIRED', message: 'x',
        details: { transaction_id: TXN, paypal_order_status: 'PAYER_ACTION_REQUIRED' } }),
    })

    await user.click(within(paymentPanel()).getByRole('button', { name: 'Check PayPal approval & authorize' }))

    expect(await within(paymentPanel()).findByText(/does not report this order as approved/)).toBeInTheDocument()
    expect(within(paymentPanel()).getByText('Awaiting buyer approval')).toBeInTheDocument()
    // No authenticated re-read on this page (it has no reviewer token).
    expect(api.calls()).toEqual([ASSESS, PAY, AUTHORIZE])
  })

  it('keeps a pending authorization pending', async () => {
    const { user } = await submitted({ [AUTHORIZE]: ok(actionIn('AUTHORIZATION_PENDING')) })
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Check PayPal approval & authorize' }))
    expect(await within(paymentPanel()).findByText('Authorization pending')).toBeInTheDocument()
    expect(within(paymentPanel()).getByRole('button', { name: 'Refresh authorization status' })).toBeInTheDocument()
  })

  it('after an ambiguous PayPal failure, shows the saved payment from safe error details and retries', async () => {
    const api = routeApi({
      [ASSESS]: ok(assessment(ALLOW_CASE)),
      [PAY]: () => jsonResponse(504, { code: 'PAYPAL_TIMEOUT', message: 'x',
        details: { transaction_id: TXN, status: 'APPROVED_FOR_PAYMENT', retry_safe: true } }),
      [ORDER]: ok(actionIn('AWAITING_BUYER_APPROVAL')),
    })
    const { user } = setup()
    await assess(user)
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submit payment' }))

    expect(await within(paymentPanel()).findByText(TXN)).toBeInTheDocument()
    expect(within(paymentPanel()).getByText('Approved, order not created')).toBeInTheDocument()
    expect(within(paymentPanel()).getAllByText(/safe to try the same action again/).length).toBeGreaterThan(0)

    await user.click(within(paymentPanel()).getByRole('button', { name: 'Create PayPal order' }))
    expect(await within(paymentPanel()).findByText('Awaiting buyer approval')).toBeInTheDocument()
    // The record is never read back (that needs the reviewer token); the order retry returns it.
    expect(api.calls()).toEqual([ASSESS, PAY, ORDER])
    noCaptureOrVoid(api)
  })

  it('does not offer a retry when the saved payment is not retry-safe', async () => {
    const api = routeApi({
      [ASSESS]: ok(assessment(ALLOW_CASE)),
      [PAY]: () => jsonResponse(422, { code: 'PAYPAL_REQUEST_REJECTED', message: 'x',
        details: { transaction_id: TXN, status: 'FAILED', retry_safe: false } }),
    })
    const { user } = setup()
    await assess(user)
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submit payment' }))

    expect(await within(paymentPanel()).findByText(/A reviewer can check it on the Transactions page/)).toBeInTheDocument()
    expect(within(paymentPanel()).queryByRole('button', { name: 'Create PayPal order' })).not.toBeInTheDocument()
    expect(api.calls()).toEqual([ASSESS, PAY])
  })

  it('shows the INR restriction from the backend without converting', async () => {
    const api = routeApi({
      [ASSESS]: ok(assessment(ALLOW_CASE)),
      [PAY]: () => jsonResponse(422, { code: 'PAYPAL_CURRENCY_NOT_SUPPORTED', message: 'x' }),
    })
    const { user } = setup()
    await assess(user)
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submit payment' }))

    expect(await within(paymentPanel()).findByRole('alert')).toHaveTextContent(
      'cannot be sent to PayPal yet. Amounts are never converted',
    )
    expect(api.calls()).toEqual([ASSESS, PAY])
  })
})


describe('New Payment: buyer flow with minimal action responses', () => {
  it('runs create -> PayPal approval link -> authorize without a reviewer token', async () => {
    const api = routeApi({
      [ASSESS]: ok(assessment(ALLOW_CASE)),
      [PAY]: ok(actionIn('AWAITING_BUYER_APPROVAL'), 201),
      [AUTHORIZE]: ok(actionIn('AUTHORIZED')),
    })
    const { user } = setup()
    await assess(user)
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submit payment' }))

    const link = await within(paymentPanel()).findByRole('link', { name: 'Approve this payment on PayPal Sandbox' })
    expect(link).toHaveAttribute('href', SANDBOX_APPROVAL_URL)
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Check PayPal approval & authorize' }))

    expect(await within(paymentPanel()).findByText('Authorized')).toBeInTheDocument()
    expect(within(paymentPanel()).queryByRole('link')).not.toBeInTheDocument()
    expect(api.calls()).toEqual([ASSESS, PAY, AUTHORIZE])
    for (let i = 0; i < api.mock.calls.length; i++) {
      expect(api.headersOf(i).has('authorization')).toBe(false)  // buyer flow never sends a token
    }
    noCaptureOrVoid(api)
  })

  it('rejects an action response that contains reviewer or risk data', async () => {
    const leaky = { ...actionIn('AUTHORIZED'), review: { reviewer_name: 'Asha', comment: 'internal' } }
    routeApi({
      [ASSESS]: ok(assessment(ALLOW_CASE)),
      [PAY]: ok(actionIn('AWAITING_BUYER_APPROVAL'), 201),
      [AUTHORIZE]: ok(leaky),
    })
    const { user } = setup()
    await assess(user)
    await user.click(within(paymentPanel()).getByRole('button', { name: 'Submit payment' }))
    await user.click(await within(paymentPanel()).findByRole('button', { name: 'Check PayPal approval & authorize' }))

    expect(await within(paymentPanel()).findByRole('alert')).toHaveTextContent('unexpected response')
    expect(document.body.textContent).not.toContain('internal')
    expect(within(paymentPanel()).getByText('Awaiting buyer approval')).toBeInTheDocument()
  })
})
