import { createContext, useContext } from 'react'

/**
 * Reviewer token held in application memory only (React state).
 *
 * Never written to localStorage, sessionStorage, cookies or URLs, never logged,
 * never rendered. It is lost on page reload and can be cleared at any time.
 */
export interface ReviewerTokenState {
  token: string
  setToken: (token: string) => void
  clearToken: () => void
}

export const ReviewerTokenContext = createContext<ReviewerTokenState | null>(null)

export function useReviewerToken(): ReviewerTokenState {
  const state = useContext(ReviewerTokenContext)
  if (!state) throw new Error('useReviewerToken must be used inside <ReviewerTokenProvider>')
  return state
}
