import { lazy, Suspense, useState } from 'react'
import { ReviewerTokenProvider } from './auth/ReviewerTokenProvider'
import { ActivityEmptyState } from './components/ActivityEmptyState'
import {
  ActivityIcon,
  BanIcon,
  CheckCircleIcon,
  ClockIcon,
  MenuIcon,
} from './components/Icons'
import { MetricCard } from './components/MetricCard'
import { Sidebar } from './components/Sidebar'
import { NAV_ITEMS } from './navigation'
import { IS_MOCK_DATA, mockMetrics, mockTransactions } from './mock/dashboardMock'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { NewPaymentPage } from './pages/NewPaymentPage'
import type { NavKey } from './types'

function OverviewPage({ onNavigate }: { onNavigate: (key: NavKey) => void }) {
  return (
    <>
      <section className="metrics" aria-label="Overview metrics">
        <MetricCard
          label="Total Transactions"
          value={mockMetrics.total}
          hint="All agent-initiated payments"
          tone="neutral"
          icon={ActivityIcon}
        />
        <MetricCard
          label="Approved"
          value={mockMetrics.approved}
          hint="Passed policy or human review"
          tone="success"
          icon={CheckCircleIcon}
        />
        <MetricCard
          label="Blocked"
          value={mockMetrics.blocked}
          hint="Stopped by guardrails"
          tone="danger"
          icon={BanIcon}
        />
        <MetricCard
          label="Pending Review"
          value={mockMetrics.pending}
          hint="Awaiting a human decision"
          tone="warning"
          icon={ClockIcon}
        />
      </section>

      <section className="panel" aria-labelledby="activity-title">
        <header className="panel__header">
          <div>
            <h2 id="activity-title">Transaction Activity</h2>
            <p>Recent payments initiated by your AI agents.</p>
          </div>
        </header>
        {mockTransactions.length === 0 && (
          <ActivityEmptyState onCreatePayment={() => onNavigate('new-payment')} />
        )}
      </section>
    </>
  )
}

// AG Grid pages are code-split so the grid library loads only when they are opened.
const TransactionsPage = lazy(() =>
  import('./pages/TransactionsPage').then((m) => ({ default: m.TransactionsPage })),
)
const AuditLogsPage = lazy(() => import('./pages/AuditLogsPage').then((m) => ({ default: m.AuditLogsPage })))

function PageLoading() {
  return (
    <p className="payment-form__note" role="status">
      Loading…
    </p>
  )
}

function PlaceholderPage({ label }: { label: string }) {
  return (
    <section className="panel">
      <div className="empty-state">
        <h3>{label}</h3>
        <p>This section is not built yet. It will be available in an upcoming release.</p>
      </div>
    </section>
  )
}

export default function App() {
  const [active, setActive] = useState<NavKey>('overview')
  const [navOpen, setNavOpen] = useState(false)

  const current = NAV_ITEMS.find((item) => item.key === active)!

  const navigate = (key: NavKey) => {
    setActive(key)
    setNavOpen(false)
  }

  return (
    <ReviewerTokenProvider>
    <div className="app-shell">
      <Sidebar
        active={active}
        open={navOpen}
        onSelect={navigate}
        onClose={() => setNavOpen(false)}
      />
      {navOpen && <div className="scrim" onClick={() => setNavOpen(false)} />}

      <div className="main">
        <header className="topbar">
          <button
            className="icon-button topbar__menu"
            onClick={() => setNavOpen(true)}
            aria-label="Open navigation"
          >
            <MenuIcon />
          </button>
          <div className="topbar__titles">
            <h1>{active === 'overview' ? 'AgentPay Guard' : current.label}</h1>
            <p className="tagline">Let AI transact. Let humans stay in control.</p>
          </div>
          {IS_MOCK_DATA && active === 'overview' && (
            <span className="mock-badge" title="Values shown are placeholders, not live payment data">
              Demo data
            </span>
          )}
        </header>

        <main className="content">
          {active === 'overview' ? (
            <OverviewPage onNavigate={navigate} />
          ) : active === 'new-payment' ? (
            <NewPaymentPage />
          ) : active === 'transactions' ? (
            <Suspense fallback={<PageLoading />}>
              <TransactionsPage />
            </Suspense>
          ) : active === 'approvals' ? (
            <ApprovalsPage />
          ) : active === 'audit-logs' ? (
            <Suspense fallback={<PageLoading />}>
              <AuditLogsPage />
            </Suspense>
          ) : (
            <PlaceholderPage label={current.label} />
          )}
        </main>
      </div>
    </div>
    </ReviewerTokenProvider>
  )
}
