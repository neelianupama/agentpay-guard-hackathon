import type { FormEvent } from 'react'
import { REQUEST_TEXT_MAX_LENGTH, type ParsedPaymentIntent } from '../api/intent'
import type { IntentParseState } from '../hooks/useIntentParser'
import { FIELD_LABELS, isSupportedCurrency, PAYMENT_FIELDS } from '../lib/paymentForm'
import { ShieldIcon } from './Icons'

interface IntentRequestPanelProps {
  text: string
  error?: string
  state: IntentParseState
  /** Disable parsing, e.g. while a risk assessment is running. */
  locked?: boolean
  onChange: (value: string) => void
  onSubmit: () => void
}

export function IntentRequestPanel({
  text,
  error,
  state,
  locked = false,
  onChange,
  onSubmit,
}: IntentRequestPanelProps) {
  const parsing = state.status === 'loading'
  const disabled = parsing || locked

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!disabled) onSubmit()
  }

  return (
    <form
      className="panel intent-panel"
      onSubmit={handleSubmit}
      noValidate
      aria-busy={parsing}
      aria-labelledby="intent-panel-title"
    >
      <header className="panel__header">
        <div>
          <h2 id="intent-panel-title">Describe the payment</h2>
          <p>Optional: let AI fill in the details below from a plain-language request.</p>
        </div>
      </header>

      <fieldset className="payment-form__body" disabled={disabled}>
        <div className="field">
          <label htmlFor="intent-request_text">Payment request</label>
          <textarea
            id="intent-request_text"
            name="request_text"
            rows={3}
            maxLength={REQUEST_TEXT_MAX_LENGTH}
            placeholder="e.g. Pay 250 USD to ABC Technologies for software services for the Q3 tooling budget."
            value={text}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'intent-request_text-error' : undefined}
            onChange={(e) => onChange(e.target.value)}
          />
          {error && (
            <p className="field__error" id="intent-request_text-error" role="alert">
              {error}
            </p>
          )}
        </div>
      </fieldset>

      <footer className="payment-form__footer">
        <p className="payment-form__note">
          <ShieldIcon width={16} height={16} />
          AI only drafts the details. It does not assess risk, approve or make payments.
        </p>
        <button type="submit" className="button button--secondary" disabled={disabled}>
          {parsing && <span className="spinner" aria-hidden="true" />}
          {parsing ? 'Parsing…' : 'Parse with AI'}
        </button>
      </footer>

      <IntentStatus state={state} />
    </form>
  )
}

function IntentStatus({ state }: { state: IntentParseState }) {
  switch (state.status) {
    case 'idle':
      return null
    case 'loading':
      return (
        <div className="intent-panel__status" role="status">
          Reading your request…
        </div>
      )
    case 'error':
      return (
        <div className="intent-panel__status">
          <div className="alert alert--danger" role="alert">
            <h3>AI parsing failed</h3>
            <p>{state.error.message}</p>
          </div>
        </div>
      )
    case 'success':
      return <ExtractedSummary intent={state.result} />
  }
}

function ExtractedSummary({ intent }: { intent: ParsedPaymentIntent }) {
  const missing = intent.missing_fields
  const unsupportedCurrency =
    intent.currency !== null && !isSupportedCurrency(intent.currency) ? intent.currency : null
  const needsAttention = intent.clarification_needed || unsupportedCurrency !== null

  return (
    <section className="intent-panel__status" aria-labelledby="intent-summary-title">
      <h3 id="intent-summary-title" className="intent-summary__title">
        Extracted by AI
      </h3>

      <dl className="intent-summary">
        {PAYMENT_FIELDS.map((field) => {
          const value = intent[field]
          return (
            <div key={field} className="intent-summary__row">
              <dt>{FIELD_LABELS[field]}</dt>
              <dd className={value === null ? 'intent-summary__missing' : undefined}>
                {value ?? 'Not found'}
                {field === 'currency' && unsupportedCurrency && ' (not supported)'}
              </dd>
            </div>
          )
        })}
      </dl>

      <div
        className={`alert ${needsAttention ? 'alert--warning' : 'alert--info'}`}
        role={needsAttention ? 'alert' : 'status'}
      >
        {needsAttention ? (
          <>
            <h3>Clarification needed</h3>
            {missing.length > 0 && (
              <p>Missing: {missing.map((f) => FIELD_LABELS[f]).join(', ')}.</p>
            )}
            {unsupportedCurrency && (
              <p>
                {unsupportedCurrency} is not supported for risk assessment. It has not been converted.
              </p>
            )}
            {missing.length === 0 && !unsupportedCurrency && (
              <p>The AI flagged this request as ambiguous. Check every detail carefully.</p>
            )}
            <p>Complete or correct the payment details below before assessing risk.</p>
          </>
        ) : (
          <>
            <h3>Review before assessing</h3>
            <p>All details were found. Check them below, then click Assess Risk.</p>
          </>
        )}
      </div>
    </section>
  )
}
