import { useEffect, useRef, useState } from 'react'
import { API_URL } from '../api'

/**
 * Subscribes to GET /events/stream and calls `onChange` (debounced) whenever any ticket is
 * created or changes status. Returns whether the stream is currently connected.
 */
export function useLiveUpdates(onChange: () => void, debounceMs = 250): boolean {
  const [connected, setConnected] = useState(false)
  const callback = useRef(onChange)
  useEffect(() => {
    callback.current = onChange
  })

  useEffect(() => {
    let timer: number | undefined
    const refresh = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => callback.current(), debounceMs)
    }
    const source = new EventSource(`${API_URL}/events/stream`)
    source.onopen = () => {
      setConnected(true)
      refresh() // A global stream is advisory; refresh summaries after every reconnect.
    }
    source.onerror = () => setConnected(false)
    source.addEventListener('status', refresh)
    return () => {
      window.clearTimeout(timer)
      source.close()
    }
  }, [debounceMs])

  return connected
}
