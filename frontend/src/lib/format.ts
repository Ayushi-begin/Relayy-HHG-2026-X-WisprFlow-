import type { Decision, TicketStatus, Urgency } from '../api'

export type Tone = 'slate' | 'cyan' | 'amber' | 'emerald' | 'violet' | 'rose' | 'orange' | 'red'

// Tailwind classes per tone: soft fill + text + ring, for badges on the dark surface.
export const toneClasses: Record<Tone, string> = {
  slate: 'bg-slate-400/10 text-slate-300 ring-slate-400/20',
  cyan: 'bg-cyan-400/10 text-cyan-300 ring-cyan-400/25',
  amber: 'bg-amber-400/10 text-amber-300 ring-amber-400/25',
  emerald: 'bg-emerald-400/10 text-emerald-300 ring-emerald-400/25',
  violet: 'bg-violet-400/10 text-violet-300 ring-violet-400/25',
  rose: 'bg-rose-400/10 text-rose-300 ring-rose-400/25',
  orange: 'bg-orange-400/10 text-orange-300 ring-orange-400/25',
  red: 'bg-red-500/15 text-red-300 ring-red-400/30',
}

export const urgencyMeta: Record<Urgency, { label: string; tone: Tone; dot: string }> = {
  critical: { label: 'Critical', tone: 'red', dot: 'bg-red-400' },
  high: { label: 'High', tone: 'orange', dot: 'bg-orange-400' },
  medium: { label: 'Medium', tone: 'amber', dot: 'bg-amber-300' },
  low: { label: 'Low', tone: 'emerald', dot: 'bg-emerald-400' },
}

export function statusMeta(status: TicketStatus, resolution: string | null): { label: string; tone: Tone; icon: string } {
  switch (status) {
    case 'processing':
      return { label: 'Processing', tone: 'cyan', icon: '◌' }
    case 'escalated':
      return { label: 'Escalated', tone: 'amber', icon: '⚑' }
    case 'rejected':
      return { label: 'Rejected', tone: 'rose', icon: '✕' }
    case 'resolved':
      if (resolution === 'auto_refunded') return { label: 'Auto-refunded', tone: 'emerald', icon: '✓' }
      if (resolution === 'auto_resolved') return { label: 'Auto-resolved', tone: 'emerald', icon: '✓' }
      if (resolution === 'edited') return { label: 'Approved · edited', tone: 'violet', icon: '✓' }
      return { label: 'Approved', tone: 'violet', icon: '✓' }
    case 'open':
      return { label: 'New', tone: 'slate', icon: '•' }
    default:
      return { label: status, tone: 'slate', icon: '•' }
  }
}

export const decisionMeta: Record<Decision, { label: string; tone: Tone; icon: string }> = {
  auto_resolve: { label: 'Auto-resolve', tone: 'emerald', icon: '✓' },
  auto_refund: { label: 'Auto-refund', tone: 'emerald', icon: '↺' },
  escalate: { label: 'Escalate to human', tone: 'amber', icon: '⚑' },
}

// Mirrors backend/swarm/policy.py RULES.
export const ruleLabels: Record<string, string> = {
  no_proposal: 'The action model produced no usable proposal',
  missing_triage: 'Triage did not complete',
  suspicious_ticket: 'Triage flagged the ticket as suspicious',
  critical_urgency: 'Triage rated the ticket critical',
  vip_angry: 'VIP customer with angry sentiment',
  low_confidence: 'Model confidence below 0.75',
  refund_over_limit: 'Auto-refunds are limited to $50',
  no_matching_payment: 'No successful payment matches the refund amount',
}

export const tierTone: Record<string, Tone> = { vip: 'violet', pro: 'cyan', free: 'slate' }

export function money(n: number, digits = 2): string {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function timeAgo(iso: string, now = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (seconds < 45) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function pct(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`
}
