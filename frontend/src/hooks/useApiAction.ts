import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'

export type ActionState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; result: T }
  | { status: 'error'; error: ApiError }

/**
 * Request state for one kind of API call (same contract as useRiskAssessment).
 *
 * `run` is ignored while a request is in flight, so double clicks can never
 * send duplicate requests, and resolves to the final state (or `null` if the
 * call was ignored or cancelled). In-flight requests are aborted on unmount.
 */
export function useApiAction<T>() {
  const [state, setState] = useState<ActionState<T>>({ status: 'idle' })
  const inFlight = useRef(false)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(
    () => () => {
      // Abort and forget the in-flight request. Clearing the guard here matters in
      // React StrictMode, which unmounts and remounts once on mount: otherwise the
      // remounted effect's request would be ignored and the page would stay "loading".
      controllerRef.current?.abort()
      controllerRef.current = null
      inFlight.current = false
    },
    [],
  )

  const run = useCallback(
    async (task: (signal: AbortSignal) => Promise<T>): Promise<ActionState<T> | null> => {
      if (inFlight.current) return null
      inFlight.current = true
      const controller = new AbortController()
      controllerRef.current = controller
      setState({ status: 'loading' })
      try {
        const result = await task(controller.signal)
        const next: ActionState<T> = { status: 'success', result }
        setState(next)
        return next
      } catch (err) {
        if (err instanceof ApiError && err.kind === 'aborted') return null
        const error =
          err instanceof ApiError ? err : new ApiError('invalid_response', 'Something went wrong. Please try again.')
        const next: ActionState<T> = { status: 'error', error }
        setState(next)
        return next
      } finally {
        // Only the current request may release the guard; a stale (aborted) one must not.
        if (controllerRef.current === controller) {
          controllerRef.current = null
          inFlight.current = false
        }
      }
    },
    [],
  )

  const reset = useCallback(() => setState({ status: 'idle' }), [])

  return { state, run, reset, busy: state.status === 'loading' }
}
