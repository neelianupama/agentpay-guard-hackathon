import { useState } from 'react'
import { REQUEST_TEXT_MAX_LENGTH } from '../api/intent'
import { IntentRequestPanel } from '../components/IntentRequestPanel'
import { PaymentSubmissionPanel } from '../components/PaymentSubmissionPanel'
import { RiskAssessmentForm } from '../components/RiskAssessmentForm'
import { RiskResultPanel } from '../components/RiskResultPanel'
import { useIntentParser } from '../hooks/useIntentParser'
import { useRiskAssessment } from '../hooks/useRiskAssessment'
import {
  currencyError,
  EMPTY_PAYMENT_FORM,
  fromParsedIntent,
  parsedIntentErrors,
  pickFieldErrors,
  toTransactionDetails,
  validatePaymentForm,
  type PaymentField,
  type PaymentFormErrors,
  type PaymentFormValues,
} from '../lib/paymentForm'

function withoutField(errors: PaymentFormErrors, field: PaymentField): PaymentFormErrors {
  const next = { ...errors }
  delete next[field]
  return next
}

/**
 * New Payment flow:
 *  1. (Optional) describe the payment in plain language and click "Parse with AI".
 *     The extracted details only pre-fill the form; nothing is assessed.
 *  2. Review and edit the details.
 *  3. Click "Assess Risk" to send the confirmed details to the backend risk engine,
 *     which alone decides the risk score and decision.
 *  4. Optionally submit the assessed details as a payment (POST /api/v1/payments).
 *     The backend re-assesses and decides the payment status; capture is never
 *     automatic and only happens through the reviewer workflow (Approvals page).
 */
export function NewPaymentPage() {
  const [values, setValues] = useState<PaymentFormValues>(EMPTY_PAYMENT_FORM)
  // Client-side and backend (HTTP 422) field errors; a field's error clears when it is edited.
  const [fieldErrors, setFieldErrors] = useState<PaymentFormErrors>({})
  const [requestText, setRequestText] = useState('')
  const [requestError, setRequestError] = useState<string>()
  const risk = useRiskAssessment()
  const intent = useIntentParser()

  const assessing = risk.state.status === 'loading'
  const parsing = intent.state.status === 'loading'

  const handleChange = (field: PaymentField, value: string) => {
    setValues((prev) => ({ ...prev, [field]: value }))
    setFieldErrors((prev) => (prev[field] ? withoutField(prev, field) : prev))
  }

  const handleRequestChange = (value: string) => {
    setRequestText(value)
    setRequestError(undefined)
  }

  const handleParse = async () => {
    const text = requestText.trim()
    if (!text) {
      setRequestError('Enter a payment request to parse.')
      return
    }
    if (text.length > REQUEST_TEXT_MAX_LENGTH) {
      setRequestError(`Payment request must be at most ${REQUEST_TEXT_MAX_LENGTH} characters.`)
      return
    }
    setRequestError(undefined)

    const outcome = await intent.submit(text)
    if (outcome?.status === 'success') {
      // Pre-fill only. Missing details stay empty and are flagged; risk is NOT assessed.
      setValues(fromParsedIntent(outcome.result))
      setFieldErrors(parsedIntentErrors(outcome.result))
      risk.reset()
    } else if (outcome?.status === 'error' && outcome.error.kind === 'validation') {
      setRequestError(outcome.error.fieldErrors.request_text ?? 'Check the payment request and try again.')
    }
  }

  const handleSubmit = async () => {
    const validation = validatePaymentForm(values)
    // Explain unsupported currencies (e.g. JPY from AI parsing) instead of a generic message.
    const currency = currencyError(values.currency)
    if (currency) validation.currency = currency
    setFieldErrors(validation)
    if (Object.keys(validation).length > 0) return

    const outcome = await risk.submit(toTransactionDetails(values))
    if (outcome?.status === 'error' && outcome.error.kind === 'validation') {
      setFieldErrors(pickFieldErrors(outcome.error.fieldErrors))
    }
  }

  return (
    <div className="new-payment">
      <div className="new-payment__main">
        <IntentRequestPanel
          text={requestText}
          error={requestError}
          state={intent.state}
          locked={assessing}
          onChange={handleRequestChange}
          onSubmit={() => void handleParse()}
        />
        <RiskAssessmentForm
          values={values}
          errors={fieldErrors}
          submitting={assessing}
          locked={parsing}
          onChange={handleChange}
          onSubmit={() => void handleSubmit()}
        />
      </div>
      <div className="new-payment__side">
        <RiskResultPanel state={risk.state} />
        {risk.state.status === 'success' && (
          <PaymentSubmissionPanel
            key={risk.state.result.assessed_at}
            assessment={risk.state.result}
            currentDetails={toTransactionDetails(values)}
          />
        )}
      </div>
    </div>
  )
}
