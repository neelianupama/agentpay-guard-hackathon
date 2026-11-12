/**
 * Minimal JSON HTTP client for the AgentPay Guard backend.
 *
 * Responsibilities: base URL resolution, timeouts, cancellation and turning
 * every failure mode into a typed ApiError. No business logic lives here.
 */

const DEFAULT_API_BASE_URL = 'http://localhost:8000'
const DEFAULT_TIMEOUT_MS = 15_000

/** Backend base URL from VITE_API_BASE_URL (see frontend/.env.example). */
export const API_BASE_URL: string = (
  import.meta.env?.VITE_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL
).replace(/\/+$/, '')

export type ApiErrorKind =
  | 'network' // backend unreachable (down, wrong URL, CORS)
  | 'timeout'
  | 'aborted' // cancelled by the caller
  | 'validation' // HTTP 422 from the backend
  | 'http' // any other non-2xx status
  | 'invalid_response' // 2xx but body is not what the contract promises

export type FieldErrors = Record<string, string>

/** Safe, typed subset of a backend `ErrorResponse.details` object. */
export interface ErrorDetails {
  transaction_id?: string
  status?: string
  retry_safe?: boolean
  paypal_order_status?: string
}

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | undefined
  /** Backend validation messages keyed by request field name. */
  readonly fieldErrors: FieldErrors
  /** Machine-readable `ErrorResponse.code` from the backend, when it sent one. */
  readonly code: string | undefined
  /** Known-safe fields from `ErrorResponse.details` (e.g. transaction_id, retry_safe). */
  readonly details: ErrorDetails

  constructor(
    kind: ApiErrorKind,
    message: string,
    options: { status?: number; fieldErrors?: FieldErrors; code?: string; details?: ErrorDetails } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = options.status
    this.fieldErrors = options.fieldErrors ?? {}
    this.code = options.code
    this.details = options.details ?? {}
  }
}

export interface RequestOptions {
  signal?: AbortSignal
  timeoutMs?: number
  baseUrl?: string
  fetchImpl?: typeof fetch
  /** Name used in error messages, e.g. "risk service" (default). */
  service?: string
  /** Extra request headers (e.g. Authorization for reviewer actions). Never logged. */
  headers?: Record<string, string>
}

interface FastApiValidationItem {
  loc?: unknown
  msg?: unknown
}

const ERROR_CODE_PATTERN = /^[A-Z][A-Z0-9_]{0,63}$/

/** The `code` of a backend ErrorResponse body, if present and well-formed. */
export function parseErrorCode(body: unknown): string | undefined {
  const code = (body as { code?: unknown } | null)?.code
  return typeof code === 'string' && ERROR_CODE_PATTERN.test(code) ? code : undefined
}

const TRANSACTION_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/
const STATUS_PATTERN = /^[A-Z][A-Z_]{0,63}$/

/** Pick only known-safe, well-formed fields from `ErrorResponse.details`. */
export function parseErrorDetails(body: unknown): ErrorDetails {
  const raw = (body as { details?: unknown } | null)?.details
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return {}
  const d = raw as Record<string, unknown>
  const out: ErrorDetails = {}
  if (typeof d.transaction_id === 'string' && TRANSACTION_ID_PATTERN.test(d.transaction_id)) {
    out.transaction_id = d.transaction_id
  }
  if (typeof d.status === 'string' && STATUS_PATTERN.test(d.status)) out.status = d.status
  if (typeof d.retry_safe === 'boolean') out.retry_safe = d.retry_safe
  if (typeof d.paypal_order_status === 'string' && STATUS_PATTERN.test(d.paypal_order_status)) {
    out.paypal_order_status = d.paypal_order_status
  }
  return out
}

/** Map FastAPI 422 `detail` entries with loc ["body", field] to field messages. */
export function parseValidationErrors(body: unknown): FieldErrors {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (!Array.isArray(detail)) return {}
  const errors: FieldErrors = {}
  for (const item of detail as FastApiValidationItem[]) {
    const loc = Array.isArray(item?.loc) ? item.loc : []
    const field = loc[0] === 'body' && typeof loc[1] === 'string' ? loc[1] : '_form'
    if (typeof item?.msg === 'string' && !(field in errors)) errors[field] = item.msg
  }
  return errors
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return undefined
  }
}

export async function postJson(
  path: string,
  body: unknown,
  options: RequestOptions = {},
): Promise<unknown> {
  return requestJson('POST', path, body, options)
}

export async function getJson(path: string, options: RequestOptions = {}): Promise<unknown> {
  return requestJson('GET', path, undefined, options)
}

export async function requestJson(
  method: 'GET' | 'POST',
  path: string,
  body: unknown,
  options: RequestOptions = {},
): Promise<unknown> {
  const baseUrl = (options.baseUrl ?? API_BASE_URL).replace(/\/+$/, '')
  const fetchImpl = options.fetchImpl ?? fetch
  const service = options.service ?? 'risk service'
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, options.timeoutMs ?? DEFAULT_TIMEOUT_MS)
  const onCallerAbort = () => controller.abort()
  if (options.signal?.aborted) controller.abort()
  options.signal?.addEventListener('abort', onCallerAbort)

  let response: Response
  try {
    const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
    const init: RequestInit = { method, headers, signal: controller.signal }
    if (method === 'POST') {
      headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(body ?? {})
    }
    response = await fetchImpl(`${baseUrl}${path}`, init)
  } catch {
    if (timedOut) throw new ApiError('timeout', `The ${service} took too long to respond.`)
    if (options.signal?.aborted) throw new ApiError('aborted', 'The request was cancelled.')
    throw new ApiError(
      'network',
      `Could not reach the ${service} at ${baseUrl}. Check that the backend is running.`,
    )
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', onCallerAbort)
  }

  const data = await readJson(response)

  if (response.status === 422) {
    throw new ApiError('validation', `The ${service} rejected some of the details.`, {
      status: 422,
      fieldErrors: parseValidationErrors(data),
      code: parseErrorCode(data),
      details: parseErrorDetails(data),
    })
  }
  if (!response.ok) {
    throw new ApiError('http', `The ${service} returned an error (HTTP ${response.status}).`, {
      status: response.status,
      code: parseErrorCode(data),
      details: parseErrorDetails(data),
    })
  }
  if (data === undefined) {
    throw new ApiError('invalid_response', `The ${service} returned an unreadable response.`, {
      status: response.status,
    })
  }
  return data
}
