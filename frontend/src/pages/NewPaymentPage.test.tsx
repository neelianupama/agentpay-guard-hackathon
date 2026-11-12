import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import {
  DECISION_CASES,
  VALID_DETAILS,
  deferredFetch,
  jsonResponse,
  makeAssessment,
  mockFetch,
  respondWith,
  sentBody,
} from '../test/http'
import { NewPaymentPage } from './NewPaymentPage'

type User = ReturnType<typeof userEvent.setup>

function setup(options?: Parameters<typeof userEvent.setup>[0]) {
  const user = userEvent.setup(options)
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
const submitButton = () => screen.getByRole('button', { name: /assess risk|assessing/i })
const resultPanel = () => screen.getByRole('region', { name: 'Risk assessment' })

async function fillForm(user: User, values = VALID_DETAILS) {
  await user.type(field.merchant(), values.merchant_name)
  await user.type(field.amount(), values.amount)
  await user.selectOptions(field.currency(), values.currency)
  await user.type(field.description(), values.description)
  await user.type(field.purpose(), values.purpose)
}

describe('NewPaymentPage: form', () => {
  it('renders all payment fields and the submit button', () => {
    setup()
    expect(field.merchant()).toHaveValue('')
    expect(field.amount()).toHaveValue('')
    expect(field.currency()).toHaveValue('USD')
    expect(field.description()).toHaveValue('')
    expect(field.purpose()).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Assess Risk' })).toBeEnabled()
  })

  it('offers exactly USD, EUR, GBP, CAD, AUD and INR', () => {
    setup()
    const options = within(field.currency() as HTMLElement).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'INR'])
  })

  it('has no inputs for risk score, level or decision', () => {
    setup()
    const form = screen.getByRole('button', { name: 'Assess Risk' }).closest('form')!
    expect(within(form).getAllByRole('textbox')).toHaveLength(4)
    expect(within(form).getAllByRole('combobox')).toHaveLength(1)
    expect(screen.queryByLabelText(/risk score|risk level|decision/i)).not.toBeInTheDocument()
  })

  it('shows the idle result state before any submission', () => {
    setup()
    expect(within(resultPanel()).getByText('No assessment yet')).toBeInTheDocument()
  })
})

describe('NewPaymentPage: client-side validation', () => {
  it('flags every required field and does not call the API', async () => {
    const fetch = respondWith(200, makeAssessment())
    const { user } = setup()

    await user.click(submitButton())

    expect(field.merchant()).toHaveAccessibleDescription('Merchant name is required.')
    expect(field.amount()).toHaveAccessibleDescription('Amount is required.')
    expect(field.description()).toHaveAccessibleDescription('Description is required.')
    expect(field.purpose()).toHaveAccessibleDescription('Payment purpose is required.')
    for (const input of [field.merchant(), field.amount(), field.description(), field.purpose()]) {
      expect(input).toHaveAttribute('aria-invalid', 'true')
    }
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each([
    ['0', 'Amount must be greater than 0.'],
    ['12.345', 'Enter a number like 49.99, with up to 2 decimal places.'],
    ['ten', 'Enter a number like 49.99, with up to 2 decimal places.'],
  ])('rejects amount "%s" without calling the API', async (amount, message) => {
    const fetch = respondWith(200, makeAssessment())
    const { user } = setup()
    await fillForm(user, { ...VALID_DETAILS, amount })

    await user.click(submitButton())

    expect(field.amount()).toHaveAccessibleDescription(message)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('clears a field error when the field is edited', async () => {
    respondWith(200, makeAssessment())
    const { user } = setup()
    await user.click(submitButton())
    expect(field.merchant()).toHaveAttribute('aria-invalid', 'true')

    await user.type(field.merchant(), 'A')

    expect(field.merchant()).not.toHaveAttribute('aria-invalid')
    expect(field.amount()).toHaveAttribute('aria-invalid', 'true')
  })
})

describe('NewPaymentPage: successful assessment', () => {
  it('sends the trimmed TransactionDetails and nothing else', async () => {
    const fetch = respondWith(200, makeAssessment())
    const { user } = setup()
    await fillForm(user, { ...VALID_DETAILS, merchant_name: '  Acme Cloud  ', currency: 'EUR' })

    await user.click(submitButton())
    await within(resultPanel()).findByText('ALLOW')

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(sentBody(fetch)).toEqual({ ...VALID_DETAILS, currency: 'EUR' })
  })

  it.each(DECISION_CASES)(
    'renders a $decision decision from the backend',
    async ({ decision, level, score, title, reasons }) => {
      respondWith(200, makeAssessment({ decision, risk_level: level, risk_score: score, reasons }))
      const { user } = setup()
      await fillForm(user)

      await user.click(submitButton())

      const panel = resultPanel()
      expect(await within(panel).findByText(decision)).toBeInTheDocument()
      expect(within(panel).getByText(title, { exact: false })).toBeInTheDocument()
      expect(within(panel).getByText(String(score))).toBeInTheDocument()
      expect(within(panel).getByRole('meter', { name: 'Risk score' })).toHaveAttribute(
        'aria-valuenow',
        String(score),
      )
      expect(within(panel).getByText(level)).toBeInTheDocument()

      const list = within(panel).getByRole('heading', { name: 'Reasons' }).nextElementSibling as HTMLElement
      expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual(reasons)
    },
  )

  it('shows the assessment timestamp from the backend', async () => {
    respondWith(200, makeAssessment({ assessed_at: '2026-11-12T06:30:00.123456Z' }))
    const { user } = setup()
    await fillForm(user)
    await user.click(submitButton())

    await within(resultPanel()).findByText('ALLOW')
    const time = resultPanel().querySelector('time')
    expect(time).toHaveAttribute('dateTime', '2026-11-12T06:30:00.123456Z')
    expect(time).toHaveAttribute('title', '2026-11-12T06:30:00.123456Z')
    expect(time?.textContent).toBe(
      new Date('2026-11-12T06:30:00.123456Z').toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'medium',
      }),
    )
  })

  it('shows the payment exactly as echoed by the backend', async () => {
    respondWith(
      200,
      makeAssessment({ transaction: { ...VALID_DETAILS, amount: '1234567.5', currency: 'GBP' } }),
    )
    const { user } = setup()
    await fillForm(user)
    await user.click(submitButton())

    const panel = resultPanel()
    expect(await within(panel).findByText('1,234,567.50 GBP', { exact: false })).toBeInTheDocument()
    expect(within(panel).getByText('No payment has been made.', { exact: false })).toBeInTheDocument()
  })
})

describe('NewPaymentPage: loading and duplicate submissions', () => {
  it('disables the form and shows progress while the request runs', async () => {
    const pending = deferredFetch()
    const { user } = setup()
    await fillForm(user)

    await user.click(submitButton())

    const button = submitButton()
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleName('Assessing…')
    expect(field.merchant()).toBeDisabled()
    expect(field.amount()).toBeDisabled()
    expect(within(resultPanel()).getByRole('status')).toHaveTextContent('Assessing risk…')

    await act(async () => pending.resolve(jsonResponse(200, makeAssessment())))
    expect(await within(resultPanel()).findByText('ALLOW')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Assess Risk' })).toBeEnabled()
  })

  it('sends only one request for repeated clicks and form submits', async () => {
    const pending = deferredFetch()
    const { user } = setup()
    await fillForm(user)

    await user.click(submitButton())
    await user.click(submitButton())
    await user.dblClick(submitButton())
    const form = submitButton().closest('form')!
    fireEvent.submit(form)
    fireEvent.submit(form)

    expect(pending.fetch).toHaveBeenCalledTimes(1)

    await act(async () => pending.resolve(jsonResponse(200, makeAssessment())))
    await within(resultPanel()).findByText('ALLOW')
    expect(pending.fetch).toHaveBeenCalledTimes(1)
  })

  it('allows a new assessment after the previous one completes', async () => {
    const fetch = respondWith(200, makeAssessment())
    const { user } = setup()
    await fillForm(user)

    await user.click(submitButton())
    await within(resultPanel()).findByText('ALLOW')
    await user.click(submitButton())
    await within(resultPanel()).findByText('ALLOW')

    expect(fetch).toHaveBeenCalledTimes(2)
  })
})

describe('NewPaymentPage: API failures', () => {
  it('shows a clear message when the backend is unreachable', async () => {
    mockFetch(async () => {
      throw new TypeError('Failed to fetch')
    })
    const { user } = setup()
    await fillForm(user)

    await user.click(submitButton())

    const alert = await within(resultPanel()).findByRole('alert')
    expect(alert).toHaveTextContent('Assessment failed')
    expect(alert).toHaveTextContent('Could not reach the risk service at http://localhost:8000.')
    expect(screen.getByRole('button', { name: 'Assess Risk' })).toBeEnabled()
  })

  it('shows the HTTP status for server errors', async () => {
    respondWith(500, { detail: 'Internal Server Error' })
    const { user } = setup()
    await fillForm(user)

    await user.click(submitButton())

    expect(await within(resultPanel()).findByRole('alert')).toHaveTextContent(
      'The risk service returned an error (HTTP 500).',
    )
  })

  it('shows an error for a response that breaks the contract', async () => {
    respondWith(200, { ...makeAssessment(), decision: 'APPROVED_BY_AGENT' })
    const { user } = setup()
    await fillForm(user)

    await user.click(submitButton())

    expect(await within(resultPanel()).findByRole('alert')).toHaveTextContent('unexpected response')
    expect(within(resultPanel()).queryByText('APPROVED_BY_AGENT')).not.toBeInTheDocument()
  })

  it('times out after 15 seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    deferredFetch()
    const { user } = setup({ advanceTimers: vi.advanceTimersByTime })
    await fillForm(user)

    await user.click(submitButton())
    expect(submitButton()).toBeDisabled()

    await act(async () => {
      vi.advanceTimersByTime(15_000)
    })

    expect(await within(resultPanel()).findByRole('alert')).toHaveTextContent(
      'The risk service took too long to respond.',
    )
    expect(screen.getByRole('button', { name: 'Assess Risk' })).toBeEnabled()
  })
})

describe('NewPaymentPage: backend validation errors', () => {
  it('shows HTTP 422 messages under the matching fields', async () => {
    respondWith(422, {
      detail: [
        { type: 'string_too_long', loc: ['body', 'merchant_name'], msg: 'String should have at most 200 characters' },
        { type: 'greater_than', loc: ['body', 'amount'], msg: 'Input should be greater than 0' },
      ],
    })
    const { user } = setup()
    await fillForm(user)

    await user.click(submitButton())

    const alert = await within(resultPanel()).findByRole('alert')
    expect(alert).toHaveTextContent('The risk service rejected some of the details.')
    expect(alert).toHaveTextContent('Correct the highlighted fields and try again.')
    expect(field.merchant()).toHaveAccessibleDescription('String should have at most 200 characters')
    expect(field.amount()).toHaveAccessibleDescription('Input should be greater than 0')
    expect(field.purpose()).not.toHaveAttribute('aria-invalid')
  })

  it('clears a backend field error when that field is edited', async () => {
    respondWith(422, {
      detail: [{ type: 'greater_than', loc: ['body', 'amount'], msg: 'Input should be greater than 0' }],
    })
    const { user } = setup()
    await fillForm(user)
    await user.click(submitButton())
    await within(resultPanel()).findByRole('alert')
    expect(field.amount()).toHaveAttribute('aria-invalid', 'true')

    await user.type(field.amount(), '1')

    expect(field.amount()).not.toHaveAttribute('aria-invalid')
  })
})
