import { useEffect, useRef } from 'react'
import type { PolicyDecision, RiskLevel } from '../api/risk'
import type { RiskAssessmentState } from '../hooks/useRiskAssessment'
import { BanIcon, CheckCircleIcon, ClockIcon, ShieldIcon } from './Icons'

const DECISION_COPY: Record<PolicyDecision, { title: string; detail: string }> = {
  ALLOW: {
    title: 'Allowed by policy',
    detail: 'This payment meets policy and may proceed without human review.',
  },
  REVIEW: {
    title: 'Human review required',
    detail: 'A person must approve this payment before it can proceed.',
  },
  BLOCK: {
    title: 'Blocked by policy',
    detail: 'This payment must not proceed.',
  },
}

const DECISION_ICON = { ALLOW: CheckCircleIcon, REVIEW: ClockIcon, BLOCK: BanIcon } as const

const LEVEL_TONE: Record<RiskLevel, string> = {
  LOW: 'success',
  MEDIUM: 'warning',
  HIGH: 'danger',
  CRITICAL: 'critical',
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
}

function formatAmount(amount: string, currency: string): string {
  const [whole, fraction = ''] = amount.split('.')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${grouped}.${fraction.padEnd(2, '0')} ${currency}`
}

/** Space (px) the panel's top must leave below it to count as "in view". */
const MIN_VISIBLE_PX = 160

/**
 * In the single-column layout (narrow screens) the panel sits below the
 * forms, so a new result can appear entirely off-screen. Bring it into view
 * when an assessment finishes, unless its top is already visible.
 */
function useRevealOnResult(state: RiskAssessmentState) {
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    if (state.status !== 'success' && state.status !== 'error') return
    const el = ref.current
    if (!el || typeof el.scrollIntoView !== 'function') return
    const { top } = el.getBoundingClientRect()
    if (top >= 0 && top <= window.innerHeight - MIN_VISIBLE_PX) return
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' })
  }, [state])
  return ref
}

export function RiskResultPanel({ state }: { state: RiskAssessmentState }) {
  const ref = useRevealOnResult(state)
  return (
    <section
      ref={ref}
      className="panel risk-result"
      aria-labelledby="risk-result-title"
      aria-live="polite"
    >
      <header className="panel__header">
        <div>
          <h2 id="risk-result-title">Risk assessment</h2>
          <p>Result returned by the backend policy engine.</p>
        </div>
      </header>
      <div className="risk-result__body">
        <ResultBody state={state} />
      </div>
    </section>
  )
}

function ResultBody({ state }: { state: RiskAssessmentState }) {
  switch (state.status) {
    case 'idle':
      return (
        <div className="empty-state empty-state--compact">
          <span className="empty-state__icon">
            <ShieldIcon width={26} height={26} />
          </span>
          <h3>No assessment yet</h3>
          <p>Fill in the payment details and select Assess Risk to see the backend's decision.</p>
        </div>
      )

    case 'loading':
      return (
        <div className="risk-result__loading" role="status">
          <span className="spinner spinner--lg" aria-hidden="true" />
          <p>Assessing risk…</p>
        </div>
      )

    case 'error': {
      const { error } = state
      return (
        <div className="alert alert--danger" role="alert">
          <h3>Assessment failed</h3>
          <p>{error.message}</p>
          {error.kind === 'validation' && (
            <p>Correct the highlighted fields and try again.</p>
          )}
          {error.fieldErrors._form && <p>{error.fieldErrors._form}</p>}
        </div>
      )
    }

    case 'success': {
      const { result } = state
      const copy = DECISION_COPY[result.decision]
      const Icon = DECISION_ICON[result.decision]
      const tone = LEVEL_TONE[result.risk_level]
      return (
        <div className="risk-result__content">
          <div className={`decision-banner decision-banner--${result.decision.toLowerCase()}`}>
            <span className="decision-banner__icon">
              <Icon width={24} height={24} />
            </span>
            <div>
              <p className="decision-banner__label">Policy decision</p>
              <p className="decision-banner__value">
                {result.decision}
                <span> · {copy.title}</span>
              </p>
              <p className="decision-banner__detail">{copy.detail}</p>
            </div>
          </div>

          <dl className="risk-stats">
            <div className="risk-stat">
              <dt>Risk score</dt>
              <dd>
                <span className="risk-stat__score">{result.risk_score}</span>
                <span className="risk-stat__max">/ 100</span>
              </dd>
              <div
                className={`risk-meter risk-meter--${tone}`}
                role="meter"
                aria-label="Risk score"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={result.risk_score}
              >
                <span style={{ width: `${result.risk_score}%` }} />
              </div>
            </div>
            <div className="risk-stat">
              <dt>Risk level</dt>
              <dd>
                <span className={`level-badge level-badge--${tone}`}>{result.risk_level}</span>
              </dd>
            </div>
            <div className="risk-stat">
              <dt>Assessed</dt>
              <dd>
                <time dateTime={result.assessed_at} title={result.assessed_at}>
                  {formatTimestamp(result.assessed_at)}
                </time>
              </dd>
            </div>
          </dl>

          <div className="risk-reasons">
            <h3>Reasons</h3>
            <ul>
              {result.reasons.map((reason, index) => (
                <li key={index}>{reason}</li>
              ))}
            </ul>
          </div>

          <div className="assessed-transaction">
            <h3>Assessed payment</h3>
            <p>
              <strong>{result.transaction.merchant_name}</strong> ·{' '}
              {formatAmount(result.transaction.amount, result.transaction.currency)}
            </p>
            <p className="assessed-transaction__meta">
              {result.transaction.description} — {result.transaction.purpose}
            </p>
          </div>

          <p className="risk-result__footnote">
            This is a risk assessment only. No payment has been made.
          </p>
        </div>
      )
    }
  }
}
