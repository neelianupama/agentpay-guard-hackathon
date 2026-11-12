import type { ColDef, ValueFormatterParams } from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import {
  AUDIT_EVENT_TYPES,
  searchAuditEvents,
  type AuditEvent,
  type AuditEventType,
  type AuditQuery,
  type AuditSearchResult,
} from '../api/audit'
import { useReviewerToken } from '../auth/reviewerToken'
import { ReviewerTokenPanel } from '../components/ReviewerTokenPanel'
import { useApiAction } from '../hooks/useApiAction'
import { defaultColDef, gridTheme } from '../lib/agGrid'
import { EVENT_LABELS, eventDetails, reasonLabel } from '../lib/auditDisplay'
import { formatDate } from '../lib/paymentDisplay'

export const AUDIT_PAGE_SIZE = 25
const TRANSACTION_ID = /^[A-Za-z0-9_-]{1,64}$/

const COLUMNS: ColDef<AuditEvent>[] = [
  { headerName: 'Time', colId: 'occurred_at', minWidth: 170, filter: 'agDateColumnFilter',
    valueGetter: (p) => (p.data ? new Date(p.data.occurred_at) : null),
    valueFormatter: (p: ValueFormatterParams<AuditEvent, Date | null>) => (p.data ? formatDate(p.data.occurred_at) : ''),
    comparator: (a: Date | null, b: Date | null, na, nb) =>
      (a?.getTime() ?? 0) - (b?.getTime() ?? 0) || (na.data?.sequence ?? 0) - (nb.data?.sequence ?? 0) },
  { headerName: 'Event', colId: 'event_type', minWidth: 170,
    valueGetter: (p) => (p.data ? EVENT_LABELS[p.data.event_type] : '') },
  { headerName: 'Transaction', field: 'transaction_id', minWidth: 230, cellClass: 'grid-mono',
    valueFormatter: (p) => p.value ?? '—' },
  { headerName: 'Status', colId: 'status', minWidth: 200,
    valueGetter: (p) => {
      const e = p.data
      if (!e?.status) return '—'
      return e.previous_status ? `${e.previous_status} → ${e.status}` : e.status
    } },
  { headerName: 'Action', colId: 'action', minWidth: 160,
    valueGetter: (p) => [p.data?.operation, p.data?.outcome].filter(Boolean).join(' · ') || '—' },
  { headerName: 'Details', colId: 'details', minWidth: 280, flex: 2,
    valueGetter: (p) => (p.data ? eventDetails(p.data) : ''), wrapText: true, autoHeight: true },
]

function StorageStatus({ result }: { result: AuditSearchResult }) {
  if (result.source === 'elastic' && !result.degraded) {
    return (
      <div className="alert alert--success" role="status" aria-label="Audit storage">
        <h3>Durable: stored in Elastic</h3>
        <p>These audit events were read from Elasticsearch.</p>
      </div>
    )
  }
  if (result.source === 'elastic') {
    return (
      <div className="alert alert--warning" role="status" aria-label="Audit storage">
        <h3>Elastic, with events not yet stored</h3>
        <p>
          Results are from Elastic. {result.pending_events} newer event(s) are held only in backend
          memory, are not yet stored in Elastic and are not shown. ({reasonLabel(result.reason)}.)
        </p>
        <Counts result={result} />
      </div>
    )
  }
  return (
    <div className="alert alert--danger" role="status" aria-label="Audit storage">
      <h3>Not durable: memory fallback</h3>
      <p>
        {reasonLabel(result.reason)}. Showing events held temporarily in backend memory. They are
        not durably stored and will be lost if the backend restarts.
      </p>
      <Counts result={result} />
    </div>
  )
}

function Counts({ result }: { result: AuditSearchResult }) {
  return (
    <dl className="audit-counts">
      <div><dt>Pending (not in Elastic)</dt><dd>{result.pending_events}</dd></div>
      <div><dt>Dropped (buffer full)</dt><dd>{result.dropped_events}</dd></div>
    </dl>
  )
}

const EMPTY_QUERY: AuditQuery = { q: '', transaction_id: '', event_type: '', limit: AUDIT_PAGE_SIZE, offset: 0 }

/** Audit trail from GET /api/v1/audit/events (reviewer token required). */
export function AuditLogsPage() {
  const { token } = useReviewerToken()
  const search = useApiAction<AuditSearchResult>()
  const { run } = search
  const [query, setQuery] = useState<AuditQuery>(EMPTY_QUERY)
  const [draft, setDraft] = useState({ q: '', transaction_id: '', event_type: '' as AuditEventType | '' })
  const [txnError, setTxnError] = useState<string | null>(null)
  const ids = { q: useId(), txn: useId(), type: useId() }

  const load = useCallback(
    (reviewerToken: string, nextQuery: AuditQuery) =>
      void run((signal) => searchAuditEvents(nextQuery, reviewerToken, { signal })),
    [run],
  )

  useEffect(() => {
    if (token) load(token, query)
  }, [load, token, query])

  const apply = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const txn = draft.transaction_id.trim()
    if (txn && !TRANSACTION_ID.test(txn)) {
      setTxnError('Transaction IDs contain only letters, digits, "_" and "-".')
      return
    }
    setTxnError(null)
    setQuery({ ...draft, transaction_id: txn, q: draft.q.trim(), limit: AUDIT_PAGE_SIZE, offset: 0 })
  }

  const reset = () => {
    setDraft({ q: '', transaction_id: '', event_type: '' })
    setTxnError(null)
    setQuery(EMPTY_QUERY)
  }

  if (!token) {
    return (
      <div className="payments-stack">
        <ReviewerTokenPanel purpose="The audit trail includes reviewer names, so viewing it requires the reviewer token." />
      </div>
    )
  }

  const result = search.state.status === 'success' ? search.state.result : null
  const from = result && result.total > 0 ? result.offset + 1 : 0
  const to = result ? result.offset + result.items.length : 0

  return (
    <div className="payments-stack">
      <ReviewerTokenPanel purpose="The audit trail includes reviewer names, so viewing it requires the reviewer token." />

      <section className="panel" aria-labelledby="audit-title">
        <header className="panel__header">
          <div>
            <h2 id="audit-title">Audit events</h2>
            <p>Risk decisions, payment status changes, reviewer actions and PayPal outcomes.</p>
          </div>
          <button type="button" className="button button--secondary" disabled={search.busy}
            onClick={() => load(token, query)}>
            Refresh
          </button>
        </header>

        <div className="payments-page__body">
          <form className="audit-filters" onSubmit={apply} aria-label="Audit filters" noValidate>
            <div className="field field--grow">
              <label htmlFor={ids.q}>Search</label>
              <input id={ids.q} type="search" maxLength={200} value={draft.q}
                placeholder="Merchant, reason, reviewer, error code…"
                onChange={(e) => setDraft({ ...draft, q: e.target.value })} />
            </div>
            <div className="field field--grow">
              <label htmlFor={ids.txn}>Transaction ID</label>
              <input id={ids.txn} type="text" maxLength={64} value={draft.transaction_id}
                aria-invalid={txnError ? true : undefined}
                aria-describedby={txnError ? `${ids.txn}-error` : undefined}
                onChange={(e) => setDraft({ ...draft, transaction_id: e.target.value })} />
              {txnError && <p className="field__error" id={`${ids.txn}-error`} role="alert">{txnError}</p>}
            </div>
            <div className="field">
              <label htmlFor={ids.type}>Event type</label>
              <select id={ids.type} value={draft.event_type}
                onChange={(e) => setDraft({ ...draft, event_type: e.target.value as AuditEventType | '' })}>
                <option value="">All events</option>
                {AUDIT_EVENT_TYPES.map((t) => <option key={t} value={t}>{EVENT_LABELS[t]}</option>)}
              </select>
            </div>
            <div className="audit-filters__actions">
              <button type="submit" className="button button--primary" disabled={search.busy}>Search</button>
              <button type="button" className="button button--secondary" onClick={reset}>Reset</button>
            </div>
          </form>

          {search.state.status === 'loading' && <p role="status">Loading audit events…</p>}
          {search.state.status === 'error' && (
            <div className="alert alert--danger" role="alert">
              <h3>Could not load audit events</h3>
              <p>{search.state.error.message}</p>
            </div>
          )}

          {result && <StorageStatus result={result} />}

          {result && result.items.length === 0 && (
            <div className="empty-state empty-state--compact">
              <h3>No audit events</h3>
              <p>No events match these filters yet.</p>
            </div>
          )}

          {result && result.items.length > 0 && (
            <>
              <div className="grid-wrap" role="region" aria-label="Audit events table">
                <AgGridReact<AuditEvent>
                  theme={gridTheme}
                  rowData={result.items}
                  columnDefs={COLUMNS}
                  defaultColDef={defaultColDef}
                  getRowId={(p) => p.data.event_id}
                  domLayout="autoHeight"
                  suppressColumnVirtualisation
                  ensureDomOrder
                  enableCellTextSelection
                />
              </div>
              <nav className="pagination" aria-label="Audit pages">
                <span>Showing {from}–{to} of {result.total}</span>
                <button type="button" className="button button--secondary"
                  disabled={query.offset === 0 || search.busy}
                  onClick={() => setQuery({ ...query, offset: Math.max(0, query.offset - AUDIT_PAGE_SIZE) })}>
                  Previous
                </button>
                <button type="button" className="button button--secondary"
                  disabled={to >= result.total || search.busy}
                  onClick={() => setQuery({ ...query, offset: query.offset + AUDIT_PAGE_SIZE })}>
                  Next
                </button>
              </nav>
            </>
          )}
        </div>
      </section>
    </div>
  )
}
