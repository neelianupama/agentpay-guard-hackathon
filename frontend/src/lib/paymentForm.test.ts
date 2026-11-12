import { describe, expect, it } from 'vitest'
import { VALID_DETAILS } from '../test/http'
import {
  EMPTY_PAYMENT_FORM,
  pickFieldErrors,
  toTransactionDetails,
  validatePaymentForm,
} from './paymentForm'

describe('validatePaymentForm', () => {
  it('accepts a complete, valid form', () => {
    expect(validatePaymentForm(VALID_DETAILS)).toEqual({})
  })

  it('requires every text field and the amount', () => {
    expect(validatePaymentForm(EMPTY_PAYMENT_FORM)).toEqual({
      merchant_name: 'Merchant name is required.',
      amount: 'Amount is required.',
      description: 'Description is required.',
      purpose: 'Payment purpose is required.',
    })
  })

  it('treats whitespace-only values as missing', () => {
    const errors = validatePaymentForm({ ...VALID_DETAILS, merchant_name: '   ', purpose: '\t' })
    expect(errors.merchant_name).toBe('Merchant name is required.')
    expect(errors.purpose).toBe('Payment purpose is required.')
  })

  it.each([
    ['0', 'Amount must be greater than 0.'],
    ['0.00', 'Amount must be greater than 0.'],
    ['-5', 'Enter a number like 49.99, with up to 2 decimal places.'],
    ['1.005', 'Enter a number like 49.99, with up to 2 decimal places.'],
    ['abc', 'Enter a number like 49.99, with up to 2 decimal places.'],
    ['1e5', 'Enter a number like 49.99, with up to 2 decimal places.'],
    ['1,000', 'Enter a number like 49.99, with up to 2 decimal places.'],
    ['1234567890123', 'Amount is too large.'],
  ])('rejects amount %s', (amount, message) => {
    expect(validatePaymentForm({ ...VALID_DETAILS, amount }).amount).toBe(message)
  })

  it.each(['0.01', '49.99', '2000', '10000.5', '999999999999.99'])('accepts amount %s', (amount) => {
    expect(validatePaymentForm({ ...VALID_DETAILS, amount }).amount).toBeUndefined()
  })

  it('rejects a currency outside the offered options', () => {
    expect(validatePaymentForm({ ...VALID_DETAILS, currency: 'JPY' }).currency).toBe('Select a currency.')
  })

  it('enforces backend length limits', () => {
    const errors = validatePaymentForm({
      ...VALID_DETAILS,
      merchant_name: 'x'.repeat(201),
      description: 'x'.repeat(1001),
      purpose: 'x'.repeat(201),
    })
    expect(errors.merchant_name).toBe('Merchant name must be at most 200 characters.')
    expect(errors.description).toBe('Description must be at most 1000 characters.')
    expect(errors.purpose).toBe('Payment purpose must be at most 200 characters.')
  })
})

describe('toTransactionDetails', () => {
  it('trims values and returns exactly the five contract fields', () => {
    const details = toTransactionDetails({
      ...VALID_DETAILS,
      merchant_name: '  Acme Cloud ',
      amount: ' 49.99 ',
    })
    expect(details).toEqual(VALID_DETAILS)
    expect(Object.keys(details).sort()).toEqual(['amount', 'currency', 'description', 'merchant_name', 'purpose'])
  })
})

describe('pickFieldErrors', () => {
  it('keeps only errors for form fields', () => {
    expect(pickFieldErrors({ amount: 'bad', _form: 'x', risk_score: 'y' })).toEqual({ amount: 'bad' })
  })
})
