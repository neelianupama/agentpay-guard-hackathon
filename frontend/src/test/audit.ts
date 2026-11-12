import type { AuditEvent, AuditSearchResult } from '../api/audit'

let counter = 0

/** An event shaped exactly like the backend AuditEvent (all fields present, nulls included). */
export function makeAuditEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  counter += 1
  return {
    schema_version: 1,
    event_id: counter.toString(16).padStart(32, '0'),
    event_type: 'PAYMENT_CREATED',
    occurred_at: new Date(Date.UTC(2026, 10, 12, 6, 30, counter)).toISOString(),
    sequence: counter,
    transaction_id: 'txn_0123456789abcdef01234567',
    status: 'PENDING_REVIEW',
    previous_status: null,
    operation: null,
    outcome: null,
    decision: 'REVIEW',
    risk_level: 'MEDIUM',
    risk_score: 35,
    reasons: ['Amount 2,500.00 USD is at or above the human-review threshold of 2,000.00 USD.'],
    merchant_name: 'Acme Cloud',
    amount: '2500.00',
    currency: 'USD',
    reviewer_name: null,
    paypal_order_id: null,
    paypal_authorization_id: null,
    paypal_capture_id: null,
    paypal_status: null,
    error_code: null,
    retry_safe: null,
    ...overrides,
  }
}

export function elasticResult(items: AuditEvent[], overrides: Partial<AuditSearchResult> = {}): AuditSearchResult {
  return {
    source: 'elastic', durable: true, degraded: false, reason: null,
    message: 'Results from Elastic.', pending_events: 0, dropped_events: 0,
    items, total: items.length, limit: 25, offset: 0, ...overrides,
  }
}

export function fallbackResult(items: AuditEvent[], overrides: Partial<AuditSearchResult> = {}): AuditSearchResult {
  return {
    source: 'memory_fallback', durable: false, degraded: true, reason: 'ELASTIC_NOT_CONFIGURED',
    message: 'Elastic is not configured. Audit events are held only in backend memory.',
    pending_events: items.length, dropped_events: 0,
    items, total: items.length, limit: 25, offset: 0, ...overrides,
  }
}
