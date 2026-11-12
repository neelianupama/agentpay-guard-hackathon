import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

beforeEach(() => {
  // No test may reach a real backend or PayPal. Tests that need HTTP replace
  // this with mockFetch/routeFetch/routeApi from src/test/http.ts.
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new TypeError('Unmocked fetch in tests: use mockFetch/routeApi'))),
  )
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})
