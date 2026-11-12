/**
 * Payment form values and client-side input validation.
 *
 * These checks mirror the backend's *format* constraints (required fields,
 * lengths, amount format) purely for faster feedback. They contain no risk
 * logic; the backend re-validates everything and remains authoritative.
 */

import type { ParsedPaymentIntent } from '../api/intent'
import { CURRENCY_OPTIONS, type TransactionDetails } from '../api/risk'

export type PaymentField = keyof TransactionDetails
export type PaymentFormValues = Record<PaymentField, string>
export type PaymentFormErrors = Partial<Record<PaymentField, string>>

export const PAYMENT_FIELDS: readonly PaymentField[] = [
  'merchant_name',
  'amount',
  'currency',
  'description',
  'purpose',
]

export const EMPTY_PAYMENT_FORM: PaymentFormValues = {
  merchant_name: '',
  amount: '',
  currency: 'USD',
  description: '',
  purpose: '',
}

export const FIELD_LABELS: Record<PaymentField, string> = {
  merchant_name: 'Merchant name',
  amount: 'Amount',
  currency: 'Currency',
  description: 'Description',
  purpose: 'Payment purpose',
}

/** Backend limits from TransactionDetails (ShortText=200, LongText=1000, max_digits=14, 2 dp). */
export const LIMITS = {
  merchant_name: 200,
  description: 1000,
  purpose: 200,
  amountIntegerDigits: 12,
} as const

const AMOUNT_PATTERN = /^\d+(\.\d{1,2})?$/

function validateText(value: string, label: string, max: number): string | undefined {
  const trimmed = value.trim()
  if (!trimmed) return `${label} is required.`
  if (trimmed.length > max) return `${label} must be at most ${max} characters.`
  return undefined
}

function validateAmount(value: string): string | undefined {
  const trimmed = value.trim()
  if (!trimmed) return 'Amount is required.'
  if (!AMOUNT_PATTERN.test(trimmed)) return 'Enter a number like 49.99, with up to 2 decimal places.'
  const [integerPart] = trimmed.split('.')
  if (/^0*$/.test(trimmed.replace('.', ''))) return 'Amount must be greater than 0.'
  if (integerPart.replace(/^0+/, '').length > LIMITS.amountIntegerDigits) return 'Amount is too large.'
  return undefined
}

export function validatePaymentForm(values: PaymentFormValues): PaymentFormErrors {
  const errors: PaymentFormErrors = {}
  const set = (field: PaymentField, message: string | undefined) => {
    if (message) errors[field] = message
  }
  set('merchant_name', validateText(values.merchant_name, 'Merchant name', LIMITS.merchant_name))
  set('amount', validateAmount(values.amount))
  set(
    'currency',
    (CURRENCY_OPTIONS as readonly string[]).includes(values.currency) ? undefined : 'Select a currency.',
  )
  set('description', validateText(values.description, 'Description', LIMITS.description))
  set('purpose', validateText(values.purpose, 'Payment purpose', LIMITS.purpose))
  return errors
}

/** Build the exact request contract from form values (trimmed). */
export function toTransactionDetails(values: PaymentFormValues): TransactionDetails {
  return {
    merchant_name: values.merchant_name.trim(),
    amount: values.amount.trim(),
    currency: values.currency,
    description: values.description.trim(),
    purpose: values.purpose.trim(),
  }
}

/** Keep only backend field errors that belong to form fields. */
export function pickFieldErrors(errors: Record<string, string>): PaymentFormErrors {
  const picked: PaymentFormErrors = {}
  for (const field of PAYMENT_FIELDS) {
    if (errors[field]) picked[field] = errors[field]
  }
  return picked
}

export function isSupportedCurrency(currency: string): boolean {
  return (CURRENCY_OPTIONS as readonly string[]).includes(currency)
}

/** Shown when a currency is not one the risk endpoint can assess. Never converted automatically. */
export function unsupportedCurrencyMessage(currency: string): string {
  return (
    `${currency} is not supported for risk assessment. Supported currencies: ${CURRENCY_OPTIONS.join(', ')}. ` +
    'Amounts are never converted between currencies.'
  )
}

/** Currency error for the current value: unsupported codes get an explicit explanation. */
export function currencyError(currency: string): string | undefined {
  if (!currency) return 'Select a currency.'
  return isSupportedCurrency(currency) ? undefined : unsupportedCurrencyMessage(currency)
}

export const MISSING_FROM_REQUEST_MESSAGE = 'Not found in your request. Enter it before assessing risk.'

/**
 * Turn an AI-extracted intent into editable form values.
 * Missing details stay empty: nothing is invented, defaulted or converted.
 */
export function fromParsedIntent(intent: ParsedPaymentIntent): PaymentFormValues {
  return {
    merchant_name: intent.merchant_name ?? '',
    amount: intent.amount ?? '',
    currency: intent.currency ?? '',
    description: intent.description ?? '',
    purpose: intent.purpose ?? '',
  }
}

/** Field errors to show right after parsing: missing details and unsupported currencies. */
export function parsedIntentErrors(intent: ParsedPaymentIntent): PaymentFormErrors {
  const errors: PaymentFormErrors = {}
  for (const field of intent.missing_fields) errors[field] = MISSING_FROM_REQUEST_MESSAGE
  if (intent.currency !== null && !isSupportedCurrency(intent.currency)) {
    errors.currency = unsupportedCurrencyMessage(intent.currency)
  }
  return errors
}
