/**
 * Regression: on narrow screens (single-column layout, < 1100px) the risk
 * result panel sits below the forms. Clicking "Assess Risk" returned HTTP 200
 * and rendered the result, but entirely off-screen, so the user saw no change.
 * The panel must be brought into view when a result or error arrives.
 */
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { VALID_DETAILS, deferredFetch, jsonResponse, makeAssessment, respondWith } from '../test/http'
import { NewPaymentPage } from './NewPaymentPage'

const resultPanel = () => screen.getByRole('region', { name: 'Risk assessment' })

let scrollIntoView: Mock

beforeEach(() => {
  // jsdom does not implement scrollIntoView; install a spy for these tests.
  scrollIntoView = vi.fn()
  Element.prototype.scrollIntoView = scrollIntoView
})

afterEach(() => {
  delete (Element.prototype as Partial<Element>).scrollIntoView
})

/** Lay the result panel out at `top` px from the viewport top (innerHeight is 768 in jsdom). */
function placeResultPanelAt(top: number) {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const y = this.classList.contains('risk-result') ? top : 0
    return { x: 0, y, top: y, left: 0, right: 600, bottom: y + 600, width: 600, height: 600, toJSON: () => ({}) }
  })
}

async function fillAndAssess() {
  const user = userEvent.setup()
  render(<NewPaymentPage />)
  await user.type(screen.getByLabelText('Merchant name'), VALID_DETAILS.merchant_name)
  await user.type(screen.getByLabelText('Amount'), VALID_DETAILS.amount)
  await user.type(screen.getByLabelText('Description'), VALID_DETAILS.description)
  await user.type(screen.getByLabelText('Payment purpose'), VALID_DETAILS.purpose)
  await user.click(screen.getByRole('button', { name: 'Assess Risk' }))
}

describe('NewPaymentPage: risk result visibility', () => {
  it('scrolls an off-screen result into view (narrow layout, as measured at 1024px)', async () => {
    placeResultPanelAt(1026)
    respondWith(200, makeAssessment())

    await fillAndAssess()

    expect(await within(resultPanel()).findByText('ALLOW')).toBeInTheDocument()
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' })
    expect(scrollIntoView.mock.contexts[0]).toBe(resultPanel())
  })

  it('also reveals an off-screen error', async () => {
    placeResultPanelAt(1026)
    respondWith(500, { detail: 'Internal Server Error' })

    await fillAndAssess()

    await within(resultPanel()).findByRole('alert')
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('does not scroll while idle or loading, only once the result arrives', async () => {
    placeResultPanelAt(1026)
    const pending = deferredFetch()

    await fillAndAssess()
    expect(within(resultPanel()).getByRole('status')).toHaveTextContent('Assessing risk…')
    expect(scrollIntoView).not.toHaveBeenCalled()

    await act(async () => pending.resolve(jsonResponse(200, makeAssessment())))
    await within(resultPanel()).findByText('ALLOW')
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('does not scroll when the result is already visible (wide layout)', async () => {
    placeResultPanelAt(129)
    respondWith(200, makeAssessment())

    await fillAndAssess()

    await within(resultPanel()).findByText('ALLOW')
    expect(scrollIntoView).not.toHaveBeenCalled()
  })

  it('scrolls back up to a result that is above the viewport', async () => {
    placeResultPanelAt(-400)
    respondWith(200, makeAssessment())

    await fillAndAssess()

    await within(resultPanel()).findByText('ALLOW')
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('jumps without animation when the user prefers reduced motion', async () => {
    placeResultPanelAt(1026)
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduce'), media: query }))
    respondWith(200, makeAssessment())

    await fillAndAssess()

    await within(resultPanel()).findByText('ALLOW')
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'start' })
  })
})
