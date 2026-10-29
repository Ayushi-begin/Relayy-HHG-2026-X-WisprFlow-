// Typed client for the Relayy FastAPI backend. Protected mutations receive an
// operator token from in-memory React state; it is never persisted or placed in a URL.

export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export type AgentName = 'orchestrator' | 'triage' | 'research' | 'action' | 'human'
export type EventStatus = 'running' | 'tool' | 'policy' | 'done' | 'error'
export type Decision = 'auto_resolve' | 'auto_refund' | 'escalate'
export type TicketStatus = 'open' | 'processing' | 'escalated' | 'pending' | 'resolved' | 'rejected' | 'closed'
export type Urgency = 'low' | 'medium' | 'high' | 'critical'
export type OperationKind = 'email' | 'refund'
export type OperationAction = 'retry' | 'reconcile'

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
  cost_usd: number | null
}

export interface TicketState {
  ticket: { id: number | null; customer_id: number | null; subject: string; body: string; created_at: string }
  triage: TriageResult | null
  research: ResearchResult | null
  action: ActionResult | null
  events: SwarmEvent[]
  usage: Usage[]
}

export interface DeliveryOperation {
  id: string
  kind: OperationKind
  state: string
  provider_status: string | null
  provider_id: string | null
  amount_minor: number | null
  currency: string | null
  test_mode: boolean
  recipient: string | null
  updated_at: string
  error_code: string | null
  error_message: string | null
  allowed_actions: OperationAction[]
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
  run_id?: string | null
  fulfillment_state?: string
  email_status?: string | null
  refund_status?: string | null
  legacy_local_only?: boolean
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
  run_id: string | null
  version: number
  execution_mode: string | null
  fulfillment_state: string
  manual_followup_required: boolean
  can_review: boolean
  can_reprocess: boolean
  operations: DeliveryOperation[]
}

export interface TicketRunSummary {
  id: string
  run_number: number
  state: string
  execution_mode: string
  started_at: string
  ended_at: string | null
  review_outcome: string | null
}

export interface TimelinePoint {
  ticket_id: number
  decided_at: string | null
  decision: Decision
  category: string
  seconds: number | null
  cost_usd: number | null
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
  total_cost_usd: number | null
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

export interface DeliveryReadiness {
  configured?: boolean
  ready?: boolean
  state?: string
  reason?: string | null
}

export interface Health {
  status: string
  delivery_mode?: string
  operator_required?: boolean
  providers?: Record<string, DeliveryReadiness>
  email_configured?: boolean
  refund_configured?: boolean
  provider_access_verified?: boolean
}

export interface NewTicket {
  subject: string
  body: string
  customer_email: string
}

export interface HumanReviewPayload {
  action: 'approved' | 'edited' | 'rejected'
  reply?: string
  run_id: string | null
  expected_version: number
}

function operatorHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body: unknown = await res.json()
      if (typeof body === 'object' && body !== null && 'detail' in body) {
        const value = (body as { detail?: unknown }).detail
        detail = typeof value === 'string' ? value : JSON.stringify(value)
      }
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
  listRuns: (id: number) => request<TicketRunSummary[]>(`/tickets/${id}/runs`),
  health: () => request<Health>('/api/health'),
  createTicket: (ticket: NewTicket, token: string) =>
    request<{ id: number; status: string; stream_url: string }>('/tickets', {
      method: 'POST',
      headers: operatorHeaders(token),
      body: JSON.stringify(ticket),
    }),
  processTicket: (id: number, token: string) =>
    request<{ id: number; status: string; stream_url: string }>(`/tickets/${id}/process`, { method: 'POST', headers: operatorHeaders(token) }),
  reviewTicket: (id: number, payload: HumanReviewPayload, token: string) =>
    request<TicketDetail>(`/tickets/${id}/review`, {
      method: 'POST',
      headers: operatorHeaders(token),
      body: JSON.stringify(payload),
    }),
  recoverOperation: (ticketId: number, operationId: string, action: OperationAction, expectedVersion: number, token: string) =>
    request<TicketDetail>(`/tickets/${ticketId}/operations/${operationId}/${action}`, {
      method: 'POST',
      headers: operatorHeaders(token),
      body: JSON.stringify({ expected_version: expectedVersion }),
    }),
  metrics: () => request<Metrics>('/metrics'),
}
