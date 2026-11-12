import { InboxIcon, PlusCircleIcon } from './Icons'

interface ActivityEmptyStateProps {
  onCreatePayment: () => void
}

export function ActivityEmptyState({ onCreatePayment }: ActivityEmptyStateProps) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon">
        <InboxIcon width={28} height={28} />
      </span>
      <h3>No transaction activity yet</h3>
      <p>
        When an AI agent initiates a payment, it will appear here with its risk
        assessment and approval status. Payments that need a human decision will
        be routed to Approvals.
      </p>
      <button className="button button--primary" onClick={onCreatePayment}>
        <PlusCircleIcon width={18} height={18} />
        Create a payment request
      </button>
    </div>
  )
}
