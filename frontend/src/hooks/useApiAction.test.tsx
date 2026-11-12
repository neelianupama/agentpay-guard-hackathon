import { act, render, renderHook, screen } from '@testing-library/react'
import { StrictMode, useEffect } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { useApiAction } from './useApiAction'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

/** A task that rejects as "aborted" when its signal aborts, like the real API client. */
function abortableTask<T>(gate: Promise<T>) {
  return (signal: AbortSignal) =>
    new Promise<T>((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new ApiError('aborted', 'cancelled')))
      void gate.then(resolve)
    })
}

type Task = (signal: AbortSignal) => Promise<string>

/** Loads on mount, like TransactionsPage/ApprovalsPage; a button triggers an extra run. */
function MountLoader({ task, onExtra }: { task: Task; onExtra?: (result: unknown) => void }) {
  const { state, run } = useApiAction<string>()
  useEffect(() => {
    void run(task)
  }, [run, task])
  return (
    <>
      <p>{state.status === 'success' ? `loaded:${state.result}` : state.status}</p>
      <button type="button" onClick={() => void run(task).then((r) => onExtra?.(r))}>again</button>
    </>
  )
}

describe('useApiAction', () => {
  it('ignores a second run while one is in flight', async () => {
    const gate = deferred<string>()
    const task = vi.fn(abortableTask(gate.promise))
    const { result } = renderHook(() => useApiAction<string>())

    let second: Promise<unknown> = Promise.resolve()
    act(() => {
      void result.current.run(task)
      second = result.current.run(task)
    })
    await expect(second).resolves.toBeNull()
    await act(async () => gate.resolve('done'))
    expect(task).toHaveBeenCalledTimes(1)
    expect(result.current.state).toEqual({ status: 'success', result: 'done' })
  })

  it('a request started on mount completes under StrictMode (regression)', async () => {
    const gate = deferred<string>()
    const task = vi.fn(abortableTask(gate.promise))

    render(<StrictMode><MountLoader task={task} /></StrictMode>)
    await act(async () => gate.resolve('data'))

    expect(screen.getByText('loaded:data')).toBeInTheDocument()
    expect(task).toHaveBeenCalledTimes(2) // StrictMode ran the effect twice
    expect(task.mock.calls[0][0].aborted).toBe(true) // first request was cancelled
    expect(task.mock.calls[1][0].aborted).toBe(false)
  })

  it('a stale aborted request cannot release the guard for the current one', async () => {
    const gate = deferred<string>()
    const task = vi.fn(abortableTask(gate.promise))
    const extra = vi.fn()
    render(<StrictMode><MountLoader task={task} onExtra={extra} /></StrictMode>)
    // Let the aborted first request settle (its finally runs) while the second is in flight.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })

    await act(async () => screen.getByRole('button', { name: 'again' }).click())

    expect(extra).toHaveBeenCalledWith(null) // ignored: the current request is still in flight
    expect(task).toHaveBeenCalledTimes(2)
    await act(async () => gate.resolve('ok'))
    expect(screen.getByText('loaded:ok')).toBeInTheDocument()
  })
})
