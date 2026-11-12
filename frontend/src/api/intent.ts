/**
 * Payment intent parsing API: POST /api/v1/intent/parse.
 *
 * Types mirror the backend Pydantic contracts (backend/app/schemas):
 * request = PaymentIntentRequest, response = ParsedPaymentIntent.
 *
 * Parsing only extracts payment details from text. It never assesses risk,
 * and its response can never carry a risk score or decision: any response
 * with fields beyond the contract is rejected as invalid.
 */

import { ApiError, postJson, type RequestOptions } from './client'
import type { TransactionDetails } from './risk'

export const INTENT_PARSE_PATH = '/api/v1/intent/parse'

/** Backend `PaymentIntentRequest.request_text` limit (LongText). */
export const REQUEST_TEXT_MAX_LENGTH = 1000

/**
 * The backend allows ~20 s per provider attempt plus one retry, so parsing
 * gets a longer client timeout than risk assessment.
 */
export const INTENT_TIMEOUT_MS = 50_000

export type PaymentDetailField = keyof TransactionDetails

/** Backend `PaymentDetailField`, in canonical order. */
export const PAYMENT_DETAIL_FIELDS: readonly PaymentDetailField[] = [
  'merchant_name',
  'amount',
  'currency',
  'description',
  'purpose',
]

/** Backend `ParsedPaymentIntent`. A detail is `null` when it was not found. */
export interface ParsedPaymentIntent {
  merchant_name: string | null
  amount: string | null
  currency: string | null
  description: string | null
  purpose: string | null
  missing_fields: PaymentDetailField[]
  clarification_needed: boolean
}

const RESPONSE_KEYS = [...PAYMENT_DETAIL_FIELDS, 'missing_fields', 'clarification_needed']

/** Fixed, user-facing messages per backend error code. Backend/provider text is never shown. */
const ERROR_MESSAGES: Record<string, string> = {
  INTENT_RATE_LIMITED: 'Too many parsing requests right now. Wait a moment and try again.',
  INTENT_TIMEOUT: 'The AI took too long to read the request. Try again, or enter the details manually.',
  INTENT_SERVICE_UNAVAILABLE: 'AI parsing is not available right now. You can enter the details manually.',
  INTENT_MODEL_UNAVAILABLE: 'AI parsing is not available right now. You can enter the details manually.',
  INTENT_EXTRACTION_FAILED:
    'The AI could not read this request. Try rephrasing it, or enter the details manually.',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Validate that an API response matches the ParsedPaymentIntent contract exactly. */
export function parseParsedPaymentIntent(data: unknown): ParsedPaymentIntent {
  const fail = (what: string): never => {
    throw new ApiError('invalid_response', `The AI parsing service returned an unexpected response (${what}).`)
  }
  if (!isRecord(data)) return fail('not an object')

  const keys = Object.keys(data)
  if (keys.length !== RESPONSE_KEYS.length || !RESPONSE_KEYS.every((k) => k in data)) {
    return fail('fields')
  }
  for (const field of PAYMENT_DETAIL_FIELDS) {
    const value = data[field]
    if (value !== null && (typeof value !== 'string' || value.trim() === '')) return fail(field)
  }
  const { missing_fields, clarification_needed } = data
  if (typeof clarification_needed !== 'boolean') return fail('clarification_needed')

  // missing_fields must list exactly the null details, in canonical order.
  const expectedMissing = PAYMENT_DETAIL_FIELDS.filter((f) => data[f] === null)
  if (
    !Array.isArray(missing_fields) ||
    missing_fields.length !== expectedMissing.length ||
    !expectedMissing.every((f, i) => missing_fields[i] === f)
  ) {
    return fail('missing_fields')
  }
  if (expectedMissing.length > 0 && !clarification_needed) return fail('clarification_needed')

  return {
    merchant_name: data.merchant_name as string | null,
    amount: data.amount as string | null,
    currency: data.currency as string | null,
    description: data.description as string | null,
    purpose: data.purpose as string | null,
    missing_fields: expectedMissing,
    clarification_needed,
  }
}

function friendlyError(error: ApiError): ApiError {
  if (error.kind === 'http') {
    const message =
      (error.code && ERROR_MESSAGES[error.code]) ||
      `AI parsing failed (HTTP ${error.status ?? 'error'}). You can enter the details manually.`
    return new ApiError('http', message, { status: error.status, code: error.code })
  }
  if (error.kind === 'timeout') {
    return new ApiError('timeout', ERROR_MESSAGES.INTENT_TIMEOUT)
  }
  return error
}

/**
 * Ask the backend to extract payment details from natural-language text.
 *
 * Only `request_text` is sent. The result is a draft for the user to review;
 * it is never sent for risk assessment automatically.
 */
export async function parsePaymentIntent(
  requestText: string,
  options: RequestOptions = {},
): Promise<ParsedPaymentIntent> {
  let data: unknown
  try {
    data = await postJson(
      INTENT_PARSE_PATH,
      { request_text: requestText },
      { timeoutMs: INTENT_TIMEOUT_MS, service: 'AI parsing service', ...options },
    )
  } catch (err) {
    throw err instanceof ApiError ? friendlyError(err) : err
  }
  return parseParsedPaymentIntent(data)
}
