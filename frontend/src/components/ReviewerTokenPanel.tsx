import { useId, useState, type FormEvent } from 'react'
import { useReviewerToken } from '../auth/reviewerToken'

/**
 * Enter or clear the reviewer token. The draft is a password input; the token is
 * applied only on submit (no request per keystroke) and kept in memory only.
 */
export function ReviewerTokenPanel({ purpose }: { purpose: string }) {
  const { token, setToken, clearToken } = useReviewerToken()
  const [draft, setDraft] = useState('')
  const inputId = useId()

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!draft.trim()) return
    setToken(draft)
    setDraft('')
  }

  return (
    <section className="panel" aria-labelledby={`${inputId}-title`}>
      <header className="panel__header">
        <div>
          <h2 id={`${inputId}-title`}>Reviewer access</h2>
          <p>{purpose}</p>
        </div>
      </header>
      <div className="payment-form__body">
        {token ? (
          <div className="payment-actions">
            <p className="payment-form__note" role="status">
              Reviewer token set for this session. It is kept only in memory and sent only in the
              Authorization header.
            </p>
            <button type="button" className="button button--secondary" onClick={clearToken}>
              Clear token
            </button>
          </div>
        ) : (
          <form className="field-row" onSubmit={submit}>
            <div className="field field--grow">
              <label htmlFor={inputId}>Reviewer token</label>
              <input
                id={inputId}
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
            </div>
            <div className="field">
              <span aria-hidden="true">&nbsp;</span>
              <button type="submit" className="button button--primary" disabled={!draft.trim()}>
                Use token
              </button>
            </div>
          </form>
        )}
        {!token && (
          <p className="payment-form__note">
            The token is kept only in this browser tab's memory (never stored), sent only in the
            Authorization header, and cleared on reload.
          </p>
        )}
      </div>
    </section>
  )
}
