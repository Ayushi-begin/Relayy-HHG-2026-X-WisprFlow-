import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type { TicketDetail } from '../api'
import { OperationOutcome } from './OperationOutcome'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, recoverOperation: vi.fn() } }
})

const detail: TicketDetail = {
  id: 11,
  status: 'pending',
  customer_name: 'Priya',
  customer_email: 'priya@example.test',
  customer_tier: 'pro',
  resolution: null,
  resolved_by: null,
  final_reply: null,
  running: false,
  run_id: 'run-1',
  version: 4,
  execution_mode: 'test',
  fulfillment_state: 'pending',
  manual_followup_required: false,
  can_review: false,
  can_reprocess: false,
  state: { ticket: { id: 11, customer_id: 1, subject: 'Refund', body: 'Please refund', created_at: '2026-01-01T00:00:00Z' }, triage: null, research: null, action: null, events: [], usage: [] },
  operations: [
    {
      id: 'refund-1', kind: 'refund', state: 'unknown', provider_status: null, provider_id: null, amount_minor: 3000, currency: 'usd', test_mode: true,
      recipient: null, updated_at: '2026-01-01T00:00:00Z', error_code: 'timeout', error_message: 'Timed out', allowed_actions: ['reconcile'],
    },
    {
      id: 'email-1', kind: 'email', state: 'queued', provider_status: null, provider_id: null, amount_minor: null, currency: null, test_mode: true,
      recipient: 'approved@example.test', updated_at: '2026-01-01T00:00:00Z', error_code: null, error_message: null, allowed_actions: [],
    },
  ],
}

afterEach(cleanup)

describe('OperationOutcome', () => {
  it('hides the operation panel when fulfillment was not requested', () => {
    const notRequested: TicketDetail = { ...detail, fulfillment_state: 'not_requested', operations: [] }
    const { container } = render(<OperationOutcome detail={notRequested} token="operator-token" onDetail={vi.fn()} />)

    expect(container.innerHTML).toBe('')
  })

  it('blocks duplicate refund language and uses only server-authorized reconciliation', () => {
    render(<OperationOutcome detail={detail} token="operator-token" onDetail={vi.fn()} />)

    expect(screen.getByText('Refund outcome unknown')).not.toBeNull()
    expect(screen.getAllByText(/Do not submit another refund/)).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Check status' }).hasAttribute('disabled')).toBe(false)
    expect(screen.queryByRole('button', { name: /Retry refund/i })).toBeNull()
  })

  it('posts the current version and replaces the snapshot after a permitted recovery', async () => {
    const onDetail = vi.fn()
    vi.mocked(api.recoverOperation).mockResolvedValue(detail)
    render(<OperationOutcome detail={detail} token="operator-token" onDetail={onDetail} />)

    fireEvent.click(screen.getByRole('button', { name: 'Check status' }))

    await vi.waitFor(() => expect(api.recoverOperation).toHaveBeenCalledWith(11, 'refund-1', 'reconcile', 4, 'operator-token'))
    expect(onDetail).toHaveBeenCalledWith(detail)
  })

  it('does not claim a refund for an email-only provider acceptance', () => {
    const emailOnly: TicketDetail = {
      ...detail,
      operations: [{ ...detail.operations[1], state: 'accepted' }],
    }
    render(<OperationOutcome detail={emailOnly} token="operator-token" onDetail={vi.fn()} />)

    expect(screen.getByText('Email accepted by provider')).not.toBeNull()
    expect(screen.queryByText(/refund succeeded/i)).toBeNull()
  })

  it('keeps required follow-up visible after provider completion', () => {
    const completed: TicketDetail = {
      ...detail,
      manual_followup_required: true,
      operations: [{ ...detail.operations[0], state: 'succeeded', allowed_actions: [] }, { ...detail.operations[1], state: 'accepted' }],
    }
    render(<OperationOutcome detail={completed} token="operator-token" onDetail={vi.fn()} />)

    expect(screen.getByText('Reply accepted; original issue still needs review.')).not.toBeNull()
  })

  it('preserves a succeeded refund and offers only email retry when the server permits it', () => {
    const partial: TicketDetail = {
      ...detail,
      operations: [
        { ...detail.operations[0], state: 'succeeded', allowed_actions: [] },
        { ...detail.operations[1], state: 'failed', allowed_actions: ['retry'], error_code: 'resend_rejected', error_message: 'Rejected' },
      ],
    }
    render(<OperationOutcome detail={partial} token="operator-token" onDetail={vi.fn()} />)

    expect(screen.getByText(/completed refund will not run again/i)).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Retry email only' }).hasAttribute('disabled')).toBe(false)
  })
})
