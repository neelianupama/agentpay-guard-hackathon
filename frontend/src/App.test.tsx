import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from './App'
import { ok, paymentIn, paymentList, routeApi } from './test/http'
import { REVIEWER_TOKEN, enterReviewerToken } from './test/reviewer'

const PLACEHOLDER = 'This section is not built yet. It will be available in an upcoming release.'

function setup() {
  const user = userEvent.setup()
  render(<App />)
  const nav = screen.getByRole('navigation')
  const navButton = (name: string) => within(nav).getByRole('button', { name })
  return { user, navButton }
}

describe('App navigation', () => {
  it('shows the Overview dashboard by default', () => {
    const { navButton } = setup()
    expect(screen.getByRole('heading', { level: 1, name: 'AgentPay Guard' })).toBeInTheDocument()
    expect(screen.getByText('Total Transactions')).toBeInTheDocument()
    expect(screen.getByText('Demo data')).toBeInTheDocument()
    expect(navButton('Overview')).toHaveAttribute('aria-current', 'page')
  })

  it('opens the risk assessment form from New Payment', async () => {
    const { user, navButton } = setup()

    await user.click(navButton('New Payment'))

    expect(screen.getByRole('heading', { level: 1, name: 'New Payment' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Assess Risk' })).toBeInTheDocument()
    expect(navButton('New Payment')).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByText('Demo data')).not.toBeInTheDocument()
  })

  it('opens the form from the Overview empty state', async () => {
    const { user } = setup()
    await user.click(screen.getByRole('button', { name: 'Create a payment request' }))
    expect(screen.getByRole('button', { name: 'Assess Risk' })).toBeInTheDocument()
  })

  it.each(['Agent Activity'])(
    '%s still shows the placeholder',
    async (label) => {
      const { user, navButton } = setup()

      await user.click(navButton(label))

      expect(screen.getByRole('heading', { level: 1, name: label })).toBeInTheDocument()
      expect(screen.getByText(PLACEHOLDER)).toBeInTheDocument()
      expect(navButton(label)).toHaveAttribute('aria-current', 'page')
    },
  )

  // Step 5: Transactions and Approvals are now connected to the payments API.
  it('opens the Transactions page backed by the payments API', async () => {
    const api = routeApi({ 'GET /api/v1/payments': ok(paymentList([paymentIn('CAPTURED')])) })
    const { user, navButton } = setup()

    await user.click(navButton('Transactions'))

    expect(screen.getByRole('heading', { level: 1, name: 'Transactions' })).toBeInTheDocument()
    expect(api).not.toHaveBeenCalled() // reads need the reviewer token
    await enterReviewerToken(user)
    expect(await screen.findByRole('button', { name: /View payment to Acme Cloud/ })).toBeInTheDocument()
    expect(screen.queryByText(PLACEHOLDER)).not.toBeInTheDocument()
    expect(navButton('Transactions')).toHaveAttribute('aria-current', 'page')
    expect(api.calls()).toEqual(['GET /api/v1/payments'])
    expect(api.headersOf(0).get('authorization')).toBe(`Bearer ${REVIEWER_TOKEN}`)
  })

  it('opens the Approvals page backed by the payments API', async () => {
    routeApi({ 'GET /api/v1/payments': ok(paymentList([paymentIn('PENDING_REVIEW')])) })
    const { user, navButton } = setup()

    await user.click(navButton('Approvals'))

    expect(screen.getByRole('heading', { level: 1, name: 'Approvals' })).toBeInTheDocument()
    expect(screen.getByLabelText('Reviewer token')).toHaveAttribute('type', 'password')
    await enterReviewerToken(user)
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.queryByText(PLACEHOLDER)).not.toBeInTheDocument()
  })

  // Step 7C: Audit Logs is an AG Grid page over the authenticated audit API.
  it('opens the Audit Logs page backed by the audit API', async () => {
    const api = routeApi({ 'GET /api/v1/audit/events': ok({
      source: 'memory_fallback', durable: false, degraded: true, reason: 'ELASTIC_NOT_CONFIGURED',
      message: 'x', pending_events: 0, dropped_events: 0, items: [], total: 0, limit: 25, offset: 0 }) })
    const { user, navButton } = setup()

    await user.click(navButton('Audit Logs'))

    expect(screen.getByRole('heading', { level: 1, name: 'Audit Logs' })).toBeInTheDocument()
    expect(screen.queryByText(PLACEHOLDER)).not.toBeInTheDocument()
    expect(api).not.toHaveBeenCalled() // needs the reviewer token first
    await enterReviewerToken(user)
    expect(await screen.findByText('No audit events')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Audit storage' })).toHaveTextContent('Not durable: memory fallback')
    expect(api.headersOf(0).get('authorization')).toBe(`Bearer ${REVIEWER_TOKEN}`)
  })

  it('keeps the token in memory across sections, never in storage or the URL', async () => {
    routeApi({ 'GET /api/v1/payments': ok(paymentList([])) })
    const { user, navButton } = setup()
    await user.click(navButton('Approvals'))
    await enterReviewerToken(user)
    await screen.findByText('Nothing to review')

    await user.click(navButton('Transactions'))
    expect(await screen.findByText('No payments yet')).toBeInTheDocument() // no re-entry needed
    expect(localStorage.length + sessionStorage.length).toBe(0)
    expect(window.location.href).not.toContain(REVIEWER_TOKEN)
    expect(document.body.textContent).not.toContain(REVIEWER_TOKEN)
  })
})
