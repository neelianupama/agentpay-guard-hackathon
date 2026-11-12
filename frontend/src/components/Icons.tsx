import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

function Base({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  )
}

export const ShieldIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3 4.5 6v5.5c0 4.7 3.2 8.6 7.5 9.5 4.3-.9 7.5-4.8 7.5-9.5V6L12 3Z" />
    <path d="m9 12 2.2 2.2L15.5 10" />
  </Base>
)

export const GridIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
  </Base>
)

export const PlusCircleIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 8.5v7M8.5 12h7" />
  </Base>
)

export const ListIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M8.5 6.5h11M8.5 12h11M8.5 17.5h11" />
    <circle cx="4.5" cy="6.5" r=".8" fill="currentColor" />
    <circle cx="4.5" cy="12" r=".8" fill="currentColor" />
    <circle cx="4.5" cy="17.5" r=".8" fill="currentColor" />
  </Base>
)

export const BotIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="4.5" y="8" width="15" height="11" rx="3" />
    <path d="M12 4.5V8M9 13h.01M15 13h.01M9.5 16h5" />
  </Base>
)

export const CheckSquareIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="3.5" y="3.5" width="17" height="17" rx="3" />
    <path d="m8.5 12 2.5 2.5 4.5-5" />
  </Base>
)

export const FileTextIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-10L14 3.5Z" />
    <path d="M14 3.5v5h5M8.5 13h7M8.5 16.5h5" />
  </Base>
)

export const ActivityIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.5 12h4l2.5-6 4 12 2.5-6h4" />
  </Base>
)

export const CheckCircleIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="m8.5 12 2.5 2.5 4.5-5" />
  </Base>
)

export const BanIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="m6 6 12 12" />
  </Base>
)

export const ClockIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Base>
)

export const MenuIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Base>
)

export const CloseIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Base>
)

export const InboxIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.5 13.5 6 5.5h12l2.5 8v5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5v-5Z" />
    <path d="M3.5 13.5H8l1.5 2.5h5l1.5-2.5h4.5" />
  </Base>
)
