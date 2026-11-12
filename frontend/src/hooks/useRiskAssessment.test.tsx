import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { RiskAssessment } from '../api/risk'
import { VALID_DETAILS, makeAssessment } from '../test/http'
import { useRiskAssessment } from './useRiskAssessment'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useRiskAssessment', () => {
  it('starts idle', () => {
    const { result } = renderHook(() => useRiskAssessment(vi.fn()))
    expect(result.current.state).toEqual({ status: 'idle' })
  })

  it('moves through loading to success', async () => {
    const pending = deferred<RiskAssessment>()
    const assess = vi.fn(() => pending.promise)
    const { result } = renderHook(() => useRiskAssessment(assess))

    let submission: Promise<unknown> = Promise.resolve()
    act(() => {
      submission = result.current.submit(VALID_DETAILS)
    })
    expect(result.current.state.status).toBe('loading')

    const assessment = makeAssessment({ decision: 'REVIEW' })
    await act(async () => {
      pending.resolve(assessment)
      await submission
    })
    expect(result.current.state).toEqual({ status: 'success', result: assessment })
  })

  it('ignores submissions while a request is in flight', async () => {
    const pending = deferred<RiskAssessment>()
    const assess = vi.fn(() => pending.promise)
    const { result } = renderHook(() => useRiskAssessment(assess))

    let first: Promise<unknown> = Promise.resolve()
    let second: Promise<unknown> = Promise.resolve()
    let third: Promise<unknown> = Promise.resolve()
    act(() => {
      first = result.current.submit(VALID_DETAILS)
      second = result.current.submit(VALID_DETAILS)
      third = result.current.submit(VALID_DETAILS)
    })

    await expect(second).resolves.toBeNull()
    await expect(third).resolves.toBeNull()
    expect(assess).toHaveBeenCalledTimes(1)

    await act(async () => {
      pending.resolve(makeAssessment())
      await first
    })
    expect(result.current.state.status).toBe('success')
  })

  it('allows a new submission once the previous one finished', async () => {
    const assess = vi.fn(async () => makeAssessment())
    const { result } = renderHook(() => useRiskAssessment(assess))

    await act(async () => {
      await result.current.submit(VALID_DETAILS)
    })
    await act(async () => {
      await result.current.submit(VALID_DETAILS)
    })
    expect(assess).toHaveBeenCalledTimes(2)
  })

  it('exposes API errors', async () => {
    const error = new ApiError('http', 'The risk service returned an error (HTTP 500).', { status: 500 })
    const { result } = renderHook(() => useRiskAssessment(vi.fn(async () => Promise.reject(error))))

    await act(async () => {
      await result.current.submit(VALID_DETAILS)
    })
    expect(result.current.state).toEqual({ status: 'error', error })
  })

  it('wraps unexpected errors', async () => {
    const { result } = renderHook(() =>
      useRiskAssessment(vi.fn(async () => Promise.reject(new Error('boom')))),
    )
    await act(async () => {
      await result.current.submit(VALID_DETAILS)
    })
    const { state } = result.current
    expect(state.status).toBe('error')
    expect(state.status === 'error' && state.error.kind).toBe('invalid_response')
  })

  it('aborts the in-flight request on unmount', async () => {
    let signal: AbortSignal | undefined
    const assess = vi.fn((_details, options?: { signal?: AbortSignal }) => {
      signal = options?.signal
      return new Promise<RiskAssessment>(() => {})
    })
    const { result, unmount } = renderHook(() => useRiskAssessment(assess))
    act(() => {
      void result.current.submit(VALID_DETAILS)
    })
    await waitFor(() => expect(signal).toBeDefined())
    unmount()
    expect(signal?.aborted).toBe(true)
  })
})
