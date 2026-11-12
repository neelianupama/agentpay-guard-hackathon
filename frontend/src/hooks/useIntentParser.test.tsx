import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { ParsedPaymentIntent } from '../api/intent'
import { EXAMPLE_REQUEST, makeParsedIntent } from '../test/http'
import { useIntentParser } from './useIntentParser'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useIntentParser', () => {
  it('starts idle', () => {
    const { result } = renderHook(() => useIntentParser(vi.fn()))
    expect(result.current.state).toEqual({ status: 'idle' })
  })

  it('moves through loading to success', async () => {
    const pending = deferred<ParsedPaymentIntent>()
    const parse = vi.fn(() => pending.promise)
    const { result } = renderHook(() => useIntentParser(parse))

    let submission: Promise<unknown> = Promise.resolve()
    act(() => {
      submission = result.current.submit(EXAMPLE_REQUEST)
    })
    expect(result.current.state.status).toBe('loading')

    const intent = makeParsedIntent()
    await act(async () => {
      pending.resolve(intent)
      await submission
    })
    expect(result.current.state).toEqual({ status: 'success', result: intent })
    expect(parse).toHaveBeenCalledWith(EXAMPLE_REQUEST, { signal: expect.any(AbortSignal) })
  })

  it('ignores submits while a request is in flight', async () => {
    const pending = deferred<ParsedPaymentIntent>()
    const parse = vi.fn(() => pending.promise)
    const { result } = renderHook(() => useIntentParser(parse))

    let first: Promise<unknown> = Promise.resolve()
    let second: Promise<unknown> = Promise.resolve()
    act(() => {
      first = result.current.submit(EXAMPLE_REQUEST)
      second = result.current.submit(EXAMPLE_REQUEST)
    })
    await expect(second).resolves.toBeNull()

    await act(async () => {
      pending.resolve(makeParsedIntent())
      await first
    })
    expect(parse).toHaveBeenCalledTimes(1)
  })

  it('reports API errors', async () => {
    const error = new ApiError('http', 'AI parsing is not available right now.', { status: 503 })
    const { result } = renderHook(() => useIntentParser(vi.fn().mockRejectedValue(error)))

    await act(async () => {
      await result.current.submit(EXAMPLE_REQUEST)
    })
    expect(result.current.state).toEqual({ status: 'error', error })
  })

  it('wraps unexpected errors without exposing their text', async () => {
    const { result } = renderHook(() =>
      useIntentParser(vi.fn().mockRejectedValue(new Error('raw provider stack trace'))),
    )

    await act(async () => {
      await result.current.submit(EXAMPLE_REQUEST)
    })
    const state = result.current.state
    expect(state.status).toBe('error')
    if (state.status === 'error') {
      expect(state.error.message).toBe('Something went wrong while parsing the request.')
    }
  })

  it('aborts the request on unmount', () => {
    let signal: AbortSignal | undefined
    const parse = vi.fn((_text: string, options?: { signal?: AbortSignal }) => {
      signal = options?.signal
      return new Promise<ParsedPaymentIntent>(() => {})
    })
    const { result, unmount } = renderHook(() => useIntentParser(parse))

    act(() => {
      void result.current.submit(EXAMPLE_REQUEST)
    })
    unmount()
    expect(signal?.aborted).toBe(true)
  })
})
