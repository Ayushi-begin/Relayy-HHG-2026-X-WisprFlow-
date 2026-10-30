import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { TicketState } from '../api'
import { AgentCard } from './AgentCard'
import { recordedModel } from '../lib/format'

afterEach(cleanup)

describe('agent stage truthfulness', () => {
  it('uses only a recorded stage model and otherwise reports no verified model', () => {
    const usage: TicketState['usage'] = [{ agent: 'triage', model: 'configured-model-id', input_tokens: 1, output_tokens: 2, cost_usd: null }]

    expect(recordedModel(usage, 'triage')).toBe('configured-model-id')
    expect(recordedModel(usage, 'research')).toBe('No verified model')
  })

  it('shows simulated provenance and a safe stage error', () => {
    render(
      <AgentCard
        emoji="⚡"
        title="Action"
        model="No verified model"
        provenance="simulated"
        provenanceError="Configured model unavailable"
        role="Proposes a resolution"
        events={[]}
      />,
    )

    expect(screen.getByText('No verified model')).not.toBeNull()
    expect(screen.getByText(/Simulated stage — no verified model run/)).not.toBeNull()
    expect(screen.getByText(/Configured model unavailable/)).not.toBeNull()
  })
})
