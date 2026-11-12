import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import {
  getPayment,
  listPayments,
  MAX_PAGE_SIZE,
  reviewerAction,
  type Payment,
  type PaymentList,
  type ReviewerActionName,
} from '../api/payments'
import { PaymentDetails } from '../components/PaymentDetails'
import { formatMoney, STATUS_INFO } from '../lib/paymentDisplay'
import { useReviewerToken } from '../auth/reviewerToken'
import { ReviewerTokenPanel } from '../components/ReviewerTokenPanel'
import { useApiAction } from '../hooks/useApiAction'

/** Statuses where a reviewer can act (the backend's next_actions decides which actions). */
export const REVIEWER_STATUSES = new Set(['PENDING_REVIEW', 'AUTHORIZED', 'PENDING_CAPTURE', 'PENDING_VOID'])
const REVIEWER_ACTIONS: ReviewerActionName[] = ['approve', 'reject', 'capture', 'void']
const NEEDS_CONFIRMATION: ReadonlySet<ReviewerActionName> = new Set(['capture', 'void'])

const ACTION_LABEL: Record<ReviewerActionName, string> = {
  approve: 'Approve',
  reject: 'Reject',
  capture: 'Capture',
  void: 'Void',
}

/** What the backend's returned status means, so we never claim more than PayPal confirmed. */
function outcomeMessage(action: ReviewerActionName, payment: Payment): string {
  const status = STATUS_INFO[payment.status].label
  switch (payment.status) {
    case 'CAPTURED':
      return 'PayPal confirmed the capture.'
    case 'CAPTURE_PENDING':
      return 'PayPal accepted the capture but reports it as pending. Funds are not confirmed as captured.'
    case 'VOIDED':
      return 'The authorization was voided. No funds were captured.'
    case 'REJECTED':
      return 'Payment rejected. Nothing was sent to PayPal.'
    case 'AWAITING_BUYER_APPROVAL':
      return 'Approved. A PayPal order was created; the buyer must now approve it on PayPal.'
    default:
      return `${ACTION_LABEL[action]} sent. Current status: ${status}.`
  }
}

export function ApprovalsPage() {
  // The reviewer token lives only in application memory (ReviewerTokenProvider). It is
  // never persisted, logged, put in a URL, or rendered. The queue loads only with a token.
  const { token } = useReviewerToken()
  const [reviewerName, setReviewerName] = useState('')
  const list = useApiAction<PaymentList>()
  // Results of reviewer actions since the last load, keyed by transaction_id.
  const [updates, setUpdates] = useState<Record<string, Payment>>({})
  const nameId = useId()
  const { run: runList } = list

  const load = useCallback(
    (reviewerToken: string) =>
      void runList((signal) => listPayments({ limit: MAX_PAGE_SIZE, offset: 0 }, reviewerToken, { signal })),
    [runList],
  )

  useEffect(() => {
    if (token) load(token)
  }, [load, token])

  const refresh = () => {
    setUpdates({})
    if (token) load(token)
  }

  const items = useMemo(() => {
    if (list.state.status !== 'success') return []
    return list.state.result.items
      .filter((p) => REVIEWER_STATUSES.has(p.status))
      .map((p) => updates[p.transaction_id] ?? p)
  }, [list.state, updates])

  const replace = (payment: Payment) =>
    setUpdates((prev) => ({ ...prev, [payment.transaction_id]: payment }))

  // Group by stage. Cards keep their group after an action so the result stays visible
  // until the next refresh (which drops items that no longer need a reviewer).
  const reviews = items.filter((p) => p.paypal.authorization_id === null)
  const settlements = items.filter((p) => p.paypal.authorization_id !== null)

  const tokenPanel = (
    <ReviewerTokenPanel purpose="Viewing the approval queue and acting on payments require the reviewer token." />
  )
  if (!token) return <div className="payments-stack">{tokenPanel}</div>

  return (
    <div className="payments-stack">
    {tokenPanel}
    <div className="payments-page">
      <section className="panel" aria-labelledby="reviewer-name-title">
        <header className="panel__header">
          <div>
            <h2 id="reviewer-name-title">Reviewer</h2>
            <p>Recorded with your actions for the audit trail (comments are not indexed).</p>
          </div>
        </header>
        <div className="payment-form__body">
          <div className="field">
            <label htmlFor={nameId}>Your name (optional)</label>
            <input id={nameId} type="text" maxLength={100} value={reviewerName}
              onChange={(e) => setReviewerName(e.target.value)} />
          </div>
        </div>
      </section>

      <section className="panel" aria-labelledby="queue-title">
        <header className="panel__header">
          <div>
            <h2 id="queue-title">Approval queue</h2>
            <p>Payments that need a reviewer: approve or reject, then capture or void.</p>
          </div>
          <button type="button" className="button button--secondary" disabled={list.busy}
            onClick={refresh}>
            Refresh
          </button>
        </header>
        <div className="payments-page__body">
          {list.state.status === 'loading' && <p role="status">Loading payments…</p>}
          {list.state.status === 'error' && (
            <div className="alert alert--danger" role="alert">
              <h3>Could not load payments</h3>
              <p>{list.state.error.message}</p>
            </div>
          )}
          {list.state.status === 'success' && items.length === 0 && (
            <div className="empty-state empty-state--compact">
              <h3>Nothing to review</h3>
              <p>No payments are waiting for a reviewer.</p>
            </div>
          )}
          {list.state.status === 'success' && list.state.result.total > MAX_PAGE_SIZE && (
            <p className="payment-form__note">
              Showing reviewer items from the newest {MAX_PAGE_SIZE} payments only.
            </p>
          )}
          {reviews.length > 0 && (
            <div className="approval-group">
              <h3>Awaiting review</h3>
              {reviews.map((p) => (
                <ReviewerCard key={p.transaction_id} payment={p} token={token} reviewerName={reviewerName}
                  onUpdated={replace} />
              ))}
            </div>
          )}
          {settlements.length > 0 && (
            <div className="approval-group">
              <h3>Authorized payments</h3>
              {settlements.map((p) => (
                <ReviewerCard key={p.transaction_id} payment={p} token={token} reviewerName={reviewerName}
                  onUpdated={replace} />
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
    </div>
  )
}

function ReviewerCard({
  payment,
  token,
  reviewerName,
  onUpdated,
}: {
  payment: Payment
  token: string
  reviewerName: string
  onUpdated: (payment: Payment) => void
}) {
  const action = useApiAction<Payment>()
  const [comment, setComment] = useState('')
  const [confirming, setConfirming] = useState<ReviewerActionName | null>(null)
  const [outcome, setOutcome] = useState<string | null>(null)
  const commentId = useId()
  const titleId = useId()
  const allowed = REVIEWER_ACTIONS.filter((a) => payment.next_actions.includes(a))
  const amount = formatMoney(payment.amount, payment.currency)
  const hasToken = token.trim().length > 0

  const perform = async (name: ReviewerActionName) => {
    setConfirming(null)
    setOutcome(null)
    const result = await action.run((signal) =>
      reviewerAction(payment.transaction_id, name, token.trim(),
        { reviewer_name: reviewerName, comment }, { signal }),
    )
    if (result?.status === 'success') {
      onUpdated(result.result)
      setOutcome(outcomeMessage(name, result.result))
      setComment('')
    } else if (result?.status === 'error' && result.error.details.transaction_id) {
      // The backend kept the payment (e.g. capture outcome not confirmed): show its stored state.
      try {
        onUpdated(await getPayment(payment.transaction_id, token.trim()))
      } catch {
        // The error is already shown.
      }
    }
  }

  const click = (name: ReviewerActionName) => {
    if (NEEDS_CONFIRMATION.has(name)) setConfirming(name)
    else void perform(name)
  }

  return (
    <div className="approval-card">
      <PaymentDetails payment={payment} titleId={titleId}>
        {allowed.length > 0 && (
          <div className="approval-card__actions">
            <div className="field">
              <label htmlFor={commentId}>Comment (optional)</label>
              <input id={commentId} type="text" maxLength={1000} value={comment}
                onChange={(e) => setComment(e.target.value)} disabled={action.busy} />
            </div>
            {!hasToken && <p className="payment-form__note">Enter the reviewer token above to act.</p>}
            {confirming ? (
              <div className="alert alert--warning" role="group" aria-label={`Confirm ${confirming}`}>
                <p>
                  {confirming === 'capture'
                    ? `Capture ${amount} from the PayPal authorization? This moves funds and cannot be undone here.`
                    : `Void the PayPal authorization for ${amount}? It can no longer be captured afterwards.`}
                </p>
                <div className="payment-actions">
                  <button type="button" className="button button--primary" disabled={action.busy}
                    onClick={() => void perform(confirming)}>
                    {confirming === 'capture' ? `Confirm capture of ${amount}` : `Confirm void of ${amount}`}
                  </button>
                  <button type="button" className="button button--secondary" onClick={() => setConfirming(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="payment-actions">
                {allowed.map((name) => (
                  <button key={name} type="button"
                    className={`button ${name === 'reject' || name === 'void' ? 'button--secondary' : 'button--primary'}`}
                    disabled={!hasToken || action.busy}
                    onClick={() => click(name)}>
                    {name === 'capture' && payment.status === 'PENDING_CAPTURE'
                      ? 'Retry capture'
                      : name === 'void' && payment.status === 'PENDING_VOID'
                        ? 'Retry void'
                        : ACTION_LABEL[name]}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {action.busy && <p role="status">Sending reviewer action…</p>}
        {outcome && <p className="approval-card__outcome" role="status">{outcome}</p>}
        {action.state.status === 'error' && (
          <div className="alert alert--danger" role="alert">
            <h3>Reviewer action failed</h3>
            <p>{action.state.error.message}</p>
          </div>
        )}
      </PaymentDetails>
    </div>
  )
}
