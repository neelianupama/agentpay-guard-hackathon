import type { ColDef, ICellRendererParams, ValueFormatterParams } from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { getPayment, listPayments, MAX_PAGE_SIZE, type Payment, type PaymentList } from '../api/payments'
import { useReviewerToken } from '../auth/reviewerToken'
import { PaymentClientActions } from '../components/PaymentClientActions'
import { PaymentDetails, StatusBadge } from '../components/PaymentDetails'
import { ReviewerTokenPanel } from '../components/ReviewerTokenPanel'
import { useApiAction } from '../hooks/useApiAction'
import { defaultColDef, gridTheme } from '../lib/agGrid'
import { formatDate, formatMoney, STATUS_INFO } from '../lib/paymentDisplay'

/** Rows per grid page (AG Grid Community client-side pagination). */
export const TRANSACTIONS_PAGE_SIZE = 10
const PAGE_SIZES = [10, 20, 50]
const TOKEN_PURPOSE =
  'Payment records include reviewer names and comments, so viewing them requires the reviewer token.'

interface GridContext {
  open: (transactionId: string) => void
  selectedId: string | null
}

function ViewCell({ data, context }: ICellRendererParams<Payment, unknown, GridContext>) {
  if (!data) return null
  return (
    <button
      type="button"
      className="grid-link"
      aria-label={`View payment to ${data.merchant_name}, ${formatMoney(data.amount, data.currency)}`}
      aria-current={context.selectedId === data.transaction_id ? 'true' : undefined}
      onClick={() => context.open(data.transaction_id)}
    >
      View
    </button>
  )
}

function StatusCell({ data }: ICellRendererParams<Payment>) {
  return data ? <StatusBadge status={data.status} /> : null
}

const COLUMNS: ColDef<Payment>[] = [
  { headerName: '', colId: 'view', cellRenderer: ViewCell, sortable: false, filter: false,
    resizable: false, flex: 0, width: 84, minWidth: 84 },
  { headerName: 'Transaction ID', field: 'transaction_id', minWidth: 230, cellClass: 'grid-mono' },
  { headerName: 'Merchant', field: 'merchant_name', minWidth: 150 },
  { headerName: 'Amount', colId: 'amount', type: 'rightAligned', filter: 'agNumberColumnFilter',
    valueGetter: (p) => (p.data ? Number(p.data.amount) : null),
    valueFormatter: (p: ValueFormatterParams<Payment, number | null>) =>
      p.data ? formatMoney(p.data.amount, '').trim() : '' },
  { headerName: 'Currency', field: 'currency', maxWidth: 130 },
  { headerName: 'Status', field: 'status', minWidth: 180, cellRenderer: StatusCell,
    filterValueGetter: (p) => (p.data ? `${STATUS_INFO[p.data.status].label} ${p.data.status}` : '') },
  { headerName: 'Decision', colId: 'decision', valueGetter: (p) => p.data?.risk.decision ?? null, maxWidth: 140 },
  { headerName: 'Created', colId: 'created_at', filter: 'agDateColumnFilter', sort: 'desc', minWidth: 170,
    valueGetter: (p) => (p.data ? new Date(p.data.created_at) : null),
    valueFormatter: (p: ValueFormatterParams<Payment, Date | null>) => (p.data ? formatDate(p.data.created_at) : '') },
]

/**
 * Payment records from GET /api/v1/payments (reviewer token required), shown in an
 * AG Grid Community grid with a detail view per payment. Nothing is requested until
 * a token is entered.
 */
export function TransactionsPage() {
  const { token } = useReviewerToken()
  const list = useApiAction<PaymentList>()
  const detail = useApiAction<Payment>()
  const [selected, setSelected] = useState<Payment | null>(null)
  const { run: runList } = list
  const { run: runDetail } = detail

  const load = useCallback(
    (reviewerToken: string) =>
      void runList((signal) => listPayments({ limit: MAX_PAGE_SIZE, offset: 0 }, reviewerToken, { signal })),
    [runList],
  )

  useEffect(() => {
    if (token) load(token)
  }, [load, token])

  const open = useCallback(
    async (transactionId: string) => {
      const outcome = await runDetail((signal) => getPayment(transactionId, token, { signal }))
      if (outcome?.status === 'success') setSelected(outcome.result)
    },
    [runDetail, token],
  )

  // Buyer actions return only the minimal view; re-read the full record as the reviewer.
  const onActionSettled = async () => {
    if (!selected || !token) return
    const outcome = await runDetail((signal) => getPayment(selected.transaction_id, token, { signal }))
    if (outcome?.status === 'success') setSelected(outcome.result)
    load(token)
  }

  const context = useMemo<GridContext>(
    () => ({ open: (id) => void open(id), selectedId: selected?.transaction_id ?? null }),
    [open, selected],
  )

  if (!token) {
    return (
      <div className="payments-stack">
        <ReviewerTokenPanel purpose={TOKEN_PURPOSE} />
      </div>
    )
  }

  const page = list.state.status === 'success' ? list.state.result : null

  return (
    <div className="payments-stack">
      <ReviewerTokenPanel purpose={TOKEN_PURPOSE} />

      <section className="panel" aria-labelledby="transactions-title">
        <header className="panel__header">
          <div>
            <h2 id="transactions-title">Payments</h2>
            <p>Payment records from the backend. Sort, filter and page with the grid.</p>
          </div>
          <button type="button" className="button button--secondary" onClick={() => load(token)}
            disabled={list.busy}>
            Refresh
          </button>
        </header>

        <div className="payments-page__body">
          {list.state.status === 'loading' && (
            <p className="payments-page__status" role="status">Loading payments…</p>
          )}
          {list.state.status === 'error' && (
            <div className="alert alert--danger" role="alert">
              <h3>Could not load payments</h3>
              <p>{list.state.error.message}</p>
            </div>
          )}
          {page && page.items.length === 0 && (
            <div className="empty-state empty-state--compact">
              <h3>No payments yet</h3>
              <p>Payments submitted from New Payment appear here. Records are kept in backend memory only.</p>
            </div>
          )}
          {page && page.items.length > 0 && (
            <>
              {page.total > page.items.length && (
                <p className="payment-form__note" role="note">
                  Showing the newest {page.items.length} of {page.total} payments.
                </p>
              )}
              <div className="grid-wrap" role="region" aria-label="Payments table">
                <AgGridReact<Payment>
                  theme={gridTheme}
                  rowData={page.items}
                  columnDefs={COLUMNS}
                  defaultColDef={defaultColDef}
                  context={context}
                  getRowId={(p) => p.data.transaction_id}
                  domLayout="autoHeight"
                  pagination
                  paginationPageSize={TRANSACTIONS_PAGE_SIZE}
                  paginationPageSizeSelector={PAGE_SIZES}
                  suppressColumnVirtualisation
                  ensureDomOrder
                  enableCellTextSelection
                />
              </div>
            </>
          )}
        </div>
      </section>

      <section className="panel" aria-labelledby="transaction-detail-title">
        <header className="panel__header">
          <div>
            <h2 id="transaction-detail-title">Payment details</h2>
            <p>Risk decision and workflow status from the backend.</p>
          </div>
        </header>
        <div className="risk-result__body">
          {detail.state.status === 'loading' && <p role="status">Loading payment…</p>}
          {detail.state.status === 'error' && (
            <div className="alert alert--danger" role="alert">
              <h3>Could not load payment</h3>
              <p>{detail.state.error.message}</p>
            </div>
          )}
          {selected && detail.state.status !== 'loading' ? (
            <PaymentDetails payment={selected} titleId="selected-payment-title">
              <PaymentClientActions key={selected.transaction_id} subject={selected}
                onSettled={onActionSettled} />
            </PaymentDetails>
          ) : (
            detail.state.status === 'idle' && (
              <div className="empty-state empty-state--compact">
                <h3>No payment selected</h3>
                <p>Select View on a payment to see its risk decision, status and PayPal details.</p>
              </div>
            )
          )}
        </div>
      </section>
    </div>
  )
}
