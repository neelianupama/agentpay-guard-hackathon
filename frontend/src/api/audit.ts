/**
 * Audit trail API: GET /api/v1/audit/events (reviewer token required).
 *
 * Types mirror backend app/schemas/audit.py and app/services/audit/events.py.
 * Events use a strict allowlist: any unknown field (for example free-text
 * comments) makes the response invalid instead of being displayed.
 */

import { ApiError, getJson, type RequestOptions } from './client'
import { paymentErrorMessage } from './payments'

export const AUDIT_EVENTS_PATH = '/api/v1/audit/events'
export const AUDIT_MAX_LIMIT = 100
export const AUDIT_MAX_OFFSET = 9_999
const SERVICE = 'audit service'

export const AUDIT_EVENT_TYPES = [
  'RISK_ASSESSED',
  'PAYMENT_CREATED',
  'PAYMENT_STATUS_CHANGED',
  'REVIEW_APPROVED',
  'REVIEW_REJECTED',
  'PAYPAL_ORDER_CREATED',
  'PAYPAL_AUTHORIZATION',
  'PAYPAL_CAPTURE',
  'PAYPAL_VOID',
  'PAYPAL_OPERATION_FAILED',
] as const
export type AuditEventType = (typeof AUDIT_EVENT_TYPES)[number]

/** Backend `AuditEvent` (all optional fields may be null). */
export interface AuditEvent {
  schema_version: number
  event_id: string
  event_type: AuditEventType
  occurred_at: string
  sequence: number
  transaction_id: string | null
  status: string | null
  previous_status: string | null
  operation: string | null
  outcome: string | null
  decision: string | null
  risk_level: string | null
  risk_score: number | null
  reasons: string[]
  merchant_name: string | null
  amount: string | null
  currency: string | null
  reviewer_name: string | null
  paypal_order_id: string | null
  paypal_authorization_id: string | null
  paypal_capture_id: string | null
  paypal_status: string | null
  error_code: string | null
  retry_safe: boolean | null
}

/** Backend `AuditSearchResponse`. */
export interface AuditSearchResult {
  source: 'elastic' | 'memory_fallback'
  durable: boolean
  degraded: boolean
  reason: string | null
  message: string
  pending_events: number
  dropped_events: number
  items: AuditEvent[]
  total: number
  limit: number
  offset: number
}

export interface AuditQuery {
  q?: string
  transaction_id?: string
  event_type?: AuditEventType | ''
  limit: number
  offset: number
}

// ------------------------------------------------------------------ validation

const NULLABLE_STRING_FIELDS = [
  'transaction_id', 'status', 'previous_status', 'operation', 'outcome', 'decision',
  'risk_level', 'merchant_name', 'amount', 'currency', 'reviewer_name', 'paypal_order_id',
  'paypal_authorization_id', 'paypal_capture_id', 'paypal_status', 'error_code',
] as const
const EVENT_FIELDS = new Set<string>([
  'schema_version', 'event_id', 'event_type', 'occurred_at', 'sequence', 'risk_score',
  'reasons', 'retry_safe', ...NULLABLE_STRING_FIELDS,
])
const RESULT_FIELDS = ['source', 'durable', 'degraded', 'reason', 'message', 'pending_events',
  'dropped_events', 'items', 'total', 'limit', 'offset']
const TRANSACTION_ID = /^[A-Za-z0-9_-]{1,64}$/
const CODE = /^[A-Z][A-Z0-9_]{0,63}$/

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function fail(what: string): never {
  throw new ApiError('invalid_response', `The ${SERVICE} returned an unexpected response (${what}).`)
}

const isCount = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v) && v >= 0

export function parseAuditEvent(data: unknown): AuditEvent {
  if (!isRecord(data)) fail('event')
  for (const key of Object.keys(data)) if (!EVENT_FIELDS.has(key)) fail(`unexpected field ${key}`)
  if (typeof data.event_id !== 'string' || !/^[0-9a-f]{32}$/.test(data.event_id)) fail('event_id')
  if (!AUDIT_EVENT_TYPES.includes(data.event_type as AuditEventType)) fail('event_type')
  if (typeof data.occurred_at !== 'string' || Number.isNaN(Date.parse(data.occurred_at))) fail('occurred_at')
  for (const field of NULLABLE_STRING_FIELDS) {
    const v = data[field]
    if (v !== undefined && v !== null && typeof v !== 'string') fail(field)
  }
  const reasons = data.reasons ?? []
  if (!Array.isArray(reasons) || !reasons.every((r) => typeof r === 'string')) fail('reasons')
  const score = data.risk_score ?? null
  if (score !== null && (typeof score !== 'number' || !Number.isInteger(score) || score < 0 || score > 100)) {
    fail('risk_score')
  }
  const retry = data.retry_safe ?? null
  if (retry !== null && typeof retry !== 'boolean') fail('retry_safe')

  const event = {
    schema_version: typeof data.schema_version === 'number' ? data.schema_version : 1,
    event_id: data.event_id as string,
    event_type: data.event_type as AuditEventType,
    occurred_at: data.occurred_at as string,
    sequence: typeof data.sequence === 'number' ? data.sequence : 0,
    risk_score: score as number | null,
    reasons: [...(reasons as string[])],
    retry_safe: retry as boolean | null,
  } as AuditEvent
  for (const field of NULLABLE_STRING_FIELDS) {
    ;(event as unknown as Record<string, unknown>)[field] = (data[field] as string | null | undefined) ?? null
  }
  return event
}

export function parseAuditSearch(data: unknown): AuditSearchResult {
  if (!isRecord(data)) fail('not an object')
  if (!RESULT_FIELDS.every((k) => k in data)) fail('fields')
  if (data.source !== 'elastic' && data.source !== 'memory_fallback') fail('source')
  if (typeof data.durable !== 'boolean' || typeof data.degraded !== 'boolean') fail('state')
  // Never accept a "durable" claim for memory fallback results.
  if (data.source === 'memory_fallback' && data.durable) fail('durable')
  if (data.reason !== null && (typeof data.reason !== 'string' || !CODE.test(data.reason))) fail('reason')
  if (typeof data.message !== 'string') fail('message')
  for (const k of ['pending_events', 'dropped_events', 'total', 'limit', 'offset'] as const) {
    if (!isCount(data[k])) fail(k)
  }
  if (!Array.isArray(data.items)) fail('items')
  return {
    source: data.source,
    durable: data.durable,
    degraded: data.degraded,
    reason: data.reason as string | null,
    message: data.message,
    pending_events: data.pending_events as number,
    dropped_events: data.dropped_events as number,
    items: (data.items as unknown[]).map(parseAuditEvent),
    total: data.total as number,
    limit: data.limit as number,
    offset: data.offset as number,
  }
}

// ------------------------------------------------------------------- request

/** Search audit events. The reviewer token goes only into the Authorization header. */
export async function searchAuditEvents(
  query: AuditQuery,
  reviewerToken: string,
  options: RequestOptions = {},
): Promise<AuditSearchResult> {
  const params = new URLSearchParams()
  const q = query.q?.trim()
  if (q) params.set('q', q.slice(0, 200))
  const txn = query.transaction_id?.trim()
  if (txn) {
    if (!TRANSACTION_ID.test(txn)) {
      throw new ApiError('validation', 'Transaction IDs contain only letters, digits, "_" and "-".')
    }
    params.set('transaction_id', txn)
  }
  if (query.event_type) params.set('event_type', query.event_type)
  params.set('limit', String(Math.min(Math.max(Math.trunc(query.limit), 1), AUDIT_MAX_LIMIT)))
  params.set('offset', String(Math.min(Math.max(Math.trunc(query.offset), 0), AUDIT_MAX_OFFSET)))

  let data: unknown
  try {
    data = await getJson(`${AUDIT_EVENTS_PATH}?${params.toString()}`, {
      service: SERVICE,
      ...options,
      headers: { ...options.headers, Authorization: `Bearer ${reviewerToken}` },
    })
  } catch (err) {
    if (err instanceof ApiError) {
      const known = paymentErrorMessage(err.code)  // reviewer auth codes are shared
      if (known) throw new ApiError(err.kind, known, { status: err.status, code: err.code })
    }
    throw err
  }
  return parseAuditSearch(data)
}
