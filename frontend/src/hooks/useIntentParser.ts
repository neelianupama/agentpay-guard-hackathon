import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { parsePaymentIntent, type ParsedPaymentIntent } from '../api/intent'

export type IntentParseState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; result: ParsedPaymentIntent }
  | { status: 'error'; error: ApiError }

/**
 * Request state for natural-language intent parsing.
 *
 * Mirrors `useRiskAssessment`: `submit` is ignored while a request is in
 * flight (no duplicate requests) and resolves to the final state, or `null`
 * if the call was ignored or cancelled. Parsing never triggers assessment.
 */
export function useIntentParser(parse: typeof parsePaymentIntent = parsePaymentIntent) {
  const [state, setState] = useState<IntentParseState>({ status: 'idle' })
  const inFlight = useRef(false)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => () => controllerRef.current?.abort(), [])

  const submit = useCallback(
    async (requestText: string): Promise<IntentParseState | null> => {
      if (inFlight.current) return null
      inFlight.current = true
      const controller = new AbortController()
      controllerRef.current = controller
      setState({ status: 'loading' })
      try {
        const result = await parse(requestText, { signal: controller.signal })
        const next: IntentParseState = { status: 'success', result }
        setState(next)
        return next
      } catch (err) {
        if (err instanceof ApiError && err.kind === 'aborted') return null
        const error =
          err instanceof ApiError
            ? err
            : new ApiError('invalid_response', 'Something went wrong while parsing the request.')
        const next: IntentParseState = { status: 'error', error }
        setState(next)
        return next
      } finally {
        inFlight.current = false
        if (controllerRef.current === controller) controllerRef.current = null
      }
    },
    [parse],
  )

  return { state, submit }
}
