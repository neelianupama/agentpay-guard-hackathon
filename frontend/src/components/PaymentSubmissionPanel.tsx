import { useState } from 'react'
import type { ErrorDetails } from '../api/client'
import { createPayment, createPaymentOrder, type PaymentAction, type PaymentStatus, PAYMENT_STATUSES } from '../api/payments'
import type { RiskAssessment, TransactionDetails } from '../api/risk'
import { useApiAction } from '../hooks/useApiAction'
import { PaymentActionSummary } from './PaymentActionSummary'
import { PaymentClientActions } from './PaymentClientActions'
import { STATUS_INFO } from '../lib/paymentDisplay'

const SUBMIT_COPY = {
  ALLOW: {
    button: 'Submit payment',
    note: 'Submitting creates the payment and an authorize-only PayPal Sandbox order. Nothing is captured.',
  },
  REVIEW: {
    button: 'Submit for review',
    note: 'Submitting records the payment for reviewer approval. Nothing is sent to PayPal until a reviewer approves it.',
  },
  BLOCK: {
    button: 'Record blocked payment',
    note: 'This payment is blocked by policy. Recording it keeps an audit record; it is never sent to PayPal.',
  },
} as const

function sameDetails(a: TransactionDetails, b: TransactionDetails): boolean {
  return (
    a.merchant_name === b.merchant_name &&
    a.amount === b.amount &&
    a.currency === b.currency &&
    a.description === b.description &&
    a.purpose === b.purpose
  )
}

/**
 * Step after a risk preview: submit the *assessed* details to POST /api/v1/payments.
 * The backend re-runs the risk engine and decides the payment status; this
 * panel only shows what the backend returns.
 */
export function PaymentSubmissionPanel({
  assessment,
  currentDetails,
}: {
  assessment: RiskAssessment
  currentDetails: TransactionDetails
}) {
  const create = useApiAction<PaymentAction>()
  const retry = useApiAction<PaymentAction>()
  // Minimal action view from the backend (no reviewer, risk or PayPal ID data).
  const [payment, setPayment] = useState<PaymentAction | null>(null)
  // Saved-but-incomplete payment, as safely reported in the error details (reading the
  // full record needs the reviewer token, which this page does not use).
  const [stored, setStored] = useState<Required<Pick<ErrorDetails, 'transaction_id'>> & ErrorDetails | null>(null)
  const copy = SUBMIT_COPY[assessment.decision]
  const stale = !sameDetails(assessment.transaction, currentDetails)

  const submit = async () => {
    const outcome = await create.run((signal) => createPayment(assessment.transaction, { signal }))
    if (outcome?.status === 'success') {
      setPayment(outcome.result)
    } else if (outcome?.status === 'error' && outcome.error.details.transaction_id) {
      const details = outcome.error.details
      setStored({ ...details, transaction_id: details.transaction_id! })
    }
  }

  const retryOrder = async () => {
    if (!stored) return
    const outcome = await retry.run((signal) => createPaymentOrder(stored.transaction_id, { signal }))
    if (outcome?.status === 'success') {
      setPayment(outcome.result)
      setStored(null)
    }
  }

  const storedStatus =
    stored?.status && (PAYMENT_STATUSES as readonly string[]).includes(stored.status)
      ? (stored.status as PaymentStatus)
      : null
  const canRetryOrder = storedStatus === 'APPROVED_FOR_PAYMENT' && stored?.retry_safe === true

  return (
    <section className="panel payment-panel" aria-labelledby="payment-panel-title">
      <header className="panel__header">
        <div>
          <h2 id="payment-panel-title">Payment</h2>
          <p>Status and next steps come from the AgentPay Guard backend.</p>
        </div>
      </header>
      <div className="risk-result__body">
        {payment ? (
          <PaymentActionSummary action={payment} titleId="submitted-payment-title">
            <PaymentClientActions subject={payment} onSettled={(result) => { if (result) setPayment(result) }} />
          </PaymentActionSummary>
        ) : stored ? (
          <div className="payment-submit" role="status">
            <p>
              The payment was saved as <code>{stored.transaction_id}</code>
              {storedStatus && <> with status <strong>{STATUS_INFO[storedStatus].label}</strong></>}, but
              the PayPal step did not complete.
            </p>
            {canRetryOrder ? (
              <button type="button" className="button button--primary" disabled={retry.busy}
                onClick={() => void retryOrder()}>
                {retry.busy ? 'Working…' : 'Create PayPal order'}
              </button>
            ) : (
              <p>A reviewer can check it on the Transactions page.</p>
            )}
            {retry.state.status === 'error' && (
              <div className="alert alert--danger" role="alert">
                <h3>Retry failed</h3>
                <p>{retry.state.error.message}</p>
              </div>
            )}
          </div>
        ) : (
          <div className="payment-submit">
            <p>{copy.note}</p>
            {stale && (
              <p className="payment-submit__stale" role="status">
                The form has changed since this assessment. Assess again to submit the new details.
              </p>
            )}
            <button
              type="button"
              className="button button--primary"
              disabled={create.busy || stale}
              onClick={() => void submit()}
            >
              {create.busy && <span className="spinner" aria-hidden="true" />}
              {create.busy ? 'Submitting…' : copy.button}
            </button>
          </div>
        )}
        {create.state.status === 'error' && (
          <div className="alert alert--danger" role="alert">
            <h3>Payment not completed</h3>
            <p>{create.state.error.message}</p>
          </div>
        )}
      </div>
    </section>
  )
}
