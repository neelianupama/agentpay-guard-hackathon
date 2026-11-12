import type { ComponentType, SVGProps } from 'react'

interface MetricCardProps {
  label: string
  value: number
  hint: string
  tone: 'neutral' | 'success' | 'danger' | 'warning'
  icon: ComponentType<SVGProps<SVGSVGElement>>
}

export function MetricCard({ label, value, hint, tone, icon: Icon }: MetricCardProps) {
  return (
    <article className={`metric-card metric-card--${tone}`}>
      <div className="metric-card__head">
        <span className="metric-card__label">{label}</span>
        <span className="metric-card__icon">
          <Icon />
        </span>
      </div>
      <p className="metric-card__value">{value.toLocaleString()}</p>
      <p className="metric-card__hint">{hint}</p>
    </article>
  )
}
