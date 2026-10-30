import type { TicketDetail, TicketStatus } from '../api'
import type { Tone } from './format'

export interface OutcomeBanner {
  tone: Tone
  icon: 'loading' | string
  title: string
  body: string
}

/** Chooses copy from server authority without treating approval as provider delivery. */
export function outcomeBanner(detail: Pick<TicketDetail, 'can_review' | 'fulfillment_state' | 'manual_followup_required' | 'resolution' | 'resolved_by'>, status: TicketStatus): OutcomeBanner {
  if (status === 'processing') {
    return { tone: 'cyan', icon: 'loading', title: 'Agents are working', body: 'Results appear as each agent finishes.' }
  }
  if (status === 'pending') {
    return { tone: 'cyan', icon: '◌', title: 'Fulfillment pending', body: 'A provider outcome is still pending. Do not treat this as customer delivery.' }
  }
  if (detail.fulfillment_state === 'blocked') {
    return { tone: 'rose', icon: '!', title: 'Fulfillment blocked', body: 'Review the blocked operation before taking another action.' }
  }
  if (detail.fulfillment_state === 'failed') {
    return { tone: 'rose', icon: '!', title: 'Delivery failed', body: 'No provider-accepted reply is available. Review the operation details.' }
  }
  if (detail.manual_followup_required) {
    return { tone: 'amber', icon: '!', title: 'Manual follow-up required', body: 'Review the ticket and operation details before contacting the customer.' }
  }
  if (status === 'escalated' && detail.can_review) {
    return { tone: 'amber', icon: '⚑', title: 'Awaiting human review', body: 'Review and act below in Mission Control or via Slack notification.' }
  }
  if (status === 'resolved') {
    if (detail.resolution === 'auto_resolved' || detail.resolution === 'auto_refunded') {
      return { tone: 'emerald', icon: '✓', title: 'Authorization complete', body: 'Provider outcome is shown separately below.' }
    }
    if (detail.resolved_by) {
      return { tone: 'violet', icon: '✓', title: `${detail.resolution === 'edited' ? 'Edited and approved' : 'Approved'} by @${detail.resolved_by}`, body: 'Approval is not proof of email delivery.' }
    }
    return { tone: 'violet', icon: '✓', title: 'Approval recorded', body: 'Approval is not proof of email delivery.' }
  }
  if (status === 'rejected') {
    return { tone: 'rose', icon: '✕', title: detail.resolved_by ? `Rejected by @${detail.resolved_by}` : 'Draft rejected', body: 'No customer reply was authorized. This ticket needs manual handling.' }
  }
  return { tone: 'slate', icon: '•', title: 'Not processed yet', body: 'Run the agents to triage, research and resolve this ticket.' }
}
