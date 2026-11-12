/**
 * New Payment page: natural-language parsing ("Parse with AI") flow.
 *
 * Only `fetch` is mocked. Parsing must never trigger risk assessment; risk is
 * assessed only when the user explicitly clicks "Assess Risk".
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { INTENT_TIMEOUT_MS } from '../api/intent'
import { MISSING_FROM_REQUEST_MESSAGE, unsupportedCurrencyMessage } from '../lib/paymentForm'
import {
  API_URL,
  DECISION_CASES,
  EXAMPLE_REQUEST,
  INTENT_URL,
  VALID_DETAILS,
  calledUrls,
  deferredFetch,
  jsonResponse,
  makeAssessment,
  makeCompleteIntent,
  makeParsedIntent,
  mockFetch,
  routeFetch,
  sentBody,
} from '../test/http'
import { NewPaymentPage } from './NewPaymentPage'

type User = ReturnType<typeof userEvent.setup>

function setup(options?: Parameters<typeof userEvent.setup>[0]) {
  const user = userEvent.setup(options)
  render(<NewPaymentPage />)
  return { user }
}

const requestBox = () => screen.getByLabelText('Payment request')
const parseButton = () => screen.getByRole('button', { name: /parse with ai|parsing/i })
const assessButton = () => screen.getByRole('button', { name: /assess risk|assessing/i })
const resultPanel = () => screen.getByRole('region', { name: 'Risk assessment' })
const intentPanel = () => screen.getByRole('form', { name: 'Describe the payment' })
const summary = () => screen.findByRole('region', { name: 'Extracted by AI' })
const field = {
  merchant: () => screen.getByLabelText('Merchant name'),
  amount: () => screen.getByLabelText('Amount'),
  currency: () => screen.getByLabelText('Currency'),
  description: () => screen.getByLabelText('Description'),
  purpose: () => screen.getByLabelText('Payment purpose'),
}

/** The value shown next to a label in the "Extracted by AI" summary. */
function summaryValue(region: HTMLElement, label: string): string {
  const term = within(region).getByText(label, { selector: 'dt' })
  return term.nextElementSibling?.textContent ?? ''
}

const ok = (body: unknown) => () => jsonResponse(200, body)

async function parse(user: User, text = EXAMPLE_REQUEST) {
  await user.type(requestBox(), text)
  await user.click(parseButton())
}

describe('Parse with AI: rendering', () => {
  it('offers a natural-language request box next to the manual form', () => {
    setup()
    expect(requestBox()).toHaveValue('')
    expect(parseButton()).toHaveAccessibleName('Parse with AI')
    expect(parseButton()).toBeEnabled()
    expect(assessButton()).toHaveAccessibleName('Assess Risk')
    expect(
      within(intentPanel()).getByText(/does not assess risk, approve or make payments/),
    ).toBeInTheDocument()
  })

  it('requires request text before parsing', async () => {
    const fetch = routeFetch({})
    const { user } = setup()

    await user.click(parseButton())

    expect(requestBox()).toHaveAccessibleDescription('Enter a payment request to parse.')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('clears the request error when the text is edited', async () => {
    routeFetch({})
    const { user } = setup()
    await user.click(parseButton())
    await user.type(requestBox(), 'P')
    expect(requestBox()).not.toHaveAttribute('aria-invalid')
  })
})

describe('Parse with AI: successful parsing', () => {
  it('sends only the trimmed request text and never calls the risk endpoint', async () => {
    const fetch = routeFetch({ intent: ok(makeParsedIntent()) })
    const { user } = setup()

    await parse(user, `   ${EXAMPLE_REQUEST}   `)
    await summary()

    expect(calledUrls(fetch)).toEqual([INTENT_URL])
    expect(sentBody(fetch)).toEqual({ request_text: EXAMPLE_REQUEST })
    expect(within(resultPanel()).getByText('No assessment yet')).toBeInTheDocument()
  })

  it('displays every extracted detail', async () => {
    routeFetch({ intent: ok(makeParsedIntent()) })
    const { user } = setup()

    await parse(user)
    const region = await summary()

    expect(summaryValue(region, 'Merchant name')).toBe('ABC Technologies')
    expect(summaryValue(region, 'Amount')).toBe('250')
    expect(summaryValue(region, 'Currency')).toBe('USD')
    expect(summaryValue(region, 'Description')).toBe('software services')
    expect(summaryValue(region, 'Payment purpose')).toBe('Not found')
  })

  it('pre-fills the editable payment form with the extracted details', async () => {
    routeFetch({ intent: ok(makeParsedIntent()) })
    const { user } = setup()

    await parse(user)
    await summary()

    expect(field.merchant()).toHaveValue('ABC Technologies')
    expect(field.amount()).toHaveValue('250')
    expect(field.currency()).toHaveValue('USD')
    expect(field.description()).toHaveValue('software services')
    expect(field.purpose()).toHaveValue('')
    expect(field.merchant()).toBeEnabled()
  })

  it('asks the user to review a complete intent but does not assess it', async () => {
    const fetch = routeFetch({ intent: ok(makeCompleteIntent()) })
    const { user } = setup()

    await parse(user)
    const region = await summary()

    expect(within(region).getByRole('status')).toHaveTextContent(
      'All details were found. Check them below, then click Assess Risk.',
    )
    expect(within(region).queryByText('Clarification needed')).not.toBeInTheDocument()
    expect(calledUrls(fetch)).toEqual([INTENT_URL])
    expect(within(resultPanel()).getByText('No assessment yet')).toBeInTheDocument()
  })
})

describe('Parse with AI: missing details and clarification', () => {
  const incomplete = makeParsedIntent({
    amount: null,
    currency: null,
    missing_fields: ['amount', 'currency', 'purpose'],
  })

  it('lists missing fields and shows that clarification is needed', async () => {
    routeFetch({ intent: ok(incomplete) })
    const { user } = setup()

    await parse(user, 'Pay ABC Technologies for software services.')
    const region = await summary()

    const alert = within(region).getByRole('alert')
    expect(alert).toHaveTextContent('Clarification needed')
    expect(alert).toHaveTextContent('Missing: Amount, Currency, Payment purpose.')
    expect(summaryValue(region, 'Amount')).toBe('Not found')
    expect(summaryValue(region, 'Currency')).toBe('Not found')
  })

  it('highlights missing fields in the form without inventing values', async () => {
    routeFetch({ intent: ok(incomplete) })
    const { user } = setup()

    await parse(user, 'Pay ABC Technologies for software services.')
    await summary()

    expect(field.amount()).toHaveValue('')
    expect(field.amount()).toHaveAccessibleDescription(MISSING_FROM_REQUEST_MESSAGE)
    expect(field.purpose()).toHaveAccessibleDescription(MISSING_FROM_REQUEST_MESSAGE)
    // Currency is not defaulted to USD: the select shows that nothing is chosen.
    expect(field.currency()).toHaveValue('')
    expect(field.currency()).toHaveAccessibleDescription(MISSING_FROM_REQUEST_MESSAGE)
    expect(field.merchant()).not.toHaveAttribute('aria-invalid')
  })

  it('never assesses risk while required details are missing', async () => {
    const fetch = routeFetch({ intent: ok(incomplete), risk: ok(makeAssessment()) })
    const { user } = setup()

    await parse(user, 'Pay ABC Technologies for software services.')
    await summary()
    await user.click(assessButton())

    expect(calledUrls(fetch)).toEqual([INTENT_URL])
    expect(field.amount()).toHaveAccessibleDescription('Amount is required.')
    expect(field.currency()).toHaveAccessibleDescription('Select a currency.')
    expect(within(resultPanel()).getByText('No assessment yet')).toBeInTheDocument()
  })

  it('shows clarification for an ambiguous request even when every field is filled', async () => {
    routeFetch({ intent: ok(makeCompleteIntent({ clarification_needed: true })) })
    const { user } = setup()

    await parse(user)
    const alert = within(await summary()).getByRole('alert')

    expect(alert).toHaveTextContent('Clarification needed')
    expect(alert).toHaveTextContent('The AI flagged this request as ambiguous.')
  })
})

describe('Parse with AI: review, edit and explicit assessment', () => {
  it('assesses only after the user clicks Assess Risk, with the reviewed details', async () => {
    const { decision, level, score, title, reasons } = DECISION_CASES[1]
    const fetch = routeFetch({
      intent: ok(makeCompleteIntent()),
      risk: ok(makeAssessment({ decision, risk_level: level, risk_score: score, reasons })),
    })
    const { user } = setup()

    await parse(user)
    await summary()
    expect(calledUrls(fetch)).toEqual([INTENT_URL])

    await user.click(assessButton())
    const panel = resultPanel()
    expect(await within(panel).findByText(decision)).toBeInTheDocument()
    expect(within(panel).getByText(title, { exact: false })).toBeInTheDocument()

    expect(calledUrls(fetch)).toEqual([INTENT_URL, API_URL])
    expect(sentBody(fetch, 1)).toEqual({
      merchant_name: 'ABC Technologies',
      amount: '250',
      currency: 'USD',
      description: 'software services',
      purpose: 'Q3 tooling budget',
    })
    // The real backend result is shown with the existing result components.
    expect(within(panel).getByRole('meter', { name: 'Risk score' })).toHaveAttribute(
      'aria-valuenow',
      String(score),
    )
    expect(within(panel).getByText(level)).toBeInTheDocument()
    const list = within(panel).getByRole('heading', { name: 'Reasons' }).nextElementSibling as HTMLElement
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual(reasons)
    expect(panel.querySelector('time')).toHaveAttribute('dateTime', '2026-11-12T06:30:00Z')
  })

  it('sends the user-edited details, not the AI values', async () => {
    const fetch = routeFetch({ intent: ok(makeParsedIntent()), risk: ok(makeAssessment()) })
    const { user } = setup()

    await parse(user)
    const region = await summary()
    await user.clear(field.amount())
    await user.type(field.amount(), '300.00')
    await user.clear(field.merchant())
    await user.type(field.merchant(), 'ABC Technologies Ltd')
    await user.type(field.purpose(), 'Engineering tooling')
    expect(field.purpose()).not.toHaveAttribute('aria-invalid')

    await user.click(assessButton())
    await within(resultPanel()).findByText('ALLOW')

    expect(sentBody(fetch, 1)).toEqual({
      merchant_name: 'ABC Technologies Ltd',
      amount: '300.00',
      currency: 'USD',
      description: 'software services',
      purpose: 'Engineering tooling',
    })
    // The summary keeps showing what the AI extracted, for comparison.
    expect(summaryValue(region, 'Amount')).toBe('250')
  })

  it('clears the previous risk result when a new request is parsed', async () => {
    routeFetch({ intent: ok(makeCompleteIntent()), risk: ok(makeAssessment()) })
    const { user } = setup()

    await parse(user)
    await summary()
    await user.click(assessButton())
    await within(resultPanel()).findByText('ALLOW')

    await user.click(parseButton())
    await within(resultPanel()).findByText('No assessment yet')
  })
})

describe('Parse with AI: unsupported currencies', () => {
  const jpy = makeParsedIntent({ amount: '5000', currency: 'JPY' })
  const jpyText = 'Pay ¥5000 to ABC Technologies for software services.'

  it('shows the currency as stated and explains it is unsupported', async () => {
    routeFetch({ intent: ok(jpy) })
    const { user } = setup()

    await parse(user, jpyText)
    const region = await summary()

    expect(summaryValue(region, 'Currency')).toBe('JPY (not supported)')
    expect(within(region).getByRole('alert')).toHaveTextContent(
      'JPY is not supported for risk assessment. It has not been converted.',
    )
    expect(field.currency()).toHaveValue('JPY')
    expect(field.amount()).toHaveValue('5000')
    expect(field.currency()).toHaveAccessibleDescription(unsupportedCurrencyMessage('JPY'))
  })

  it('does not assess risk with an unsupported currency', async () => {
    const fetch = routeFetch({
      intent: ok(makeCompleteIntent({ amount: '5000', currency: 'JPY' })),
      risk: ok(makeAssessment()),
    })
    const { user } = setup()

    await parse(user, jpyText)
    await summary()
    await user.click(assessButton())

    expect(calledUrls(fetch)).toEqual([INTENT_URL])
    expect(field.currency()).toHaveAccessibleDescription(unsupportedCurrencyMessage('JPY'))
    expect(field.currency()).toHaveValue('JPY')
    expect(within(resultPanel()).getByText('No assessment yet')).toBeInTheDocument()
  })

  it('never converts the amount; only an explicit user choice changes the currency', async () => {
    const fetch = routeFetch({
      intent: ok(makeCompleteIntent({ amount: '5000', currency: 'JPY' })),
      risk: ok(makeAssessment()),
    })
    const { user } = setup()

    await parse(user, jpyText)
    await summary()
    await user.selectOptions(field.currency(), 'EUR')
    expect(field.currency()).not.toHaveAttribute('aria-invalid')

    await user.click(assessButton())
    await within(resultPanel()).findByText('ALLOW')

    expect(sentBody(fetch, 1)).toMatchObject({ amount: '5000', currency: 'EUR' })
  })
})

describe('Parse with AI: failures', () => {
  it.each([
    [503, 'INTENT_SERVICE_UNAVAILABLE', 'AI parsing is not available right now.'],
    [503, 'INTENT_MODEL_UNAVAILABLE', 'AI parsing is not available right now.'],
    [429, 'INTENT_RATE_LIMITED', 'Too many parsing requests right now.'],
    [502, 'INTENT_EXTRACTION_FAILED', 'The AI could not read this request.'],
    [504, 'INTENT_TIMEOUT', 'The AI took too long to read the request.'],
  ])('shows a safe message for HTTP %i %s', async (status, code, message) => {
    const fetch = routeFetch({
      intent: () => jsonResponse(status, { code, message: 'provider said: gsk_leaked_key' }),
    })
    const { user } = setup()

    await parse(user)

    const alert = await within(intentPanel()).findByRole('alert')
    expect(alert).toHaveTextContent('AI parsing failed')
    expect(alert).toHaveTextContent(message)
    expect(document.body).not.toHaveTextContent('gsk_leaked_key')
    expect(document.body).not.toHaveTextContent('provider said')
    expect(field.merchant()).toHaveValue('')
    expect(parseButton()).toBeEnabled()
    expect(calledUrls(fetch)).toEqual([INTENT_URL])
  })

  it('shows a clear message when the backend is unreachable', async () => {
    mockFetch(async () => {
      throw new TypeError('Failed to fetch')
    })
    const { user } = setup()

    await parse(user)

    expect(await within(intentPanel()).findByRole('alert')).toHaveTextContent(
      'Could not reach the AI parsing service at http://localhost:8000.',
    )
  })

  it('rejects a parse response that carries a risk decision', async () => {
    const fetch = routeFetch({
      intent: ok({ ...makeCompleteIntent(), decision: 'ALLOW', risk_score: 0 }),
    })
    const { user } = setup()

    await parse(user)

    expect(await within(intentPanel()).findByRole('alert')).toHaveTextContent('unexpected response')
    expect(field.merchant()).toHaveValue('')
    expect(within(resultPanel()).getByText('No assessment yet')).toBeInTheDocument()
    expect(within(resultPanel()).queryByText('ALLOW')).not.toBeInTheDocument()
    expect(calledUrls(fetch)).toEqual([INTENT_URL])
  })

  it('shows backend validation errors under the request box', async () => {
    routeFetch({
      intent: () =>
        jsonResponse(422, {
          detail: [{ type: 'string_too_long', loc: ['body', 'request_text'], msg: 'Request is too long' }],
        }),
    })
    const { user } = setup()

    await parse(user)

    expect(await screen.findByText('Request is too long')).toBeInTheDocument()
    expect(requestBox()).toHaveAccessibleDescription('Request is too long')
  })

  it('times out after the AI parsing timeout', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    deferredFetch()
    const { user } = setup({ advanceTimers: vi.advanceTimersByTime })

    await parse(user)
    expect(parseButton()).toBeDisabled()

    await act(async () => {
      vi.advanceTimersByTime(INTENT_TIMEOUT_MS)
    })

    expect(await within(intentPanel()).findByRole('alert')).toHaveTextContent(
      'The AI took too long to read the request.',
    )
    expect(parseButton()).toBeEnabled()
  })

  it('still allows entering details manually after a failure', async () => {
    const fetch = routeFetch({
      intent: () => jsonResponse(503, { code: 'INTENT_SERVICE_UNAVAILABLE', message: 'x' }),
      risk: ok(makeAssessment()),
    })
    const { user } = setup()

    await parse(user)
    await within(intentPanel()).findByRole('alert')
    await user.type(field.merchant(), VALID_DETAILS.merchant_name)
    await user.type(field.amount(), VALID_DETAILS.amount)
    await user.type(field.description(), VALID_DETAILS.description)
    await user.type(field.purpose(), VALID_DETAILS.purpose)
    await user.click(assessButton())

    await within(resultPanel()).findByText('ALLOW')
    expect(sentBody(fetch, 1)).toEqual(VALID_DETAILS)
  })
})

describe('Parse with AI: loading and duplicate submissions', () => {
  it('shows progress and locks both forms while parsing', async () => {
    const pending = deferredFetch()
    const { user } = setup()

    await parse(user)

    expect(parseButton()).toBeDisabled()
    expect(parseButton()).toHaveAccessibleName('Parsing…')
    expect(requestBox()).toBeDisabled()
    expect(within(intentPanel()).getByRole('status')).toHaveTextContent('Reading your request…')
    expect(assessButton()).toBeDisabled()
    expect(assessButton()).toHaveAccessibleName('Assess Risk')
    expect(field.merchant()).toBeDisabled()

    await act(async () => pending.resolve(jsonResponse(200, makeParsedIntent())))
    await summary()
    expect(parseButton()).toBeEnabled()
    expect(assessButton()).toBeEnabled()
    expect(pending.fetch).toHaveBeenCalledTimes(1)
  })

  it('sends only one parse request for repeated clicks and submits', async () => {
    const pending = deferredFetch()
    const { user } = setup()

    await parse(user)
    await user.click(parseButton())
    await user.dblClick(parseButton())
    fireEvent.submit(intentPanel())
    fireEvent.submit(intentPanel())

    expect(pending.fetch).toHaveBeenCalledTimes(1)
    await act(async () => pending.resolve(jsonResponse(200, makeParsedIntent())))
    await summary()
    expect(pending.fetch).toHaveBeenCalledTimes(1)
  })

  it('cannot parse while a risk assessment is running', async () => {
    const pending = deferredFetch()
    const { user } = setup()
    await user.type(requestBox(), EXAMPLE_REQUEST)
    await user.type(field.merchant(), VALID_DETAILS.merchant_name)
    await user.type(field.amount(), VALID_DETAILS.amount)
    await user.type(field.description(), VALID_DETAILS.description)
    await user.type(field.purpose(), VALID_DETAILS.purpose)

    await user.click(assessButton())
    expect(parseButton()).toBeDisabled()
    fireEvent.submit(intentPanel())

    expect(calledUrls(pending.fetch)).toEqual([API_URL])
    await act(async () => pending.resolve(jsonResponse(200, makeAssessment())))
    await within(resultPanel()).findByText('ALLOW')
    expect(parseButton()).toBeEnabled()
  })
})
