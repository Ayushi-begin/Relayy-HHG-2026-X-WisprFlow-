import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type { TicketDetail } from '../api'
import { useTicketStream } from './useTicketStream'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, getTicket: vi.fn() } }
})

class MockEventSource {
  static latest: MockEventSource | null = null
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  listeners = new Map<string, (event: MessageEvent<string>) => void>()
  constructor(_url: string) {
    MockEventSource.latest = this
  }
  addEventListener(name: string, handler: (event: MessageEvent<string>) => void) {
    this.listeners.set(name, handler)
  }
  close() {}
  emit(name: string, data: unknown, id = '1') {
    this.listeners.get(name)?.({ data: JSON.stringify(data), lastEventId: id } as MessageEvent<string>)
  }
}

const detail: TicketDetail = {
  id: 4, status: 'escalated', customer_name: 'Marcus', customer_email: 'marcus@example.test', customer_tier: 'pro', resolution: null, resolved_by: null,
  final_reply: null, running: false, run_id: 'run-4', version: 7, execution_mode: 'simulated', fulfillment_state: 'not_requested', manual_followup_required: false,
  can_review: true, can_reprocess: true,
  state: {
    ticket: { id: 4, customer_id: 2, subject: 'Need help', body: 'Please help', created_at: '2026-01-01T00:00:00Z' }, triage: null, research: null, action: null,
    events: [{ timestamp: '2026-01-01T00:00:01Z', agent: 'triage', status: 'done', message: 'Triage complete' }], usage: [],
  },
  operations: [],
}

function StreamProbe() {
  const stream = useTicketStream(4)
  return <output>{stream.detail?.state.events[0]?.message ?? 'loading'}:{stream.events.length}:{stream.error ?? 'ok'}</output>
}

describe('useTicketStream', () => {
  it('reconciles replayed non-agent envelopes without errors while retaining nested agent events', async () => {
    vi.stubGlobal('EventSource', MockEventSource)
    const completed: TicketDetail = { ...detail, status: 'resolved', version: 8 }
    vi.mocked(api.getTicket).mockResolvedValue(completed)
    render(<StreamProbe />)

    await vi.waitFor(() => expect(screen.getByText('Triage complete:1:ok')).not.toBeNull())
    for (const [kind, id] of [['status', '9'], ['review', '10'], ['authorization', '11']] as const) {
      MockEventSource.latest!.emit('event', { ticket_id: 4, run_id: 'run-4', kind, timestamp: '2026-01-01T00:00:02Z', data: {} }, id)
    }
    await vi.waitFor(() => expect(api.getTicket).toHaveBeenCalledTimes(2))
    expect(screen.getByText('Triage complete:1:ok')).not.toBeNull()

    MockEventSource.latest!.emit('event', {
      ticket_id: 4,
      run_id: 'run-4',
      kind: 'agent',
      timestamp: '2026-01-01T00:00:03Z',
      data: { agent: 'research', status: 'running', message: 'Research started', timestamp: '2026-01-01T00:00:03Z' },
    }, '12')
    await vi.waitFor(() => expect(screen.getByText('Triage complete:2:ok')).not.toBeNull())

    MockEventSource.latest!.emit('event', { ticket_id: 4, run_id: 'run-4', kind: 'agent', timestamp: '2026-01-01T00:00:04Z', data: {} }, '13')
    await vi.waitFor(() => expect(screen.getByText(/live update could not be read/)).not.toBeNull())
  })
})
