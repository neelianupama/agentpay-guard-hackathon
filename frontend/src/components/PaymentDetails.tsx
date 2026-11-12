import type { ReactNode } from 'react'
import { isSandboxApprovalUrl, paymentErrorMessage, type Payment, type PaymentStatus } from '../api/payments'
import { DECISION_TONE, formatDate, formatMoney, STATUS_INFO } from '../lib/paymentDisplay'

export function StatusBadge({ status }: { status: PaymentStatus }) {
  const info = STATUS_INFO[status]
  return (
    <span className={`level-badge level-badge--${info.tone}`} data-status={status}>
      {info.label}
    </span>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="payment-facts__row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

/** Read-only view of a backend payment record. Page-specific actions go in `children`. */
export function PaymentDetails({
  payment,
  titleId,
  children,
}: {
  payment: Payment
  titleId: string
  children?: ReactNode
}) {
  const info = STATUS_INFO[payment.status]
  const { risk, paypal } = payment
  const showApprovalLink =
    payment.status === 'AWAITING_BUYER_APPROVAL' &&
    paypal.approval_url !== null &&
    isSandboxApprovalUrl(paypal.approval_url)
  const errorMessage = payment.last_error
    ? (paymentErrorMessage(payment.last_error.code) ?? 'The last payment operation did not complete.')
    : null

  return (
    <article className="payment-details" aria-labelledby={titleId}>
      <header className="payment-details__header">
        <div>
          <h3 id={titleId}>
            {payment.merchant_name} · {formatMoney(payment.amount, payment.currency)}
          </h3>
          <p className="payment-details__meta">
            <code>{payment.transaction_id}</code> · Created {formatDate(payment.created_at)}
          </p>
        </div>
        <StatusBadge status={payment.status} />
      </header>

      <p className="payment-details__status">{info.detail}</p>

      {errorMessage && payment.last_error && (
        <div className="alert alert--warning" role="alert">
          <h3>Last operation did not complete</h3>
          <p>{errorMessage}</p>
          {payment.last_error.retry_safe && <p>It is safe to try the same action again.</p>}
        </div>
      )}

      {showApprovalLink && (
        <div className="alert alert--info">
          <h3>Buyer approval needed</h3>
          <p>
            <a href={paypal.approval_url!} target="_blank" rel="noopener noreferrer">
              Approve this payment on PayPal Sandbox
            </a>{' '}
            (opens in a new tab). Then come back and check approval. Approving on PayPal only
            authorizes the payment; nothing is captured.
          </p>
        </div>
      )}

      <dl className="payment-facts">
        <Row label="Policy decision">
          <span className={`level-badge level-badge--${DECISION_TONE[risk.decision]}`}>{risk.decision}</span>
        </Row>
        <Row label="Risk score">{risk.risk_score} / 100</Row>
        <Row label="Risk level">{risk.risk_level}</Row>
        <Row label="Description">
          {payment.description} — {payment.purpose}
        </Row>
        {paypal.order_id && (
          <Row label="PayPal order">
            <code>{paypal.order_id}</code>
            {paypal.order_status && ` (${paypal.order_status})`}
          </Row>
        )}
        {paypal.authorization_id && (
          <Row label="Authorization">
            <code>{paypal.authorization_id}</code>
            {paypal.authorization_status && ` (${paypal.authorization_status})`}
          </Row>
        )}
        {paypal.capture_id && (
          <Row label="Capture">
            <code>{paypal.capture_id}</code>
            {paypal.capture_status && ` (${paypal.capture_status})`}
          </Row>
        )}
        {payment.review && (
          <Row label="Review">
            {payment.review.decision === 'APPROVE' ? 'Approved' : 'Rejected'}
            {payment.review.reviewer_name && ` by ${payment.review.reviewer_name}`} on{' '}
            {formatDate(payment.review.decided_at)}
            {payment.review.comment && ` — “${payment.review.comment}”`}
          </Row>
        )}
      </dl>

      <div className="risk-reasons">
        <h3>Risk reasons</h3>
        <ul>
          {risk.reasons.map((reason, index) => (
            <li key={index}>{reason}</li>
          ))}
        </ul>
      </div>

      {children}
    </article>
  )
}
