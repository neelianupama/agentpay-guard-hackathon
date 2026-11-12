import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import {
  TXN,
  actionIn,
  jsonResponse,
  ok,
  paymentIn,
  paymentList,
  routeApi,
} from '../test/http'
import { REVIEWER_TOKEN, enterReviewerToken, renderWithReviewer } from '../test/reviewer'
import { TRANSACTIONS_PAGE_SIZE, TransactionsPage } from './TransactionsPage'

const LIST = 'GET /api/v1/payments'
const GET = `GET /api/v1/payments/${TXN}`
const AUTHORIZE = `POST /api/v1/payments/${TXN}/authorize`

const detailPanel = () => screen.getByRole('region', { name: 'Payment details' })
/** The payments grid, once AG Grid has rendered its headers and data cells. */
async function findGrid(): Promise<HTMLElement> {
  const region = await screen.findByRole('region', { name: 'Payments table' })
  return waitFor(() => {
    const grid = within(region).getByRole('grid')
    expect(grid.querySelector('[role="gridcell"]')).not.toBeNull()
    expect(within(grid).getAllByText('Amount').length).toBeGreaterThan(0)
    return grid
  })
}
const columnHeaders = (grid: HTMLElement) =>
  within(grid).getAllByRole('columnheader').filter((h) => !h.querySelector('input'))
    .map((h) => (h.textContent ?? '').trim().replace(/\s+\d+$/, ''))  // drop sort-order index
/** Data rows only (AG Grid also renders header and floating-filter rows). */
const dataRows = (grid: HTMLElement) =>
  within(grid).getAllByRole('row').filter((r) => !r.closest('.ag-header') && r.querySelector('[role="gridcell"]'))
const merchantsInOrder = (grid: HTMLElement) =>
  dataRows(grid)
    .sort((a, b) => Number(a.getAttribute('aria-rowindex')) - Number(b.getAttribute('aria-rowindex')))
    .map((r) => r.querySelector('[col-id="merchant_name"]')?.textContent ?? '')

/** Render with the in-memory token provider and enter the reviewer token via the UI. */
async function setup({ strict = false } = {}) {
  const user = userEvent.setup()
  renderWithReviewer(<TransactionsPage />, { strict })
  await enterReviewerToken(user)
  return { user }
}

describe('Transactions page: reviewer token', () => {
  it('requests nothing and asks for the token until one is entered', async () => {
    const api = routeApi({ [LIST]: ok(paymentList([])) })
    renderWithReviewer(<TransactionsPage />)
    expect(screen.getByLabelText('Reviewer token')).toHaveAttribute('type', 'password')
    expect(screen.getByRole('button', { name: 'Use token' })).toBeDisabled()
    expect(screen.queryByRole('region', { name: 'Payments' })).not.toBeInTheDocument()
    expect(api).not.toHaveBeenCalled()
  })

  it('sends the token only in the Authorization header and never shows or stores it', async () => {
    const api = routeApi({ [LIST]: ok(paymentList([paymentIn('CAPTURED')])), [GET]: ok(paymentIn('CAPTURED')) })
    const { user } = await setup()
    await user.click(await screen.findByRole('button', { name: /View payment to Acme Cloud/ }))
    await within(detailPanel()).findByText('Captured')

    for (const index of [0, 1]) {
      const [url, init] = api.mock.calls[index]
      expect(api.headersOf(index).get('authorization')).toBe(`Bearer ${REVIEWER_TOKEN}`)
      expect(String(url)).not.toContain(REVIEWER_TOKEN)
      expect(init.body).toBeUndefined()
    }
    expect(document.body.textContent).not.toContain(REVIEWER_TOKEN)
    expect(localStorage.length + sessionStorage.length).toBe(0)
    expect(document.cookie).not.toContain(REVIEWER_TOKEN)
  })

  it('shows a rejected token as a safe error and allows clearing it', async () => {
    routeApi({ [LIST]: () => jsonResponse(401, { code: 'REVIEWER_AUTH_INVALID', message: 'x' }) })
    const { user } = await setup()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('reviewer token was rejected')
    expect(alert.textContent).not.toContain(REVIEWER_TOKEN)

    await user.click(screen.getByRole('button', { name: 'Clear token' }))
    expect(screen.getByLabelText('Reviewer token')).toHaveValue('')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('Transactions page', () => {
  it('loads under React StrictMode, as in the real app (regression)', async () => {
    routeApi({ [LIST]: ok(paymentList([])) })
    await setup({ strict: true })
    expect(await screen.findByText('No payments yet')).toBeInTheDocument()
    expect(screen.queryByText('Loading payments…')).not.toBeInTheDocument()
  })

  it('shows a loading state, then the payments in an AG Grid', async () => {
    const items = [paymentIn('CAPTURED'), paymentIn('BLOCKED', { transaction_id: 'txn_blocked', merchant_name: 'Shady Ltd' })]
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => { release = resolve })
    routeApi({ [LIST]: async () => { await gate; return jsonResponse(200, paymentList(items)) } })
    await setup()

    expect(screen.getByText('Loading payments…')).toBeInTheDocument()
    release()
    const grid = await findGrid()
    expect(columnHeaders(grid)).toEqual(
      ['', 'Transaction ID', 'Merchant', 'Amount', 'Currency', 'Status', 'Decision', 'Created'])
    const rows = dataRows(grid)
    expect(rows).toHaveLength(2)
    const captured = rows.find((r) => r.textContent?.includes('Acme Cloud'))!
    expect(captured).toHaveTextContent(TXN)
    expect(captured).toHaveTextContent('49.99')
    expect(captured).toHaveTextContent('USD')
    expect(captured).toHaveTextContent('Captured')
    expect(captured).toHaveTextContent('ALLOW')
    expect(rows.find((r) => r.textContent?.includes('Shady Ltd'))).toHaveTextContent('Blocked')
  })

  it('shows an empty state', async () => {
    routeApi({ [LIST]: ok(paymentList([])) })
    await setup()
    expect(await screen.findByText('No payments yet')).toBeInTheDocument()
  })

  it('shows an error state with a safe message', async () => {
    routeApi({ [LIST]: () => jsonResponse(500, { detail: 'Internal Server Error' }) })
    await setup()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load payments')
  })

  it('loads the newest 100 records once and paginates them in the grid', async () => {
    const items = Array.from({ length: 25 }, (_, i) =>
      paymentIn('CAPTURED', { transaction_id: `txn_page_${String(i).padStart(2, '0')}`,
        merchant_name: `Merchant ${String(i).padStart(2, '0')}`,
        created_at: new Date(Date.UTC(2026, 10, 12, 6, i)).toISOString() }))
    const api = routeApi({ [LIST]: ok(paymentList(items, 25, 100, 0)) })
    const { user } = await setup()
    const grid = await findGrid()

    expect(dataRows(grid)).toHaveLength(TRANSACTIONS_PAGE_SIZE)
    expect(screen.getByLabelText('Page number, 1 of 3')).toBeInTheDocument()
    await user.click(screen.getByLabelText('Next Page'))
    expect(await screen.findByLabelText('Page number, 2 of 3')).toBeInTheDocument()

    const url = new URL(String(api.mock.calls[0][0]))
    expect([url.searchParams.get('limit'), url.searchParams.get('offset')]).toEqual(['100', '0'])
    expect(api.calls()).toEqual([LIST])  // paging is client-side: no extra requests
  })

  it('notes when only the newest 100 of more payments are shown', async () => {
    routeApi({ [LIST]: ok(paymentList([paymentIn('CAPTURED')], 140, 100, 0)) })
    await setup()
    expect(await screen.findByRole('note')).toHaveTextContent('Showing the newest 1 of 140 payments.')
  })

  it('sorts by amount and filters by merchant with Community grid features', async () => {
    const items = [
      paymentIn('CAPTURED', { transaction_id: 'txn_mid', merchant_name: 'Beta Ltd', amount: '250.00' }),
      paymentIn('CAPTURED', { transaction_id: 'txn_low', merchant_name: 'Alpha Inc', amount: '9.99' }),
      paymentIn('BLOCKED', { transaction_id: 'txn_high', merchant_name: 'Gamma plc', amount: '3000.00' }),
    ]
    routeApi({ [LIST]: ok(paymentList(items)) })
    const { user } = await setup()
    const grid = await findGrid()

    await user.click(within(grid).getByText('Amount'))
    expect(merchantsInOrder(grid)).toEqual(['Alpha Inc', 'Beta Ltd', 'Gamma plc'])
    await user.click(within(grid).getByText('Amount'))
    expect(merchantsInOrder(grid)).toEqual(['Gamma plc', 'Beta Ltd', 'Alpha Inc'])

    await user.type(screen.getByLabelText('Merchant Filter Input'), 'beta')
    await waitFor(() => expect(merchantsInOrder(grid)).toEqual(['Beta Ltd']))
  })

  it('opens payment details from the detail endpoint', async () => {
    const payment = paymentIn('AUTHORIZED')
    const api = routeApi({ [LIST]: ok(paymentList([payment])), [GET]: ok(payment) })
    const { user } = await setup()

    await user.click(await screen.findByRole('button', { name: /View payment to Acme Cloud/ }))

    const panel = detailPanel()
    expect(await within(panel).findByText('Authorized')).toBeInTheDocument()
    expect(within(panel).getByText('AUT00000000000002')).toBeInTheDocument()
    expect(within(panel).getByText(payment.risk.reasons[0])).toBeInTheDocument()
    // Reviewer-only actions are not offered here.
    expect(within(panel).queryByRole('button', { name: /capture|void|approve|reject/i })).not.toBeInTheDocument()
    expect(within(panel).getByText(/Reviewers approve, reject, capture or void/)).toBeInTheDocument()
    expect(api.calls()).toEqual([LIST, GET])
  })

  it('authorizes a buyer-approved payment through the backend from the detail view', async () => {
    let authorized = false
    const api = routeApi({
      [LIST]: ok(paymentList([paymentIn('AWAITING_BUYER_APPROVAL')])),
      // Reviewer reads return the full record; it changes once the buyer action succeeded.
      [GET]: () => jsonResponse(200, paymentIn(authorized ? 'AUTHORIZED' : 'AWAITING_BUYER_APPROVAL')),
      [AUTHORIZE]: () => { authorized = true; return jsonResponse(200, actionIn('AUTHORIZED')) },
    })
    const { user } = await setup()
    await user.click(await screen.findByRole('button', { name: /View payment to Acme Cloud/ }))
    await user.click(await within(detailPanel()).findByRole('button', { name: 'Check PayPal approval & authorize' }))

    expect(await within(detailPanel()).findByText('Authorized')).toBeInTheDocument()
    // The full reviewer view (authorization ID) comes from the authenticated re-read.
    expect(within(detailPanel()).getByText('AUT00000000000002')).toBeInTheDocument()
    expect(api.calls()).toEqual([LIST, GET, AUTHORIZE, GET, LIST])
    expect(api.headersOf(2).has('authorization')).toBe(false)  // the buyer action itself has no token
    expect(api.headersOf(3).get('authorization')).toBe(`Bearer ${REVIEWER_TOKEN}`)
  })

  it('never shows an approval link for a payment that is not awaiting buyer approval', async () => {
    const payment = paymentIn('CAPTURED', { paypal: { ...paymentIn('CAPTURED').paypal,
      approval_url: 'https://www.sandbox.paypal.com/checkoutnow?token=OLD' } })
    routeApi({ [LIST]: ok(paymentList([payment])), [GET]: ok(payment) })
    const { user } = await setup()
    await user.click(await screen.findByRole('button', { name: /View payment to Acme Cloud/ }))
    expect(await within(detailPanel()).findByText('Captured')).toBeInTheDocument()
    expect(within(detailPanel()).queryByRole('link')).not.toBeInTheDocument()
  })

  it('reports a payment lost after a backend restart', async () => {
    routeApi({
      [LIST]: ok(paymentList([paymentIn('CAPTURED')])),
      [GET]: () => jsonResponse(404, { code: 'PAYMENT_NOT_FOUND', message: 'x' }),
    })
    const { user } = await setup()
    await user.click(await screen.findByRole('button', { name: /View payment to Acme Cloud/ }))
    expect(await within(detailPanel()).findByRole('alert')).toHaveTextContent('lost when the backend restarted')
  })
})
