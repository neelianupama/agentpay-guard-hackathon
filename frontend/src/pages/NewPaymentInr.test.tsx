/**
 * INR support on the New Payment page: selection, assessment, and parsing.
 * Only `fetch` is mocked; the backend remains the only source of decisions.
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { TransactionDetails } from '../api/risk'
import {
  API_URL,
  INTENT_URL,
  calledUrls,
  jsonResponse,
  makeAssessment,
  makeCompleteIntent,
  routeFetch,
  sentBody,
} from '../test/http'
import { NewPaymentPage } from './NewPaymentPage'

type User = ReturnType<typeof userEvent.setup>

const INR_DETAILS: TransactionDetails = {
  merchant_name: 'ABC Technologies',
  amount: '750000',
  currency: 'INR',
  description: 'Software services',
  purpose: 'Engineering tooling',
}

const INR_BLOCK = makeAssessment({
  transaction: { ...INR_DETAILS },
  risk_score: 60,
  risk_level: 'HIGH',
  decision: 'BLOCK',
  reasons: [
    'Amount 750,000.00 INR is at or above the maximum allowed transaction amount of 750,000.00 INR.',
  ],
})

function setup() {
  const user = userEvent.setup()
  render(<NewPaymentPage />)
  return { user }
}

const field = {
  merchant: () => screen.getByLabelText('Merchant name'),
  amount: () => screen.getByLabelText('Amount'),
  currency: () => screen.getByLabelText('Currency'),
  description: () => screen.getByLabelText('Description'),
  purpose: () => screen.getByLabelText('Payment purpose'),
}
const resultPanel = () => screen.getByRole('region', { name: 'Risk assessment' })
const assessButton = () => screen.getByRole('button', { name: /assess risk|assessing/i })

async function fillInr(user: User, values = INR_DETAILS) {
  await user.type(field.merchant(), values.merchant_name)
  await user.type(field.amount(), values.amount)
  await user.selectOptions(field.currency(), 'INR')
  await user.type(field.description(), values.description)
  await user.type(field.purpose(), values.purpose)
}

describe('INR: currency selection', () => {
  it('offers INR as a supported option', async () => {
    const { user } = setup()
    const option = within(field.currency() as HTMLElement).getByRole('option', { name: 'INR' })
    expect(option).toBeEnabled()

    await user.selectOptions(field.currency(), 'INR')

    expect(field.currency()).toHaveValue('INR')
    expect(field.currency()).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByText(/not supported/)).not.toBeInTheDocument()
  })
})

describe('INR: assessment', () => {
  it('sends the INR amount as entered (no conversion) and shows the backend BLOCK', async () => {
    const fetch = routeFetch({ risk: () => jsonResponse(200, INR_BLOCK) })
    const { user } = setup()
    await fillInr(user)

    await user.click(assessButton())

    const panel = resultPanel()
    expect(await within(panel).findByText('BLOCK')).toBeInTheDocument()
    expect(sentBody(fetch)).toEqual(INR_DETAILS)
    expect(within(panel).getByText('Blocked by policy', { exact: false })).toBeInTheDocument()
    // Shown in the assessed-payment line and in the reason, formatted in INR.
    expect(within(panel).getAllByText('750,000.00 INR', { exact: false })).toHaveLength(2)
    expect(
      within(panel).getByText(
        'Amount 750,000.00 INR is at or above the maximum allowed transaction amount of 750,000.00 INR.',
      ),
    ).toBeInTheDocument()
    expect(within(panel).queryByText(/USD/)).not.toBeInTheDocument()
  })

  it('shows an INR REVIEW decision from the backend', async () => {
    const review = makeAssessment({
      transaction: { ...INR_DETAILS, amount: '150000' },
      risk_score: 35,
      risk_level: 'MEDIUM',
      decision: 'REVIEW',
      reasons: ['Amount 150,000.00 INR is at or above the human-review threshold of 150,000.00 INR.'],
    })
    routeFetch({ risk: () => jsonResponse(200, review) })
    const { user } = setup()
    await fillInr(user, { ...INR_DETAILS, amount: '150000' })

    await user.click(assessButton())

    const panel = resultPanel()
    expect(await within(panel).findByText('REVIEW')).toBeInTheDocument()
    expect(within(panel).getAllByText('150,000.00 INR', { exact: false })).toHaveLength(2)
  })

  it('offers no way to override a BLOCK decision', async () => {
    routeFetch({ risk: () => jsonResponse(200, INR_BLOCK) })
    const { user } = setup()
    await fillInr(user)

    await user.click(assessButton())
    await within(resultPanel()).findByText('BLOCK')

    expect(within(resultPanel()).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve|override|allow/i })).not.toBeInTheDocument()
  })
})

describe('INR: natural-language parsing', () => {
  const parsedInr = makeCompleteIntent({ amount: '750000', currency: 'INR' })

  it('keeps INR as a usable currency and does not assess automatically', async () => {
    const fetch = routeFetch({ intent: () => jsonResponse(200, parsedInr) })
    const { user } = setup()

    await user.type(
      screen.getByLabelText('Payment request'),
      'Pay ₹7,50,000 to ABC Technologies for software services for the Q3 tooling budget.',
    )
    await user.click(screen.getByRole('button', { name: 'Parse with AI' }))
    const summary = await screen.findByRole('region', { name: 'Extracted by AI' })

    expect(within(summary).getByText('INR', { selector: 'dd' })).toBeInTheDocument()
    expect(within(summary).queryByText(/not supported/)).not.toBeInTheDocument()
    expect(within(summary).getByRole('status')).toHaveTextContent('Review before assessing')
    expect(field.currency()).toHaveValue('INR')
    expect(field.amount()).toHaveValue('750000')
    expect(field.currency()).not.toHaveAttribute('aria-invalid')
    expect(calledUrls(fetch)).toEqual([INTENT_URL])
  })

  it('assesses the parsed INR payment only after Assess Risk is clicked', async () => {
    const fetch = routeFetch({
      intent: () => jsonResponse(200, parsedInr),
      risk: () => jsonResponse(200, INR_BLOCK),
    })
    const { user } = setup()

    await user.type(screen.getByLabelText('Payment request'), 'Pay 750000 INR to ABC Technologies.')
    await user.click(screen.getByRole('button', { name: 'Parse with AI' }))
    await screen.findByRole('region', { name: 'Extracted by AI' })
    expect(calledUrls(fetch)).toEqual([INTENT_URL])

    await user.click(assessButton())
    expect(await within(resultPanel()).findByText('BLOCK')).toBeInTheDocument()

    expect(calledUrls(fetch)).toEqual([INTENT_URL, API_URL])
    expect(sentBody(fetch, 1)).toMatchObject({ amount: '750000', currency: 'INR' })
  })
})
