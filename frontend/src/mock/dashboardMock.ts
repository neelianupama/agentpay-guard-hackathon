/**
 * MOCK DISPLAY DATA — UI placeholder only.
 *
 * Nothing in this file is connected to real payment functionality.
 * No PayPal, Claude, LangGraph or Elastic integration exists yet.
 * Replace these values with API responses from the backend
 * (VITE_API_BASE_URL) once the real endpoints are implemented.
 */

import type { MetricKey } from '../types'

export const IS_MOCK_DATA = true

export const mockMetrics: Record<MetricKey, number> = {
  total: 0,
  approved: 0,
  blocked: 0,
  pending: 0,
}

/** Intentionally empty: no transactions exist until the backend is wired up. */
export const mockTransactions: never[] = []
