import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { ReviewerTokenContext } from './reviewerToken'

/** Holds the reviewer token in memory for the current page session only. */
export function ReviewerTokenProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState('')
  const setToken = useCallback((value: string) => setTokenState(value.trim()), [])
  const clearToken = useCallback(() => setTokenState(''), [])
  const value = useMemo(() => ({ token, setToken, clearToken }), [token, setToken, clearToken])
  return <ReviewerTokenContext.Provider value={value}>{children}</ReviewerTokenContext.Provider>
}
