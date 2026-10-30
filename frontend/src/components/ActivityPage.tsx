import { useEffect, useState } from 'react'
import { API_URL } from '../api'
import { Badge, LiveDot, Panel } from './ui'

interface ActivityItem {
  id: number
  ticket_id: number
  kind: string
  timestamp: string
  agent?: string
  status?: string
  message: string
}

const MAX_ITEMS = 200
const KIND_TONE: Record<string, 'cyan' | 'amber' | 'emerald' | 'violet' | 'rose' | 'slate'> = {
  agent: 'cyan', status: 'slate', review: 'violet', authorization: 'emerald', authorization_failed: 'rose', operation: 'amber',
}

function describe(kind: string, data: Record<string, unknown>): Pick<ActivityItem, 'agent' | 'status' | 'message'> {
  if (kind === 'agent') {
    return { agent: String(data.agent ?? ''), status: String(data.status ?? ''), message: String(data.message ?? '') }
  }
  if (kind === 'status') return { message: `Status → ${String(data.status ?? 'unknown')}` }
  if (kind === 'review') return { message: `Human review: ${String(data.action ?? data.outcome ?? 'recorded')}` }
  if (kind === 'operation') {
    const op = (data.operation ?? {}) as Record<string, unknown>
    return { message: `${String(op.kind ?? 'Delivery')} operation ${String(op.state ?? 'updated')}` }
  }
  return { message: kind.replaceAll('_', ' ') }
}

/** Live command-center feed over GET /events/stream. The browser's EventSource reconnects with
 *  Last-Event-ID automatically and the server replays what was missed; ids deduplicate overlap. */
export function ActivityPage() {
  const [items, setItems] = useState<ActivityItem[]>([])
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    const source = new EventSource(`${API_URL}/events/stream`)
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.addEventListener('reset', () => setItems([]))
    source.addEventListener('event', (message) => {
      try {
        const id = Number((message as MessageEvent<string>).lastEventId)
        const envelope = JSON.parse((message as MessageEvent<string>).data) as {
          ticket_id?: number; kind?: string; timestamp?: string; data?: Record<string, unknown>
        }
        if (!Number.isInteger(id) || typeof envelope.ticket_id !== 'number' || !envelope.kind || !envelope.timestamp) return
        const item: ActivityItem = {
          id, ticket_id: envelope.ticket_id, kind: envelope.kind, timestamp: envelope.timestamp,
          ...describe(envelope.kind, envelope.data ?? {}),
        }
        setItems((list) => (list.some((x) => x.id === id) ? list : [item, ...list].sort((a, b) => b.id - a.id).slice(0, MAX_ITEMS)))
      } catch {
        // ignore malformed messages; the next valid event still renders
      }
    })
    return () => source.close()
  }, [])

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-white">Live activity</h1>
          <p className="text-sm text-slate-400">Every agent step, status change, review and delivery update, as it happens.</p>
        </div>
        <LiveDot on={connected} />
      </header>
      <Panel title={`Recent events (${items.length})`}>
        {items.length === 0 ? (
          <p className="p-6 text-center text-sm text-slate-500">Waiting for activity. Create or reprocess a ticket to see the swarm work.</p>
        ) : (
          <ol className="divide-y divide-white/[0.05]">
            {items.map((item) => (
              <li key={item.id} className="flex items-start gap-3 px-4 py-2.5 text-sm">
                <time className="w-20 shrink-0 font-mono text-[11px] text-slate-500 tabular-nums">
                  {new Date(item.timestamp).toLocaleTimeString()}
                </time>
                <Badge tone={item.status === 'error' ? 'rose' : item.status === 'policy' ? 'amber' : KIND_TONE[item.kind] ?? 'slate'}>
                  {item.agent || item.kind}
                </Badge>
                <span className="min-w-0 flex-1 text-slate-300">{item.message}</span>
                <span className="shrink-0 text-xs text-slate-500">#{item.ticket_id}</span>
              </li>
            ))}
          </ol>
        )}
      </Panel>
    </div>
  )
}
