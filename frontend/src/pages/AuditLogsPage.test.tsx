/**
 * Audit Logs page: AG Grid Community over GET /api/v1/audit/events (reviewer token
 * required). Only fetch is mocked.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { AuditSearchResult } from '../api/audit'
import { elasticResult, fallbackResult, makeAuditEvent } from '../test/audit'
import { jsonResponse, ok, routeApi } from '../test/http'
import { REVIEWER_TOKEN, enterReviewerToken, renderWithReviewer } from '../test/reviewer'
import { AUDIT_PAGE_SIZE, AuditLogsPage } from './AuditLogsPage'

const ROUTE = 'GET /api/v1/audit/events'
const TXN = 'txn_0123456789abcdef01234567'

async function setup({ strict = false, token = true } = {}) {
  const user = userEvent.setup()
  renderWithReviewer(<AuditLogsPage />, { strict })
  if (token) await enterReviewerToken(user)
  return { user }
}

async function findGrid(): Promise<HTMLElement> {
  const region = await screen.findByRole('region', { name: 'Audit events table' })
  return waitFor(() => {
    const grid = within(region).getByRole('grid')
    expect(grid.querySelector('[role="gridcell"]')).not.toBeNull()
    return grid
  })
}

const dataRows = (grid: HTMLElement) =>
  within(grid).getAllByRole('row').filter((r) => !r.closest('.ag-header') && r.querySelector('[role="gridcell"]'))
const storage = () => screen.getByRole('status', { name: 'Audit storage' })
const params = (api: ReturnType<typeof routeApi>, i: number) =>
  Object.fromEntries(new URL(String(api.mock.calls[i][0])).searchParams)

const EVENTS = [
  makeAuditEvent({ event_type: 'PAYPAL_CAPTURE', status: 'CAPTURED', previous_status: 'PENDING_CAPTURE',
    operation: 'CAPTURE', outcome: 'CAPTURED', reviewer_name: 'Asha', paypal_status: 'COMPLETED',
    decision: 'ALLOW', risk_score: 0, risk_level: 'LOW', amount: '49.99', reasons: [] }),
  makeAuditEvent({ event_type: 'PAYPAL_OPERATION_FAILED', operation: 'AUTHORIZE', outcome: 'FAILED',
    error_code: 'PAYPAL_TIMEOUT', retry_safe: true, status: 'BUYER_APPROVED' }),
  makeAuditEvent({ event_type: 'RISK_ASSESSED', transaction_id: null, status: null, decision: 'BLOCK',
    risk_score: 80, risk_level: 'CRITICAL' }),
]

describe('Audit Logs: reviewer authentication', () => {
  it('requests nothing until the reviewer token is entered', async () => {
    const api = routeApi({ [ROUTE]: ok(elasticResult(EVENTS)) })
    await setup({ token: false })
    expect(screen.getByLabelText('Reviewer token')).toHaveAttribute('type', 'password')
    expect(screen.queryByRole('region', { name: 'Audit events table' })).not.toBeInTheDocument()
    expect(api).not.toHaveBeenCalled()
  })

  it('sends the token only as a Bearer header and never shows or stores it', async () => {
    const api = routeApi({ [ROUTE]: ok(elasticResult(EVENTS)) })
    await setup()
    await findGrid()
    expect(api.headersOf(0).get('authorization')).toBe(`Bearer ${REVIEWER_TOKEN}`)
    expect(String(api.mock.calls[0][0])).not.toContain(REVIEWER_TOKEN)
    expect(document.body.textContent).not.toContain(REVIEWER_TOKEN)
    expect(localStorage.length + sessionStorage.length).toBe(0)
  })

  it('shows a rejected token safely', async () => {
    routeApi({ [ROUTE]: () => jsonResponse(401, { code: 'REVIEWER_AUTH_INVALID', message: 'x' }) })
    await setup()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load audit events')
    expect(alert).toHaveTextContent('reviewer token was rejected')
    expect(alert.textContent).not.toContain(REVIEWER_TOKEN)
  })
})

describe('Audit Logs: grid', () => {
  it('shows loading, then events in an AG Grid with safe details', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => { release = resolve })
    routeApi({ [ROUTE]: async () => { await gate; return jsonResponse(200, elasticResult(EVENTS)) } })
    await setup()
    expect(screen.getByText('Loading audit events…')).toBeInTheDocument()
    release()

    const grid = await findGrid()
    const headers = within(grid).getAllByRole('columnheader').filter((h) => !h.querySelector('input'))
      .map((h) => (h.textContent ?? '').trim().replace(/\s+\d+$/, ''))
    expect(headers).toEqual(['Time', 'Event', 'Transaction', 'Status', 'Action', 'Details'])
    const rows = dataRows(grid)
    expect(rows).toHaveLength(3)
    const capture = rows.find((r) => r.textContent?.includes('PayPal capture'))!
    expect(capture).toHaveTextContent('PENDING_CAPTURE → CAPTURED')
    expect(capture).toHaveTextContent('CAPTURE · CAPTURED')
    expect(capture).toHaveTextContent('by Asha')
    expect(capture).toHaveTextContent('PayPal: COMPLETED')
    expect(rows.find((r) => r.textContent?.includes('PayPal operation failed'))).toHaveTextContent('PAYPAL_TIMEOUT (retry safe)')
    expect(rows.find((r) => r.textContent?.includes('Risk assessed'))).toHaveTextContent('BLOCK (score 80, CRITICAL)')
  })

  it('shows an empty state', async () => {
    routeApi({ [ROUTE]: ok(elasticResult([])) })
    await setup()
    expect(await screen.findByText('No audit events')).toBeInTheDocument()
    expect(storage()).toHaveTextContent('Durable: stored in Elastic')
  })

  it('filters inside the grid with Community floating filters', async () => {
    const user = userEvent.setup()
    routeApi({ [ROUTE]: ok(elasticResult(EVENTS)) })
    renderWithReviewer(<AuditLogsPage />)
    await enterReviewerToken(user)
    const grid = await findGrid()
    await user.type(screen.getByLabelText('Event Filter Input'), 'capture')
    await waitFor(() => expect(dataRows(grid)).toHaveLength(1))
    expect(dataRows(grid)[0]).toHaveTextContent('PayPal capture')
  })
})

describe('Audit Logs: search and filters via the API', () => {
  it('applies search, transaction ID and event type as query parameters', async () => {
    const api = routeApi({ [ROUTE]: ok(elasticResult(EVENTS)) })
    const { user } = await setup()
    await findGrid()

    await user.type(screen.getByLabelText('Search'), 'timeout')
    await user.type(screen.getByLabelText('Transaction ID'), TXN)
    await user.selectOptions(screen.getByLabelText('Event type'), 'PAYPAL_OPERATION_FAILED')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => expect(api).toHaveBeenCalledTimes(2))
    expect(params(api, 0)).toEqual({ limit: String(AUDIT_PAGE_SIZE), offset: '0' })
    expect(params(api, 1)).toEqual({ q: 'timeout', transaction_id: TXN,
      event_type: 'PAYPAL_OPERATION_FAILED', limit: String(AUDIT_PAGE_SIZE), offset: '0' })

    await user.click(screen.getByRole('button', { name: 'Reset' }))
    await waitFor(() => expect(api).toHaveBeenCalledTimes(3))
    expect(params(api, 2)).toEqual({ limit: String(AUDIT_PAGE_SIZE), offset: '0' })
    expect(screen.getByLabelText('Search')).toHaveValue('')
  })

  it('rejects an invalid transaction ID without a request', async () => {
    const api = routeApi({ [ROUTE]: ok(elasticResult(EVENTS)) })
    const { user } = await setup()
    await findGrid()
    await user.type(screen.getByLabelText('Transaction ID'), 'txn 1/../x')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(screen.getByLabelText('Transaction ID')).toHaveAccessibleDescription(/only letters, digits/)
    expect(api).toHaveBeenCalledTimes(1)
  })

  it('pages through results with server-side offset', async () => {
    const api = routeApi({
      [ROUTE]: (_init, url) => {
        const offset = Number(url.searchParams.get('offset'))
        const page = Array.from({ length: offset === 0 ? AUDIT_PAGE_SIZE : 5 }, () => makeAuditEvent())
        return jsonResponse(200, elasticResult(page, { total: AUDIT_PAGE_SIZE + 5, offset }))
      },
    })
    const { user } = await setup()
    await findGrid()
    expect(screen.getByText(`Showing 1–${AUDIT_PAGE_SIZE} of ${AUDIT_PAGE_SIZE + 5}`)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText(`Showing ${AUDIT_PAGE_SIZE + 1}–${AUDIT_PAGE_SIZE + 5} of ${AUDIT_PAGE_SIZE + 5}`)).toBeInTheDocument()
    expect(params(api, 1).offset).toBe(String(AUDIT_PAGE_SIZE))
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })
})

describe('Audit Logs: storage state', () => {
  async function storageFor(result: AuditSearchResult) {
    routeApi({ [ROUTE]: ok(result) })
    await setup()
    return waitFor(() => storage())
  }

  it('Elastic, durable', async () => {
    const box = await storageFor(elasticResult(EVENTS))
    expect(box).toHaveTextContent('Durable: stored in Elastic')
    expect(box).not.toHaveTextContent(/not durable|memory/i)
  })

  it('Elastic with events not yet stored: pending count shown', async () => {
    const box = await storageFor(elasticResult(EVENTS, { degraded: true, pending_events: 4, reason: 'ELASTIC_TIMEOUT' }))
    expect(box).toHaveTextContent('Elastic, with events not yet stored')
    expect(box).toHaveTextContent('4 newer event(s) are held only in backend memory')
    expect(box).not.toHaveTextContent('Durable: stored in Elastic')
  })

  it.each([
    ['ELASTIC_NOT_CONFIGURED', 'Elastic is not configured'],
    ['ELASTIC_UNREACHABLE', 'Elastic is unreachable'],
    ['ELASTIC_AUTH_FAILED', 'Elastic rejected the configured API key'],
  ])('memory fallback (%s) is never described as durable', async (reason, label) => {
    const box = await storageFor(fallbackResult(EVENTS, { reason, pending_events: 3, dropped_events: 2 }))
    expect(box).toHaveTextContent('Not durable: memory fallback')
    expect(box).toHaveTextContent(label)
    expect(box).toHaveTextContent('not durably stored and will be lost if the backend restarts')
    expect(within(box).getByText('Pending (not in Elastic)').nextElementSibling).toHaveTextContent('3')
    expect(within(box).getByText('Dropped (buffer full)').nextElementSibling).toHaveTextContent('2')
    expect(box).not.toHaveTextContent('Durable: stored in Elastic')
    expect(await findGrid()).toBeInTheDocument()  // fallback events are still listed
  })

  it('treats a fallback response claiming durability as invalid', async () => {
    routeApi({ [ROUTE]: ok({ ...fallbackResult(EVENTS), durable: true }) })
    await setup()
    expect(await screen.findByRole('alert')).toHaveTextContent('unexpected response')
    expect(screen.queryByRole('status', { name: 'Audit storage' })).not.toBeInTheDocument()
  })
})

describe('Audit Logs: StrictMode', () => {
  it('loads and renders the grid under React StrictMode', async () => {
    routeApi({ [ROUTE]: ok(elasticResult(EVENTS)) })
    await setup({ strict: true })
    expect(dataRows(await findGrid())).toHaveLength(3)
    expect(screen.queryByText('Loading audit events…')).not.toBeInTheDocument()
  })
})
