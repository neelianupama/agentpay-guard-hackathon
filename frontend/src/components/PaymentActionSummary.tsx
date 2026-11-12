import type { ReactNode } from 'react'
import { isSandboxApprovalUrl, paymentErrorMessage, type PaymentAction } from '../api/payments'
import { formatMoney, STATUS_INFO } from '../lib/paymentDisplay'
import { StatusBadge } from './PaymentDetails'

/**
 * Buyer-facing view of the minimal action response (no reviewer, risk or PayPal ID data).
 * The risk decision itself is shown separately from the backend risk assessment.
 */
export function PaymentActionSummary({
  action,
  titleId,
  children,
}: {
  action: PaymentAction
  titleId: string
  children?: ReactNode
}) {
  const info = STATUS_INFO[action.status]
  const showApprovalLink =
    action.status === 'AWAITING_BUYER_APPROVAL' &&
    action.approval_url !== null &&
    isSandboxApprovalUrl(action.approval_url)

  return (
    <article className="payment-details" aria-labelledby={titleId}>
      <header className="payment-details__header">
        <div>
          <h3 id={titleId}>
            {action.merchant_name} · {formatMoney(action.amount, action.currency)}
          </h3>
          <p className="payment-details__meta">
            <code>{action.transaction_id}</code>
          </p>
        </div>
        <StatusBadge status={action.status} />
      </header>

      <p className="payment-details__status">{info.detail}</p>

      {action.last_error && (
        <div className="alert alert--warning" role="alert">
          <h3>Last operation did not complete</h3>
          <p>{paymentErrorMessage(action.last_error.code) ?? 'The last payment operation did not complete.'}</p>
          {action.last_error.retry_safe && <p>It is safe to try the same action again.</p>}
        </div>
      )}

      {showApprovalLink && (
        <div className="alert alert--info">
          <h3>Buyer approval needed</h3>
          <p>
            <a href={action.approval_url!} target="_blank" rel="noopener noreferrer">
              Approve this payment on PayPal Sandbox
            </a>{' '}
            (opens in a new tab). Then come back and check approval. Approving on PayPal only
            authorizes the payment; nothing is captured.
          </p>
        </div>
      )}

      {children}
    </article>
  )
}
