import { NAV_ITEMS } from '../navigation'
import type { NavKey } from '../types'
import { CloseIcon, ShieldIcon } from './Icons'

interface SidebarProps {
  active: NavKey
  open: boolean
  onSelect: (key: NavKey) => void
  onClose: () => void
}

export function Sidebar({ active, open, onSelect, onClose }: SidebarProps) {
  return (
    <aside className={`sidebar${open ? ' sidebar--open' : ''}`} aria-label="Primary">
      <div className="sidebar__brand">
        <span className="brand-mark">
          <ShieldIcon width={22} height={22} />
        </span>
        <span className="brand-name">AgentPay Guard</span>
        <button className="icon-button sidebar__close" onClick={onClose} aria-label="Close navigation">
          <CloseIcon />
        </button>
      </div>

      <nav className="sidebar__nav">
        <p className="sidebar__section">Workspace</p>
        <ul>
          {NAV_ITEMS.map(({ key, label, icon: Icon }) => (
            <li key={key}>
              <button
                className={`nav-link${active === key ? ' nav-link--active' : ''}`}
                aria-current={active === key ? 'page' : undefined}
                onClick={() => onSelect(key)}
              >
                <Icon />
                <span>{label}</span>
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <div className="sidebar__footer">
        <span className="env-pill">
          <span className="env-pill__dot" />
          Sandbox · UI preview
        </span>
      </div>
    </aside>
  )
}
