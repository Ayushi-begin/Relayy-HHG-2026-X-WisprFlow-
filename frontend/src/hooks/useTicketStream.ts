import { useEffect, useRef, useState } from 'react'
import { API_URL, api } from '../api'
import type { SwarmEvent, TicketDetail, TicketStatus } from '../api'

export interface StreamedEvent extends SwarmEvent {
  id: number
  ticket_id?: number
  run_id?: string | null
  version?: number
}

interface TicketEventEnvelope {
  ticket_id: number
  run_id: string | null
  kind: string
  timestamp: string
  data: unknown
}


interface TicketStream {
  detail: TicketDetail | null
  events: StreamedEvent[]
  status: TicketStatus | null
  connected: boolean
  error: string | null
  replaceDetail: (detail: TicketDetail) => void
}

const snapshotEvents = (detail: TicketDetail): StreamedEvent[] =>
  detail.state.events.map((event, index) => ({ ...event, id: -(index + 1), run_id: detail.run_id, version: detail.version }))

/**
 * Hydrates the full server snapshot before consuming advisory SSE messages. Snapshots stay
 * authoritative: reconnects and malformed/out-of-order events simply trigger a fresh GET.
 */
export function useTicketStream(ticketId: number): TicketStream {
  const [detail, setDetail] = useState<TicketDetail | null>(null)
  const [events, setEvents] = useState<StreamedEvent[]>([])
  const [status, setStatus] = useState<TicketStatus | null>(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const refetchTimer = useRef<number | undefined>(undefined)
  const requestSequence = useRef(0)
  const detailVersion = useRef(-1)
  const currentRunId = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const replaceDetail = (next: TicketDetail) => {
      if (next.version < detailVersion.current) return
      detailVersion.current = next.version
      currentRunId.current = next.run_id
      setDetail(next)
      setStatus(next.status)
      // Snapshots (initial fetch, reconnect, and server snapshot events) are authoritative.
      setEvents(snapshotEvents(next))
      setError(null)
    }
    const fetchDetail = () => {
      const sequence = ++requestSequence.current
      api
        .getTicket(ticketId)
        .then((next) => {
          if (!cancelled && sequence === requestSequence.current) replaceDetail(next)
        })
        .catch((e: Error) => !cancelled && setError(e.message))
    }
    const refetch = () => {
      window.clearTimeout(refetchTimer.current)
      refetchTimer.current = window.setTimeout(fetchDetail, 120)
    }

    fetchDetail()
    const source = new EventSource(`${API_URL}/tickets/${ticketId}/stream`)
    source.onopen = () => {
      if (cancelled) return
      setConnected(true)
      refetch()
    }
    source.onerror = () => {
      if (!cancelled) setConnected(false)
    }

    source.addEventListener('snapshot', (message) => {
      try {
        const snapshot = JSON.parse((message as MessageEvent<string>).data) as TicketDetail
        if (snapshot.id !== ticketId || !Number.isInteger(snapshot.version)) throw new Error('Invalid ticket snapshot')
        replaceDetail(snapshot)
      } catch {
        setError('A live snapshot could not be read. Refreshing the ticket state.')
        refetch()
      }
    })

    source.addEventListener('reset', refetch)
    source.addEventListener('event', (message) => {
      try {
        const envelope = JSON.parse((message as MessageEvent<string>).data) as Partial<TicketEventEnvelope>
        const id = Number((message as MessageEvent<string>).lastEventId)
        if (!Number.isInteger(id) || id < 0 || envelope.ticket_id !== ticketId || !envelope.kind || typeof envelope.timestamp !== 'string') {
          throw new Error('Invalid ticket event envelope')
        }
        if (envelope.run_id && currentRunId.current && envelope.run_id !== currentRunId.current) {
          refetch()
          return
        }
        // Durable non-agent records (and future event kinds) are represented only by the next snapshot.
        if (envelope.kind !== 'agent') {
          // Known status/review/authorization records and unknown future kinds reconcile safely.
          refetch()
          return
        }
        const data = envelope.data as Partial<SwarmEvent>
        if (typeof data?.message !== 'string' || typeof data.agent !== 'string' || typeof data.status !== 'string') {
          throw new Error('Invalid agent event')
        }
        const event: StreamedEvent = {
          ...(data as SwarmEvent),
          timestamp: data.timestamp ?? envelope.timestamp,
          id,
          ticket_id: envelope.ticket_id,
          run_id: envelope.run_id,
        }
        setEvents((previous) => (previous.some((item) => item.id === event.id) ? previous : [...previous, event].sort((a, b) => a.id - b.id)))
        if (event.status !== 'running' && event.status !== 'tool') refetch()
      } catch {
        setError('A live update could not be read. Refreshing the ticket state.')
        refetch()
      }
    })

    source.addEventListener('status', (message) => {
      try {
        const data = JSON.parse((message as MessageEvent<string>).data) as { status?: TicketStatus; run_id?: string | null; version?: number }
        if (!data.status || (data.version !== undefined && data.version < detailVersion.current)) throw new Error('Invalid ticket status')
        setStatus(data.status)
        refetch()
      } catch {
        setError('A live status update could not be read. Refreshing the ticket state.')
        refetch()
      }
    })

    return () => {
      cancelled = true
      window.clearTimeout(refetchTimer.current)
      source.close()
      setConnected(false)
    }
  }, [ticketId])

  const replaceDetail = (next: TicketDetail) => {
    if (next.version < detailVersion.current) return
    detailVersion.current = next.version
    currentRunId.current = next.run_id
    setDetail(next)
    setStatus(next.status)
    setEvents(snapshotEvents(next))
    setError(null)
  }

  return { detail, events, status, connected, error, replaceDetail }
}
