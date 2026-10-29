import { useEffect, useRef, useState } from 'react'
import { API_URL, api } from '../api'
import type { SwarmEvent, TicketDetail, TicketStatus } from '../api'

export interface StreamedEvent extends SwarmEvent {
  id: number
}

interface TicketStream {
  detail: TicketDetail | null
  events: StreamedEvent[]
  status: TicketStatus | null
  connected: boolean
  error: string | null
}

/**
 * Subscribes to GET /tickets/{id}/stream. Events arrive live (history first); whenever an
 * agent finishes, the policy overrides, or the status changes, the full ticket is refetched
 * so the cards can show structured results. Mount it once per ticket (key={ticketId}).
 */
export function useTicketStream(ticketId: number): TicketStream {
  const [detail, setDetail] = useState<TicketDetail | null>(null)
  const [events, setEvents] = useState<StreamedEvent[]>([])
  const [status, setStatus] = useState<TicketStatus | null>(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const refetchTimer = useRef<number | undefined>(undefined)
  const lastStreamStatus = useRef<TicketStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    const refetch = () => {
      window.clearTimeout(refetchTimer.current)
      refetchTimer.current = window.setTimeout(() => {
        api
          .getTicket(ticketId)
          .then((d) => {
            if (!cancelled) {
              setDetail(d)
              setStatus(d.status)
            }
          })
          .catch((e: Error) => !cancelled && setError(e.message))
      }, 120)
    }
    refetch()

    const source = new EventSource(`${API_URL}/tickets/${ticketId}/stream`)
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false) // EventSource reconnects on its own, resuming via Last-Event-ID

    source.addEventListener('event', (e) => {
      const msg = e as MessageEvent<string>
      const event: StreamedEvent = { ...(JSON.parse(msg.data) as SwarmEvent), id: Number(msg.lastEventId) }
      setEvents((prev) => (prev.some((p) => p.id === event.id) ? prev : [...prev, event].sort((a, b) => a.id - b.id)))
      if (event.status !== 'running' && event.status !== 'tool') refetch()
    })

    source.addEventListener('status', (e) => {
      const data = JSON.parse((e as MessageEvent<string>).data) as { status: TicketStatus }
      // The first status message just reports the current state (after the history replay).
      // Only a change *into* processing means a re-run, which starts a fresh event list.
      const previous = lastStreamStatus.current
      lastStreamStatus.current = data.status
      if (data.status === 'processing' && previous !== null && previous !== 'processing') setEvents([])
      setStatus(data.status)
      refetch()
    })

    return () => {
      cancelled = true
      window.clearTimeout(refetchTimer.current)
      source.close()
      setConnected(false)
    }
  }, [ticketId])

  return { detail, events, status, connected, error }
}
