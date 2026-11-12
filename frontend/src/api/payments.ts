/**
 * Payment workflow API (backend: app/api/routes/payments.py).
 *
 * Types mirror the backend Pydantic contracts in app/schemas/payments.py.
 * The backend decides everything: risk, status and allowed actions. This
 * module only transports requests and validates responses.
 *
 * Reviewer token: required for reads (list/get) and reviewer actions. Passed per
 * call, sent ONLY as `Authorization: Bearer`, never in URLs or bodies, never
 * stored or logged here.
 */

import { ApiError, getJson, postJson, type RequestOptions } from './client'
import type { PolicyDecision, RiskLevel, TransactionDetails } from './risk'

export const PAYMENTS_PATH = '/api/v1/payments'
export const MAX_PAGE_SIZE = 100
const SERVICE = 'payment service'
/** Payment calls can include PayPal round trips (backend PayPal timeout 15 s + OAuth). */
export const PAYMENT_TIMEOUT_MS = 45_000

export const PAYMENT_STATUSES = [
  'BLOCKED',
  'PENDING_REVIEW',
  'REJECTED',
  'APPROVED_FOR_PAYMENT',
  'AWAITING_BUYER_APPROVAL',
  'BUYER_APPROVED',
  'AUTHORIZATION_PENDING',
  'AUTHORIZED',
  'PENDING_CAPTURE',
  'CAPTURE_PENDING',
  'CAPTURED',
  'PENDING_VOID',
  'VOIDED',
  'FAILED',
] as const
export type PaymentStatus = (typeof PAYMENT_STATUSES)[number]

export type ClientAction = 'create_order' | 'authorize'
export type ReviewerActionName = 'approve' | 'reject' | 'capture' | 'void'

export interface PaymentRisk {
  risk_score: number
  risk_level: RiskLevel
  decision: PolicyDecision
  reasons: string[]
  assessed_at: string
}

export interface PaymentPayPal {
  order_id: string | null
  approval_url: string | null
  order_status: string | null
  authorization_id: string | null
  authorization_status: string | null
  capture_id: string | null
  capture_status: string | null
}

export interface PaymentReview {
  decision: 'APPROVE' | 'REJECT'
  reviewer_name: string | null
  comment: string | null
  decided_at: string
}

export interface ReviewerActionLog {
  action: string
  reviewer_name: string | null
  comment: string | null
  at: string
}

export interface PaymentLastError {
  code: string
  message: string
  retry_safe: boolean
  at: string
}

/** Backend `PaymentRecordResponse`. */
export interface Payment {
  transaction_id: string
  status: PaymentStatus
  merchant_name: string
  amount: string
  currency: string
  description: string
  purpose: string
  risk: PaymentRisk
  paypal: PaymentPayPal
  review: PaymentReview | null
  reviewer_actions: ReviewerActionLog[]
  last_error: PaymentLastError | null
  next_actions: string[]
  created_at: string
  updated_at: string
}

/**
 * Backend `PaymentActionResponse`: the minimal view returned by the UNAUTHENTICATED
 * buyer/agent actions (create, order retry, authorize). No reviewer, risk or PayPal ID data.
 */
export interface PaymentAction {
  transaction_id: string
  status: PaymentStatus
  merchant_name: string
  amount: string
  currency: string
  approval_url: string | null
  next_actions: string[]
  last_error: { code: string; retry_safe: boolean } | null
}

/** Backend `PaymentListResponse`. */
export interface PaymentList {
  items: Payment[]
  total: number
  limit: number
  offset: number
}

/** Backend `ReviewerActionRequest` (both fields optional). */
export interface ReviewerActionInput {
  reviewer_name?: string
  comment?: string
}

// ------------------------------------------------------------------ validation

const RISK_LEVELS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
const DECISIONS = ['ALLOW', 'REVIEW', 'BLOCK']
const TRANSACTION_ID = /^[A-Za-z0-9_-]{1,64}$/
const AMOUNT = /^\d+(\.\d{1,2})?$/
const PAYPAL_APPROVAL_HOST = 'www.sandbox.paypal.com'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const isString = (v: unknown): v is string => typeof v === 'string'
const isNullableString = (v: unknown): v is string | null => v === null || typeof v === 'string'
const isDate = (v: unknown): v is string => typeof v === 'string' && !Number.isNaN(Date.parse(v))

/** Only https links to the PayPal Sandbox checkout host are ever shown to users. */
export function isSandboxApprovalUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.hostname === PAYPAL_APPROVAL_HOST && !url.username && !url.port
  } catch {
    return false
  }
}

function fail(what: string): never {
  throw new ApiError('invalid_response', `The ${SERVICE} returned an unexpected response (${what}).`)
}

export function parsePayment(data: unknown): Payment {
  if (!isRecord(data)) fail('not an object')
  const d = data

  if (!isString(d.transaction_id) || !TRANSACTION_ID.test(d.transaction_id)) fail('transaction_id')
  if (!PAYMENT_STATUSES.includes(d.status as PaymentStatus)) fail('status')
  for (const key of ['merchant_name', 'currency', 'description', 'purpose'] as const) {
    if (!isString(d[key])) fail(key)
  }
  if (!isString(d.amount) || !AMOUNT.test(d.amount)) fail('amount')
  if (!isDate(d.created_at) || !isDate(d.updated_at)) fail('timestamps')

  const risk = d.risk
  if (
    !isRecord(risk) ||
    typeof risk.risk_score !== 'number' ||
    !Number.isInteger(risk.risk_score) ||
    risk.risk_score < 0 ||
    risk.risk_score > 100 ||
    !RISK_LEVELS.includes(risk.risk_level as string) ||
    !DECISIONS.includes(risk.decision as string) ||
    !Array.isArray(risk.reasons) ||
    !risk.reasons.every(isString) ||
    !isDate(risk.assessed_at)
  ) {
    fail('risk')
  }

  const paypal = d.paypal
  const paypalKeys = ['order_id', 'approval_url', 'order_status', 'authorization_id',
    'authorization_status', 'capture_id', 'capture_status'] as const
  if (!isRecord(paypal) || !paypalKeys.every((k) => isNullableString(paypal[k]))) fail('paypal')
  if (paypal.approval_url !== null && !isSandboxApprovalUrl(paypal.approval_url as string)) {
    fail('approval_url')
  }

  const review = d.review
  if (
    review !== null &&
    (!isRecord(review) ||
      (review.decision !== 'APPROVE' && review.decision !== 'REJECT') ||
      !isNullableString(review.reviewer_name) ||
      !isNullableString(review.comment) ||
      !isDate(review.decided_at))
  ) {
    fail('review')
  }

  if (
    !Array.isArray(d.reviewer_actions) ||
    !d.reviewer_actions.every(
      (a) => isRecord(a) && isString(a.action) && isNullableString(a.reviewer_name) &&
        isNullableString(a.comment) && isDate(a.at),
    )
  ) {
    fail('reviewer_actions')
  }

  const lastError = d.last_error
  if (
    lastError !== null &&
    (!isRecord(lastError) || !isString(lastError.code) || !isString(lastError.message) ||
      typeof lastError.retry_safe !== 'boolean' || !isDate(lastError.at))
  ) {
    fail('last_error')
  }

  if (!Array.isArray(d.next_actions) || !d.next_actions.every(isString)) fail('next_actions')

  return {
    transaction_id: d.transaction_id as string,
    status: d.status as PaymentStatus,
    merchant_name: d.merchant_name as string,
    amount: d.amount as string,
    currency: d.currency as string,
    description: d.description as string,
    purpose: d.purpose as string,
    risk: {
      risk_score: (risk as Record<string, unknown>).risk_score as number,
      risk_level: (risk as Record<string, unknown>).risk_level as RiskLevel,
      decision: (risk as Record<string, unknown>).decision as PolicyDecision,
      reasons: [...((risk as Record<string, unknown>).reasons as string[])],
      assessed_at: (risk as Record<string, unknown>).assessed_at as string,
    },
    paypal: Object.fromEntries(paypalKeys.map((k) => [k, (paypal as Record<string, unknown>)[k]])) as unknown as PaymentPayPal,
    review: review as PaymentReview | null,
    reviewer_actions: d.reviewer_actions as ReviewerActionLog[],
    last_error: lastError as PaymentLastError | null,
    next_actions: [...(d.next_actions as string[])],
    created_at: d.created_at as string,
    updated_at: d.updated_at as string,
  }
}

const ACTION_KEYS = ['transaction_id', 'status', 'merchant_name', 'amount', 'currency',
  'approval_url', 'next_actions', 'last_error'] as const
const CLIENT_ACTIONS = ['create_order', 'buyer_approves_on_paypal', 'authorize']

/**
 * Validate the minimal action view. Any field beyond the allowlist (e.g. risk or
 * reviewer data) makes the response invalid rather than being silently shown.
 */
export function parsePaymentAction(data: unknown): PaymentAction {
  if (!isRecord(data)) fail('not an object')
  const d = data
  const keys = Object.keys(d)
  if (keys.length !== ACTION_KEYS.length || !ACTION_KEYS.every((k) => k in d)) fail('action fields')
  if (!isString(d.transaction_id) || !TRANSACTION_ID.test(d.transaction_id)) fail('transaction_id')
  if (!PAYMENT_STATUSES.includes(d.status as PaymentStatus)) fail('status')
  if (!isString(d.merchant_name) || !isString(d.currency)) fail('payment')
  if (!isString(d.amount) || !AMOUNT.test(d.amount)) fail('amount')
  if (d.approval_url !== null && (!isString(d.approval_url) || !isSandboxApprovalUrl(d.approval_url))) {
    fail('approval_url')
  }
  if (!Array.isArray(d.next_actions) || !d.next_actions.every((a) => isString(a) && CLIENT_ACTIONS.includes(a))) {
    fail('next_actions')
  }
  const err = d.last_error
  if (err !== null && (!isRecord(err) || Object.keys(err).length !== 2 || !isString(err.code) ||
      typeof err.retry_safe !== 'boolean')) {
    fail('last_error')
  }
  return {
    transaction_id: d.transaction_id as string,
    status: d.status as PaymentStatus,
    merchant_name: d.merchant_name as string,
    amount: d.amount as string,
    currency: d.currency as string,
    approval_url: d.approval_url as string | null,
    next_actions: [...(d.next_actions as string[])],
    last_error: err === null ? null : { code: (err as Record<string, unknown>).code as string,
      retry_safe: (err as Record<string, unknown>).retry_safe as boolean },
  }
}

export function parsePaymentList(data: unknown): PaymentList {
  if (!isRecord(data) || !Array.isArray(data.items)) fail('list')
  const { total, limit, offset } = data
  for (const n of [total, limit, offset]) {
    if (typeof n !== 'number' || !Number.isInteger(n) || n < 0) fail('pagination')
  }
  return {
    items: (data.items as unknown[]).map(parsePayment),
    total: total as number,
    limit: limit as number,
    offset: offset as number,
  }
}

// ---------------------------------------------------------------- error copy

/** Fixed, user-facing messages per backend error code. */
const ERROR_MESSAGES: Record<string, string> = {
  PAYMENT_NOT_FOUND: 'This payment was not found. It may have been lost when the backend restarted.',
  PAYMENT_INVALID_STATE: "This action isn't allowed for the payment's current status.",
  PAYMENT_OPERATION_IN_PROGRESS: 'Another action on this payment is in progress. Try again shortly.',
  PAYMENT_BUYER_APPROVAL_REQUIRED:
    'PayPal does not report this order as approved by the buyer yet. Approve it on PayPal first.',
  PAYPAL_CURRENCY_NOT_SUPPORTED:
    'This currency cannot be sent to PayPal yet. Amounts are never converted to another currency.',
  PAYPAL_NOT_CONFIGURED: 'PayPal is not configured on the server.',
  PAYPAL_AUTHENTICATION_FAILED: 'The server could not authenticate with PayPal.',
  PAYPAL_TIMEOUT: 'PayPal did not respond in time.',
  PAYPAL_UNAVAILABLE: 'PayPal is not reachable right now.',
  PAYPAL_RATE_LIMITED: 'PayPal is rate limiting requests. Wait a moment.',
  PAYPAL_BAD_RESPONSE: 'PayPal returned an unexpected response.',
  PAYPAL_PAYMENT_DECLINED: 'PayPal declined the payment.',
  PAYPAL_REQUEST_REJECTED: 'PayPal rejected this request.',
  PAYPAL_INVALID_REQUEST: 'The payment request is not valid for PayPal.',
  REVIEWER_AUTH_REQUIRED: 'Enter the reviewer token to perform reviewer actions.',
  REVIEWER_AUTH_INVALID: 'The reviewer token was rejected.',
  REVIEWER_AUTH_NOT_CONFIGURED:
    'Reviewer actions are disabled because reviewer authentication is not configured on the server.',
}

export function paymentErrorMessage(code: string | undefined): string | undefined {
  return code ? ERROR_MESSAGES[code] : undefined
}

function friendly(error: ApiError): ApiError {
  const known = paymentErrorMessage(error.code)
  if (!known) return error
  let message = known
  if (error.details.status && error.code === 'PAYMENT_INVALID_STATE') {
    message += ` (Current status: ${error.details.status}.)`
  }
  if (error.details.retry_safe === true) message += ' It is safe to try the same action again.'
  return new ApiError(error.kind, message, {
    status: error.status,
    code: error.code,
    details: error.details,
    fieldErrors: error.fieldErrors,
  })
}

/** Runs `request` lazily so every failure (including path validation) becomes a rejection. */
async function call<T>(request: () => Promise<unknown>, parse: (d: unknown) => T): Promise<T> {
  let data: unknown
  try {
    data = await request()
  } catch (err) {
    throw err instanceof ApiError ? friendly(err) : err
  }
  return parse(data)
}

function opts(options: RequestOptions = {}): RequestOptions {
  return { timeoutMs: PAYMENT_TIMEOUT_MS, service: SERVICE, ...options }
}

function paymentPath(transactionId: string, action?: string): string {
  if (!TRANSACTION_ID.test(transactionId)) {
    throw new ApiError('invalid_response', 'Invalid transaction ID.')
  }
  return `${PAYMENTS_PATH}/${transactionId}${action ? `/${action}` : ''}`
}

// ----------------------------------------------------------------- endpoints

/** POST /api/v1/payments: only the five TransactionDetails fields are sent. Minimal response. */
export function createPayment(details: TransactionDetails, options?: RequestOptions): Promise<PaymentAction> {
  const payload: TransactionDetails = {
    merchant_name: details.merchant_name,
    amount: details.amount,
    currency: details.currency,
    description: details.description,
    purpose: details.purpose,
  }
  return call(() => postJson(PAYMENTS_PATH, payload, opts(options)), parsePaymentAction)
}

/** Options with the reviewer token added as the Authorization header (never in the URL). */
function withReviewer(reviewerToken: string, options?: RequestOptions): RequestOptions {
  return { ...opts(options), headers: { ...options?.headers, Authorization: `Bearer ${reviewerToken}` } }
}

/** Reviewer read: payment records include reviewer names and comments. */
export function listPayments(
  page: { limit: number; offset: number },
  reviewerToken: string,
  options?: RequestOptions,
): Promise<PaymentList> {
  const limit = Math.min(Math.max(Math.trunc(page.limit), 1), MAX_PAGE_SIZE)
  const offset = Math.max(Math.trunc(page.offset), 0)
  return call(
    () => getJson(`${PAYMENTS_PATH}?limit=${limit}&offset=${offset}`, withReviewer(reviewerToken, options)),
    parsePaymentList,
  )
}

/** Reviewer read of one payment. */
export function getPayment(transactionId: string, reviewerToken: string, options?: RequestOptions): Promise<Payment> {
  return call(() => getJson(paymentPath(transactionId), withReviewer(reviewerToken, options)), parsePayment)
}

/** Client action: create (or safely retry) the PayPal order. No body, no token. Minimal response. */
export function createPaymentOrder(transactionId: string, options?: RequestOptions): Promise<PaymentAction> {
  return call(() => postJson(paymentPath(transactionId, 'order'), undefined, opts(options)), parsePaymentAction)
}

/** Client action: the backend checks buyer approval with PayPal, then authorizes. Minimal response. */
export function authorizePayment(transactionId: string, options?: RequestOptions): Promise<PaymentAction> {
  return call(() => postJson(paymentPath(transactionId, 'authorize'), undefined, opts(options)), parsePaymentAction)
}

/** Reviewer action. The token goes only into the Authorization header. */
export function reviewerAction(
  transactionId: string,
  action: ReviewerActionName,
  reviewerToken: string,
  input: ReviewerActionInput = {},
  options?: RequestOptions,
): Promise<Payment> {
  const body: ReviewerActionInput = {}
  const name = input.reviewer_name?.trim()
  const comment = input.comment?.trim()
  if (name) body.reviewer_name = name
  if (comment) body.comment = comment
  return call(() => postJson(paymentPath(transactionId, action), body, withReviewer(reviewerToken, options)), parsePayment)
}
