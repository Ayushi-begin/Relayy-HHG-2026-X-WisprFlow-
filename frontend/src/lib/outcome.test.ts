import { describe, expect, it } from 'vitest'
import { outcomeBanner } from './outcome'

const base = {
  can_review: false,
  fulfillment_state: 'not_requested',
  manual_followup_required: false,
  resolution: null,
  resolved_by: null,
} as const

describe('outcomeBanner', () => {
  it('labels pending fulfillment explicitly', () => {
    const banner = outcomeBanner({ ...base, fulfillment_state: 'pending' }, 'pending')
    expect(banner.title).toBe('Fulfillment pending')
    expect(banner.body).toMatch(/Do not treat this as customer delivery/)
  })

  it('does not offer review copy for a blocked operation', () => {
    const banner = outcomeBanner({ ...base, can_review: true, fulfillment_state: 'blocked' }, 'escalated')
    expect(banner.title).toBe('Fulfillment blocked')
    expect(banner.title).not.toBe('Awaiting human review')
  })

  it('recognizes automatic resolution from resolution, not actor text', () => {
    const banner = outcomeBanner({ ...base, resolution: 'auto_resolved' }, 'resolved')
    expect(banner.title).toBe('Authorization complete')
    expect(banner.body).toMatch(/Provider outcome is shown separately/)
  })

  it('shows a failed provider state without claiming a draft or reviewer', () => {
    const banner = outcomeBanner({ ...base, fulfillment_state: 'failed' }, 'escalated')
    expect(banner.title).toBe('Delivery failed')
    expect(banner.body).toMatch(/No provider-accepted reply/)
    expect(banner.title).not.toContain('@null')
  })
})
