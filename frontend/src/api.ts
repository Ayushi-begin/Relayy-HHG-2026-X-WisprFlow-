// Typed client for the Relayy FastAPI backend. Types mirror backend/swarm/state.py.

export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export type AgentName = 'orchestrator' | 'triage' | 'research' | 'action' | 'human'
export type EventStatus = 'running' | 'tool' | 'policy' | 'done' | 'error'
export type Decision = 'auto_resolve' | 'auto_refund' | 'escalate'
export type TicketStatus = 'open' | 'processing' | 'escalated' | 'pending' | 'resolved' | 'rejected' | 'closed'
export type Urgency = 'low' | 'medium' | 'high' | 'critical'

export interface SwarmEvent {
  timestamp: string
  agent: AgentName
  status: EventStatus
  message: string
}

export interface TriageResult {
  category: 'technical' | 'billing' | 'refund' | 'account' | 'other'
  urgency: Urgency
  sentiment: 'positive' | 'neutral' | 'frustrated' | 'angry'
  language: string
  is_suspicious: boolean
  confidence: number
  summary: string
}

export interface CustomerProfile {
  id: number
  name: string
  email: string
  plan: string
  tier: 'free' | 'pro' | 'vip'
  signup_date: string
  lifetime_value: number
}

export interface ResearchResult {
  customer: CustomerProfile | null
  payments: { id: number; amount: number; date: string; status: string; description: string }[]
  past_tickets: { summary: string; resolution: string; date: string }[]
  relevant_docs: { title: string; content: string }[]
  key_findings: string[]
}

export interface ActionProposal {
  decision: Decision
  reply_draft: string
  holding_reply: string
  refund_amount: number | null
  reasoning: string
  confidence: number
}

export interface ActionResult {
  decision: Decision
  reply_draft: string
  refund_amount: number | null
  reasoning: string
  confidence: number
  proposal: ActionProposal | null
  rules_fired: string[]
}

export interface Usage {
  agent: AgentName
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export interface TicketState {
  ticket: { id: number | null; customer_id: number | null; subject: string; body: string; created_at: string }
  triage: TriageResult | null
  research: ResearchResult | null
  action: ActionResult | null
  events: SwarmEvent[]
  usage: Usage[]
}

export interface TicketSummary {
  id: number
  subject: string
  status: TicketStatus
  customer_id: number
  customer_name: string | null
  customer_tier: string | null
  created_at: string
  updated_at: string | null
  category: string | null
  urgency: Urgency | null
  decision: Decision | null
  rules_fired: string[]
  resolution: string | null
  is_suspicious: boolean
}

export interface TicketDetail {
  id: number
  status: TicketStatus
  customer_name: string | null
  customer_email: string | null
  customer_tier: string | null
  resolution: string | null
  resolved_by: string | null
  final_reply: string | null
  running: boolean
  state: TicketState
}

export interface TimelinePoint {
  ticket_id: number
  decided_at: string | null
  decision: Decision
  category: string
  seconds: number | null
  cost_usd: number
}

export interface Metrics {
  tickets_total: number
  processed: number
  by_status: Record<string, number>
  by_decision: Partial<Record<Decision, number>>
  by_category: Record<string, number>
  auto_resolved_rate: number | null
  escalated_rate: number | null
  avg_time_to_decision_seconds: number | null
  minutes_per_manual_ticket: number
  agent_hours_saved: number
  total_cost_usd: number
  cost_per_ticket_usd: number | null
  cost_by_agent: Partial<Record<AgentName, number>>
  escalations_by_rule: Record<string, number>
  human_reviews: { approved: number; edited: number; rejected: number }
  ai_reply_acceptance_rate: number | null
  suspicious_flagged: number
  auto_refunded_total: number
  timeline: TimelinePoint[]
  generated_at: string
}

export interface NewTicket {
  subject: string
  body: string
  customer_email: string
}

export interface HumanReviewPayload {
  action: 'approved' | 'edited' | 'rejected'
  reply?: string
  reviewer?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // keep the status text
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  listTickets: () => request<TicketSummary[]>('/tickets'),
  getTicket: (id: number) => request<TicketDetail>(`/tickets/${id}`),
  createTicket: (ticket: NewTicket) =>
    request<{ id: number; status: string; stream_url: string }>('/tickets', {
      method: 'POST',
      body: JSON.stringify(ticket),
    }),
  processTicket: (id: number) => request<{ id: number }>(`/tickets/${id}/process`, { method: 'POST' }),
  reviewTicket: (id: number, payload: HumanReviewPayload) =>
    request<TicketDetail>(`/tickets/${id}/review`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  metrics: () => request<Metrics>('/metrics'),
}
