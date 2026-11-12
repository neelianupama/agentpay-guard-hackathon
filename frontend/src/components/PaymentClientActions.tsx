import { authorizePayment, createPaymentOrder, type PaymentAction, type PaymentStatus } from '../api/payments'
import { useApiAction } from '../hooks/useApiAction'

/** The fields these actions need; satisfied by both the full record and the minimal view. */
export interface ActionSubject {
  transaction_id: string
  status: PaymentStatus
  next_actions: string[]
}

/**
 * Buyer/agent actions the backend currently allows (from `next_actions`):
 * - create_order: create or safely retry the PayPal order
 * - authorize: ask the backend to verify buyer approval with PayPal and authorize
 *
 * These endpoints need no reviewer token and return only the minimal action view.
 * Capture and void are deliberately absent: they are reviewer-only actions on the
 * Approvals page and never happen automatically.
 *
 * `onSettled` receives the minimal result on success, or `null` after a failure
 * (reviewer views use it to re-read the full record with the reviewer token).
 */
export function PaymentClientActions({
  subject,
  onSettled,
}: {
  subject: ActionSubject
  onSettled: (result: PaymentAction | null) => void | Promise<void>
}) {
  const action = useApiAction<PaymentAction>()
  const canCreateOrder = subject.next_actions.includes('create_order')
  const canAuthorize = subject.next_actions.includes('authorize')
  const isReviewerStep = ['PENDING_REVIEW', 'AUTHORIZED', 'PENDING_CAPTURE', 'PENDING_VOID'].includes(
    subject.status,
  )

  if (!canCreateOrder && !canAuthorize && !isReviewerStep) return null

  const go = async (task: (signal: AbortSignal) => Promise<PaymentAction>) => {
    const outcome = await action.run(task)
    if (outcome?.status === 'success') await onSettled(outcome.result)
    else if (outcome?.status === 'error' && outcome.error.status !== undefined) await onSettled(null)
  }

  const authorizeLabel =
    subject.status === 'AUTHORIZATION_PENDING' ? 'Refresh authorization status' : 'Check PayPal approval & authorize'

  return (
    <div className="payment-actions">
      {canCreateOrder && (
        <button
          type="button"
          className="button button--primary"
          disabled={action.busy}
          onClick={() => void go((signal) => createPaymentOrder(subject.transaction_id, { signal }))}
        >
          {action.busy ? 'Working…' : 'Create PayPal order'}
        </button>
      )}
      {canAuthorize && (
        <button
          type="button"
          className="button button--primary"
          disabled={action.busy}
          onClick={() => void go((signal) => authorizePayment(subject.transaction_id, { signal }))}
        >
          {action.busy ? 'Checking with PayPal…' : authorizeLabel}
        </button>
      )}
      {isReviewerStep && (
        <p className="payment-actions__note">
          The next step needs a reviewer. Reviewers approve, reject, capture or void payments on the
          Approvals page; nothing is captured automatically.
        </p>
      )}
      {action.state.status === 'error' && (
        <div className="alert alert--danger" role="alert">
          <h3>Action failed</h3>
          <p>{action.state.error.message}</p>
        </div>
      )}
    </div>
  )
}
