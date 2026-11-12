import type { ComponentType, SVGProps } from 'react'
import {
  BotIcon,
  CheckSquareIcon,
  FileTextIcon,
  GridIcon,
  ListIcon,
  PlusCircleIcon,
} from './components/Icons'
import type { NavKey } from './types'

export interface NavItem {
  key: NavKey
  label: string
  icon: ComponentType<SVGProps<SVGSVGElement>>
}

export const NAV_ITEMS: NavItem[] = [
  { key: 'overview', label: 'Overview', icon: GridIcon },
  { key: 'new-payment', label: 'New Payment', icon: PlusCircleIcon },
  { key: 'transactions', label: 'Transactions', icon: ListIcon },
  { key: 'agent-activity', label: 'Agent Activity', icon: BotIcon },
  { key: 'approvals', label: 'Approvals', icon: CheckSquareIcon },
  { key: 'audit-logs', label: 'Audit Logs', icon: FileTextIcon },
]
