/** Display helpers for audit events (only allowlisted fields are ever used). */

import type { AuditEvent, AuditEventType } from '../api/audit'
import { formatMoney } from './paymentDisplay'

export const EVENT_LABELS: Record<AuditEventType, string> = {
  RISK_ASSESSED: 'Risk assessed',
  PAYMENT_CREATED: 'Payment created',
  PAYMENT_STATUS_CHANGED: 'Status changed',
  REVIEW_APPROVED: 'Review approved',
  REVIEW_REJECTED: 'Review rejected',
  PAYPAL_ORDER_CREATED: 'PayPal order created',
  PAYPAL_AUTHORIZATION: 'PayPal authorization',
  PAYPAL_CAPTURE: 'PayPal capture',
  PAYPAL_VOID: 'PayPal void',
  PAYPAL_OPERATION_FAILED: 'PayPal operation failed',
}

const REASON_LABELS: Record<string, string> = {
  ELASTIC_NOT_CONFIGURED: 'Elastic is not configured',
  ELASTIC_UNREACHABLE: 'Elastic is unreachable',
  ELASTIC_TIMEOUT: 'Elastic did not respond in time',
  ELASTIC_AUTH_FAILED: 'Elastic rejected the configured API key',
  ELASTIC_RATE_LIMITED: 'Elastic is rate limiting requests',
  ELASTIC_UNAVAILABLE: 'Elastic returned a server error',
  ELASTIC_MAPPING_MISMATCH: 'The Elastic audit index has an incompatible mapping',
  ELASTIC_DOCUMENT_REJECTED: 'Elastic rejected some audit events',
}

export function reasonLabel(reason: string | null): string {
  return (reason && REASON_LABELS[reason]) || 'Elastic could not be used'
}

/** Safe, human-readable summary built only from allowlisted event fields. */
export function eventDetails(e: AuditEvent): string {
  const parts: string[] = []
  if (e.decision) {
    const risk = e.risk_score !== null ? ` (score ${e.risk_score}${e.risk_level ? `, ${e.risk_level}` : ''})` : ''
    parts.push(`${e.decision}${risk}`)
  }
  if (e.amount && e.currency) parts.push(`${formatMoney(e.amount, e.currency)}${e.merchant_name ? ` to ${e.merchant_name}` : ''}`)
  if (e.reviewer_name) parts.push(`by ${e.reviewer_name}`)
  if (e.paypal_status) parts.push(`PayPal: ${e.paypal_status}`)
  if (e.error_code) parts.push(`${e.error_code}${e.retry_safe ? ' (retry safe)' : ''}`)
  return parts.join(' · ')
}
