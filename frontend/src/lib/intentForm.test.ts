import { describe, expect, it } from 'vitest'
import { makeCompleteIntent, makeParsedIntent } from '../test/http'
import {
  MISSING_FROM_REQUEST_MESSAGE,
  currencyError,
  fromParsedIntent,
  isSupportedCurrency,
  parsedIntentErrors,
  unsupportedCurrencyMessage,
} from './paymentForm'

describe('fromParsedIntent', () => {
  it('copies extracted details into form values unchanged', () => {
    expect(fromParsedIntent(makeCompleteIntent())).toEqual({
      merchant_name: 'ABC Technologies',
      amount: '250',
      currency: 'USD',
      description: 'software services',
      purpose: 'Q3 tooling budget',
    })
  })

  it('leaves missing details empty instead of inventing or defaulting them', () => {
    const values = fromParsedIntent(
      makeParsedIntent({
        amount: null,
        currency: null,
        missing_fields: ['amount', 'currency', 'purpose'],
      }),
    )
    expect(values.amount).toBe('')
    expect(values.currency).toBe('') // not defaulted to USD
    expect(values.purpose).toBe('')
  })

  it('keeps an unsupported currency as stated (no conversion)', () => {
    const values = fromParsedIntent(makeParsedIntent({ amount: '5000', currency: 'JPY' }))
    expect(values).toMatchObject({ amount: '5000', currency: 'JPY' })
  })

  it('keeps INR as stated', () => {
    const values = fromParsedIntent(makeParsedIntent({ amount: '750000', currency: 'INR' }))
    expect(values).toMatchObject({ amount: '750000', currency: 'INR' })
  })
})

describe('parsedIntentErrors', () => {
  it('flags each missing field', () => {
    const errors = parsedIntentErrors(
      makeParsedIntent({ merchant_name: null, missing_fields: ['merchant_name', 'purpose'] }),
    )
    expect(errors).toEqual({
      merchant_name: MISSING_FROM_REQUEST_MESSAGE,
      purpose: MISSING_FROM_REQUEST_MESSAGE,
    })
  })

  it('flags an unsupported currency', () => {
    const errors = parsedIntentErrors(makeCompleteIntent({ currency: 'JPY' }))
    expect(errors).toEqual({ currency: unsupportedCurrencyMessage('JPY') })
  })

  it('does not flag INR', () => {
    expect(parsedIntentErrors(makeCompleteIntent({ amount: '750000', currency: 'INR' }))).toEqual({})
  })

  it('returns no errors for a complete, supported intent', () => {
    expect(parsedIntentErrors(makeCompleteIntent())).toEqual({})
  })
})

describe('currency checks', () => {
  it.each(['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'INR'])('supports %s', (code) => {
    expect(isSupportedCurrency(code)).toBe(true)
    expect(currencyError(code)).toBeUndefined()
  })

  it.each(['JPY', 'CNY', 'inr', 'usd', 'XYZ'])('does not support %s', (code) => {
    expect(isSupportedCurrency(code)).toBe(false)
    expect(currencyError(code)).toBe(unsupportedCurrencyMessage(code))
  })

  it('asks for a currency when none is set', () => {
    expect(currencyError('')).toBe('Select a currency.')
  })

  it('explains that unsupported currencies are not converted', () => {
    expect(unsupportedCurrencyMessage('JPY')).toBe(
      'JPY is not supported for risk assessment. Supported currencies: USD, EUR, GBP, CAD, AUD, INR. ' +
        'Amounts are never converted between currencies.',
    )
  })
})
