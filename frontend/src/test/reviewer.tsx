import { render, screen, type RenderOptions } from '@testing-library/react'
import type userEvent from '@testing-library/user-event'
import { StrictMode, type ReactElement } from 'react'
import { ReviewerTokenProvider } from '../auth/ReviewerTokenProvider'

export const REVIEWER_TOKEN = 'FAKE-reviewer-token-' + 'z'.repeat(32)

/** Render inside the in-memory reviewer token provider (optionally in StrictMode). */
export function renderWithReviewer(ui: ReactElement, { strict = false, ...options }: RenderOptions & { strict?: boolean } = {}) {
  const tree = <ReviewerTokenProvider>{ui}</ReviewerTokenProvider>
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree, options)
}

/** Enter a reviewer token through the real UI (password field + "Use token"). */
export async function enterReviewerToken(user: ReturnType<typeof userEvent.setup>, token = REVIEWER_TOKEN) {
  await user.type(await screen.findByLabelText('Reviewer token'), token)
  await user.click(screen.getByRole('button', { name: 'Use token' }))
}
