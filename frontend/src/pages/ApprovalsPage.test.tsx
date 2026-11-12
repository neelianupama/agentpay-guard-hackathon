/**
 * Approvals page: reviewer actions with the token sent only as a Bearer header.
 * Only fetch is mocked (routeApi).
 */
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Payment } from '../api/payments'
import {
  TXN,
  jsonResponse,
  ok,
  paymentIn,
  paymentList,
  routeApi,
  sentBody,
} from '../test/http'
import { REVIEWER_TOKEN, enterReviewerToken, renderWithReviewer } from '../test/reviewer'
import { ApprovalsPage } from './ApprovalsPage'

const TOKEN = REVIEWER_TOKEN
const LIST = 'GET /api/v1/payments'
const route = (action: string) => `POST /api/v1/payments/${TXN}/${action}`
const GET = `GET /api/v1/payments/${TXN}`
/** The per-card outcome line (the status description repeats some of the same text). */
const OUTCOME = 'p.approval-card__outcome'

type User = ReturnType<typeof userEvent.setup>

function setup({ strict = false } = {}) {
  const user = userEvent.setup()
  const view = renderWithReviewer(<ApprovalsPage />, { strict })
  return { user, view }
}

/** Enter the token through the real UI; the queue loads only after this. */
async function enterToken(user: User, token = TOKEN) {
  await enterReviewerToken(user, token)
}

function withList(items: Payment[], routes: Record<string, () => Response> = {}) {
  return routeApi({ [LIST]: ok(paymentList(items, items.length, 100)), ...routes })
}

const card = async (merchant = 'Acme Cloud') =>
  (await screen.findByRole('article', { name: new RegExp(merchant) })) as HTMLElement

describe('Approvals: queue', () => {
  it('loads under React StrictMode, as in the real app (regression)', async () => {
    withList([])
    const { user } = setup({ strict: true })
    await enterToken(user)
    expect(await screen.findByText('Nothing to review')).toBeInTheDocument()
    expect(screen.queryByText('Loading payments…')).not.toBeInTheDocument()
  })

  it('lists only payments that need a reviewer', async () => {
    const api = withList([
      paymentIn('PENDING_REVIEW'),
      paymentIn('AUTHORIZED', { transaction_id: 'txn_auth', merchant_name: 'Authorized Co' }),
      paymentIn('CAPTURED', { transaction_id: 'txn_done', merchant_name: 'Done Co' }),
      paymentIn('BLOCKED', { transaction_id: 'txn_block', merchant_name: 'Blocked Co' }),
    ])
    const { user } = setup()
    await enterToken(user)
    expect(await screen.findByRole('heading', { name: 'Awaiting review' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Authorized payments' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: /Acme Cloud/ })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: /Authorized Co/ })).toBeInTheDocument()
    expect(screen.queryByText(/Done Co/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Blocked Co/)).not.toBeInTheDocument()
    const url = new URL(String(api.mock.calls[0][0]))
    expect(url.searchParams.get('limit')).toBe('100')
  })

  it('shows an empty state and an error state', async () => {
    withList([])
    const first = setup()
    await enterToken(first.user)
    expect(await screen.findByText('Nothing to review')).toBeInTheDocument()
    first.view.unmount()

    routeApi({ [LIST]: () => jsonResponse(503, { detail: 'down' }) })
    const second = setup()
    await enterToken(second.user)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load payments')
  })
})

describe('Approvals: reviewer token handling', () => {
  it('uses a password field and loads nothing until a token is set', async () => {
    const api = withList([paymentIn('PENDING_REVIEW')])
    const { user } = setup()
    const input = screen.getByLabelText('Reviewer token')
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveAttribute('autocomplete', 'off')
    expect(screen.getByRole('button', { name: 'Use token' })).toBeDisabled()
    expect(api).not.toHaveBeenCalled()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()

    await enterToken(user)
    expect(await within(await card()).findByRole('button', { name: 'Approve' })).toBeEnabled()
    expect(api.headersOf(0).get('authorization')).toBe(`Bearer ${TOKEN}`)

    await user.click(screen.getByRole('button', { name: 'Clear token' }))
    expect(screen.getByLabelText('Reviewer token')).toHaveValue('')
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
  })

  it('shows a rejected token when loading the queue', async () => {
    routeApi({ [LIST]: () => jsonResponse(401, { code: 'REVIEWER_AUTH_INVALID', message: 'x' }) })
    const { user } = setup()
    await enterToken(user, 'wrong-token')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('reviewer token was rejected')
    expect(alert.textContent).not.toContain('wrong-token')
  })

  it('sends the token only in the Authorization header and never stores or shows it', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const log = vi.spyOn(console, 'log')
    const api = withList([paymentIn('PENDING_REVIEW')], { [route('approve')]: ok(paymentIn('AWAITING_BUYER_APPROVAL')) })
    const { user, view } = setup()
    await enterToken(user)
    await user.type(screen.getByLabelText('Your name (optional)'), 'Asha')
    await user.type(within(await card()).getByLabelText('Comment (optional)'), 'Invoice checked')

    await user.click(within(await card()).getByRole('button', { name: 'Approve' }))
    await screen.findByText(/A PayPal order was created/)

    const index = api.calls().indexOf(route('approve'))
    const [url, init] = api.mock.calls[index]
    expect(api.headersOf(index).get('authorization')).toBe(`Bearer ${TOKEN}`)
    expect(String(url)).not.toContain(TOKEN)
    expect(String(init.body)).not.toContain(TOKEN)
    expect(sentBody(api, index)).toEqual({ reviewer_name: 'Asha', comment: 'Invoice checked' })
    expect(setItem).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    expect(log.mock.calls.flat().join(' ')).not.toContain(TOKEN)
    expect(view.container.textContent).not.toContain(TOKEN)
    expect(document.location.href).not.toContain(TOKEN)
  })

  it('reports a rejected token without performing the action', async () => {
    withList([paymentIn('PENDING_REVIEW')], {
      [route('approve')]: () => jsonResponse(401, { code: 'REVIEWER_AUTH_INVALID', message: 'x' }),
    })
    const { user } = setup()
    await enterToken(user, 'wrong-token')
    await user.click(within(await card()).getByRole('button', { name: 'Approve' }))
    expect(await within(await card()).findByRole('alert')).toHaveTextContent('reviewer token was rejected')
    expect(within(await card()).getByText('Pending review')).toBeInTheDocument()
  })

  it('reports missing reviewer configuration on the server', async () => {
    withList([paymentIn('PENDING_REVIEW')], {
      [route('reject')]: () => jsonResponse(503, { code: 'REVIEWER_AUTH_NOT_CONFIGURED', message: 'x' }),
    })
    const { user } = setup()
    await enterToken(user)
    await user.click(within(await card()).getByRole('button', { name: 'Reject' }))
    expect(await within(await card()).findByRole('alert')).toHaveTextContent('not configured on the server')
  })
})

describe('Approvals: review decisions', () => {
  it('approves: the backend creates the PayPal order; nothing is captured', async () => {
    const api = withList([paymentIn('PENDING_REVIEW')], { [route('approve')]: ok(paymentIn('AWAITING_BUYER_APPROVAL')) })
    const { user } = setup()
    await enterToken(user)
    await user.click(within(await card()).getByRole('button', { name: 'Approve' }))

    expect(await screen.findByText('Approved. A PayPal order was created; the buyer must now approve it on PayPal.')).toBeInTheDocument()
    expect(within(await card()).getByText('Awaiting buyer approval')).toBeInTheDocument()
    expect(api.calls()).toEqual([LIST, route('approve')])
  })

  it('rejects without contacting PayPal', async () => {
    const api = withList([paymentIn('PENDING_REVIEW')], {
      [route('reject')]: ok(paymentIn('REJECTED', { review: {
        decision: 'REJECT', reviewer_name: null, comment: 'Unknown vendor', decided_at: '2026-11-12T07:00:00Z' } })),
    })
    const { user } = setup()
    await enterToken(user)
    await user.click(within(await card()).getByRole('button', { name: 'Reject' }))

    expect(await screen.findByText('Payment rejected. Nothing was sent to PayPal.')).toBeInTheDocument()
    expect(within(await card()).getByText('Rejected')).toBeInTheDocument()
    expect(within(await card()).queryByRole('button', { name: /Approve|Reject/ })).not.toBeInTheDocument()
    expect(api.calls()).toEqual([LIST, route('reject')])
  })
})

describe('Approvals: capture and void', () => {
  it('capture requires an explicit confirmation showing the amount', async () => {
    const api = withList([paymentIn('AUTHORIZED')], { [route('capture')]: ok(paymentIn('CAPTURED')) })
    const { user } = setup()
    await enterToken(user)

    await user.click(within(await card()).getByRole('button', { name: 'Capture' }))
    expect(api.calls()).toEqual([LIST]) // first click only asks for confirmation
    const confirm = within(await card()).getByRole('group', { name: 'Confirm capture' })
    expect(confirm).toHaveTextContent('Capture 49.99 USD from the PayPal authorization?')

    await user.click(within(confirm).getByRole('button', { name: 'Confirm capture of 49.99 USD' }))

    expect(await screen.findByText('PayPal confirmed the capture.', { selector: OUTCOME })).toBeInTheDocument()
    expect(within(await card()).getByText('Captured')).toBeInTheDocument()
    expect(within(await card()).queryByRole('button', { name: /Capture|Void/ })).not.toBeInTheDocument()
    expect(api.calls()).toEqual([LIST, route('capture')])
  })

  it('cancelling the confirmation sends nothing', async () => {
    const api = withList([paymentIn('AUTHORIZED')])
    const { user } = setup()
    await enterToken(user)
    await user.click(within(await card()).getByRole('button', { name: 'Void' }))
    await user.click(within(await card()).getByRole('button', { name: 'Cancel' }))
    expect(within(await card()).getByRole('button', { name: 'Capture' })).toBeInTheDocument()
    expect(api.calls()).toEqual([LIST])
  })

  it('a PENDING capture is not shown as captured', async () => {
    withList([paymentIn('AUTHORIZED')], { [route('capture')]: ok(paymentIn('CAPTURE_PENDING')) })
    const { user } = setup()
    await enterToken(user)
    await user.click(within(await card()).getByRole('button', { name: 'Capture' }))
    await user.click(within(await card()).getByRole('button', { name: /Confirm capture/ }))

    expect(await screen.findByText(/reports it as pending\. Funds are not confirmed as captured/, { selector: OUTCOME })).toBeInTheDocument()
    expect(within(await card()).getByText('Capture pending at PayPal')).toBeInTheDocument()
    expect(screen.queryByText('PayPal confirmed the capture.')).not.toBeInTheDocument()
    expect(screen.queryByText('Captured')).not.toBeInTheDocument()
  })

  it('an unconfirmed capture shows the stored state and offers a safe retry', async () => {
    const pendingCapture = paymentIn('PENDING_CAPTURE', {
      last_error: { code: 'PAYPAL_TIMEOUT', message: 'x', retry_safe: true, at: '2026-11-12T07:00:00Z' },
    })
    const api = withList([paymentIn('AUTHORIZED')], {
      [route('capture')]: () => jsonResponse(504, { code: 'PAYPAL_TIMEOUT', message: 'x',
        details: { transaction_id: TXN, status: 'PENDING_CAPTURE', retry_safe: true } }),
      [GET]: ok(pendingCapture),
    })
    const { user } = setup()
    await enterToken(user)
    await user.click(within(await card()).getByRole('button', { name: 'Capture' }))
    await user.click(within(await card()).getByRole('button', { name: /Confirm capture/ }))

    expect(await within(await card()).findByText('Capture not confirmed')).toBeInTheDocument()
    expect(within(await card()).getByText('Reviewer action failed')).toBeInTheDocument()
    expect(within(await card()).getByRole('button', { name: 'Retry capture' })).toBeInTheDocument()
    expect(within(await card()).queryByRole('button', { name: 'Void' })).not.toBeInTheDocument()
    expect(screen.queryByText('PayPal confirmed the capture.')).not.toBeInTheDocument()
    expect(api.calls()).toEqual([LIST, route('capture'), GET])
    expect(api.headersOf(2).get('authorization')).toBe(`Bearer ${TOKEN}`)
  })

  it('voids an authorized payment after confirmation', async () => {
    const api = withList([paymentIn('AUTHORIZED')], { [route('void')]: ok(paymentIn('VOIDED')) })
    const { user } = setup()
    await enterToken(user)
    await user.click(within(await card()).getByRole('button', { name: 'Void' }))
    expect(within(await card()).getByRole('group', { name: 'Confirm void' })).toHaveTextContent(
      'Void the PayPal authorization for 49.99 USD?')
    await user.click(within(await card()).getByRole('button', { name: 'Confirm void of 49.99 USD' }))

    expect(await screen.findByText('The authorization was voided. No funds were captured.', { selector: OUTCOME })).toBeInTheDocument()
    expect(api.calls()).toEqual([LIST, route('void')])
  })

  it('shows backend refusal of an invalid transition', async () => {
    withList([paymentIn('AUTHORIZED')], {
      [route('void')]: () => jsonResponse(409, { code: 'PAYMENT_INVALID_STATE', message: 'x',
        details: { transaction_id: TXN, status: 'CAPTURED' } }),
      [GET]: ok(paymentIn('CAPTURED')),
    })
    const { user } = setup()
    await enterToken(user)
    await user.click(within(await card()).getByRole('button', { name: 'Void' }))
    await user.click(within(await card()).getByRole('button', { name: /Confirm void/ }))
    expect(await within(await card()).findByRole('alert')).toHaveTextContent('(Current status: CAPTURED.)')
  })

  it('never captures automatically after approval or on load', async () => {
    const api = withList([paymentIn('PENDING_REVIEW'), paymentIn('AUTHORIZED', { transaction_id: 'txn_auth', merchant_name: 'Authorized Co' })], {
      [route('approve')]: ok(paymentIn('AWAITING_BUYER_APPROVAL')),
    })
    const { user } = setup()
    await enterToken(user)
    await user.click(within(await card()).getByRole('button', { name: 'Approve' }))
    await screen.findByText(/A PayPal order was created/)
    expect(api.calls().filter((c) => c.endsWith('/capture') || c.endsWith('/void'))).toEqual([])
  })
})
