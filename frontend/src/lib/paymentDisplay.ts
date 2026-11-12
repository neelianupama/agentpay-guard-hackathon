/** Display helpers for backend payment records (descriptive only; the backend decides). */

import type { PaymentStatus } from '../api/payments'

export type Tone = 'success' | 'warning' | 'danger' | 'neutral'

/** Display copy per backend status. Purely descriptive: the backend decides the status. */
export const STATUS_INFO: Record<PaymentStatus, { label: string; tone: Tone; detail: string }> = {
  BLOCKED: { label: 'Blocked', tone: 'danger', detail: 'Blocked by policy. It will never be sent to PayPal.' },
  PENDING_REVIEW: {
    label: 'Pending review',
    tone: 'warning',
    detail: 'A reviewer must approve this payment. Nothing is sent to PayPal until then.',
  },
  REJECTED: { label: 'Rejected', tone: 'danger', detail: 'A reviewer rejected this payment. Nothing was sent to PayPal.' },
  APPROVED_FOR_PAYMENT: {
    label: 'Approved, order not created',
    tone: 'neutral',
    detail: 'Approved for payment, but the PayPal order has not been created yet.',
  },
  AWAITING_BUYER_APPROVAL: {
    label: 'Awaiting buyer approval',
    tone: 'warning',
    detail: 'An authorize-only PayPal order exists. The buyer must approve it on PayPal.',
  },
  BUYER_APPROVED: {
    label: 'Buyer approved',
    tone: 'neutral',
    detail: 'PayPal confirmed buyer approval. Authorization is not confirmed yet.',
  },
  AUTHORIZATION_PENDING: {
    label: 'Authorization pending',
    tone: 'warning',
    detail: 'PayPal reports the authorization as pending. It cannot be captured yet.',
  },
  AUTHORIZED: {
    label: 'Authorized',
    tone: 'neutral',
    detail: 'Funds are authorized, not captured. A reviewer must capture or void it.',
  },
  PENDING_CAPTURE: {
    label: 'Capture not confirmed',
    tone: 'warning',
    detail: 'A reviewer requested capture, but the outcome is not confirmed yet. Funds are not confirmed as captured.',
  },
  CAPTURE_PENDING: {
    label: 'Capture pending at PayPal',
    tone: 'warning',
    detail: 'PayPal accepted the capture but reports it as pending. Funds are not confirmed as captured.',
  },
  CAPTURED: { label: 'Captured', tone: 'success', detail: 'PayPal confirmed the capture.' },
  PENDING_VOID: {
    label: 'Void not confirmed',
    tone: 'warning',
    detail: 'A reviewer requested a void, but the outcome is not confirmed yet.',
  },
  VOIDED: { label: 'Voided', tone: 'neutral', detail: 'The authorization was voided. No funds were captured.' },
  FAILED: { label: 'Failed', tone: 'danger', detail: 'PayPal rejected or declined an operation for this payment.' },
}

export const DECISION_TONE: Record<string, Tone> = { ALLOW: 'success', REVIEW: 'warning', BLOCK: 'danger' }

export function formatMoney(amount: string, currency: string): string {
  const [whole, fraction = ''] = amount.split('.')
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')} ${currency}`
}

export function formatDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
