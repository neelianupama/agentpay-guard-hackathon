import type { FormEvent } from 'react'
import { CURRENCY_OPTIONS } from '../api/risk'
import {
  isSupportedCurrency,
  LIMITS,
  type PaymentField,
  type PaymentFormErrors,
  type PaymentFormValues,
} from '../lib/paymentForm'
import { ShieldIcon } from './Icons'

interface RiskAssessmentFormProps {
  values: PaymentFormValues
  errors: PaymentFormErrors
  submitting: boolean
  /** Disables editing and submission without implying an assessment is running (e.g. while parsing). */
  locked?: boolean
  onChange: (field: PaymentField, value: string) => void
  onSubmit: () => void
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null
  return (
    <p className="field__error" id={id} role="alert">
      {message}
    </p>
  )
}

export function RiskAssessmentForm({
  values,
  errors,
  submitting,
  locked = false,
  onChange,
  onSubmit,
}: RiskAssessmentFormProps) {
  const disabled = submitting || locked
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!disabled) onSubmit()
  }
  // Show the actual value (empty or unsupported) instead of letting the select
  // silently display a different currency. It is never changed automatically.
  const currencyIsOffered = isSupportedCurrency(values.currency)

  const fieldProps = (field: PaymentField) => ({
    id: `payment-${field}`,
    name: field,
    value: values[field],
    'aria-invalid': errors[field] ? true : undefined,
    'aria-describedby': errors[field] ? `payment-${field}-error` : undefined,
    onChange: (e: { target: { value: string } }) => onChange(field, e.target.value),
  })

  return (
    <form className="panel payment-form" onSubmit={handleSubmit} noValidate aria-busy={disabled}>
      <header className="panel__header">
        <div>
          <h2>Payment details</h2>
          <p>Describe the payment an agent wants to make. Nothing is charged.</p>
        </div>
      </header>

      <fieldset className="payment-form__body" disabled={disabled}>
        <div className="field">
          <label htmlFor="payment-merchant_name">Merchant name</label>
          <input
            {...fieldProps('merchant_name')}
            type="text"
            autoComplete="organization"
            maxLength={LIMITS.merchant_name}
            placeholder="e.g. Acme Cloud"
          />
          <FieldError id="payment-merchant_name-error" message={errors.merchant_name} />
        </div>

        <div className="field-row">
          <div className="field field--grow">
            <label htmlFor="payment-amount">Amount</label>
            <input
              {...fieldProps('amount')}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              placeholder="0.00"
            />
            <FieldError id="payment-amount-error" message={errors.amount} />
          </div>
          <div className="field field--currency">
            <label htmlFor="payment-currency">Currency</label>
            <select {...fieldProps('currency')}>
              {!currencyIsOffered && values.currency === '' && (
                <option value="" disabled>
                  Select…
                </option>
              )}
              {!currencyIsOffered && values.currency !== '' && (
                <option value={values.currency}>{values.currency} (not supported)</option>
              )}
              {CURRENCY_OPTIONS.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
            <FieldError id="payment-currency-error" message={errors.currency} />
          </div>
        </div>

        <div className="field">
          <label htmlFor="payment-description">Description</label>
          <textarea
            {...fieldProps('description')}
            rows={3}
            maxLength={LIMITS.description}
            placeholder="What is this payment for?"
          />
          <FieldError id="payment-description-error" message={errors.description} />
        </div>

        <div className="field">
          <label htmlFor="payment-purpose">Payment purpose</label>
          <input
            {...fieldProps('purpose')}
            type="text"
            maxLength={LIMITS.purpose}
            placeholder="e.g. Infrastructure hosting"
          />
          <FieldError id="payment-purpose-error" message={errors.purpose} />
        </div>
      </fieldset>

      <footer className="payment-form__footer">
        <p className="payment-form__note">
          <ShieldIcon width={16} height={16} />
          Risk score and decision are computed by the AgentPay Guard backend.
        </p>
        <button type="submit" className="button button--primary" disabled={disabled}>
          {submitting && <span className="spinner" aria-hidden="true" />}
          {submitting ? 'Assessing…' : 'Assess Risk'}
        </button>
      </footer>
    </form>
  )
}
