import type { StreamedEvent } from '../hooks/useTicketStream'

export type Phase = 'idle' | 'active' | 'done' | 'error'
export type FlowState = 'idle' | 'flowing' | 'done'

/** An agent's phase, from the last event it emitted. */
export function phaseOf(events: StreamedEvent[]): Phase {
  const last = events.at(-1)
  if (!last) return 'idle'
  if (last.status === 'done') return 'done'
  if (last.status === 'error') return 'error'
  return 'active'
}
