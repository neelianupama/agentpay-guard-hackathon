import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { assessRisk, type RiskAssessment, type TransactionDetails } from '../api/risk'

export type RiskAssessmentState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; result: RiskAssessment }
  | { status: 'error'; error: ApiError }

/**
 * Request state for a risk assessment.
 *
 * `submit` is ignored while a request is in flight, so double clicks or
 * repeated Enter presses can never send duplicate requests. It resolves to
 * the final state, or `null` if the call was ignored or cancelled.
 */
export function useRiskAssessment(assess: typeof assessRisk = assessRisk) {
  const [state, setState] = useState<RiskAssessmentState>({ status: 'idle' })
  const inFlight = useRef(false)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => () => controllerRef.current?.abort(), [])

  const submit = useCallback(
    async (details: TransactionDetails): Promise<RiskAssessmentState | null> => {
      if (inFlight.current) return null
      inFlight.current = true
      const controller = new AbortController()
      controllerRef.current = controller
      setState({ status: 'loading' })
      try {
        const result = await assess(details, { signal: controller.signal })
        const next: RiskAssessmentState = { status: 'success', result }
        setState(next)
        return next
      } catch (err) {
        if (err instanceof ApiError && err.kind === 'aborted') return null
        const error =
          err instanceof ApiError
            ? err
            : new ApiError('invalid_response', 'Something went wrong while assessing risk.')
        const next: RiskAssessmentState = { status: 'error', error }
        setState(next)
        return next
      } finally {
        inFlight.current = false
        if (controllerRef.current === controller) controllerRef.current = null
      }
    },
    [assess],
  )

  const reset = useCallback(() => setState({ status: 'idle' }), [])

  return { state, submit, reset }
}
