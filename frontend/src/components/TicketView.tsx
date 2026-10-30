import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { api } from '../api'
import type { ActionResult, ResearchResult, TicketDetail, TriageResult } from '../api'
import { useTicketStream } from '../hooks/useTicketStream'
import type { StreamedEvent } from '../hooks/useTicketStream'
import { decisionMeta, money, recordedModel, ruleLabels, statusMeta, tierTone, timeAgo, toneClasses, urgencyMeta } from '../lib/format'
import { outcomeBanner } from '../lib/outcome'
import { phaseOf } from '../lib/phase'
import type { FlowState } from '../lib/phase'
import { AgentCard, FlowConnector } from './AgentCard'
import { OperationOutcome } from './OperationOutcome'
import { Badge, ConfidenceBar, LiveDot, Spinner } from './ui'

const sentimentEmoji = { positive: '🙂', neutral: '😐', frustrated: '😣', angry: '😡' } as const

export function TicketView({ ticketId, operatorToken }: { ticketId: number | null; operatorToken: string | null }) {
  if (ticketId === null) {
    return (
      <div className="grid h-full place-items-center p-10 text-center">
        <div>
          <div className="mx-auto mb-4 grid size-14 place-items-center rounded-2xl bg-white/[0.04] text-2xl ring-1 ring-white/[0.08]">🛰️</div>
          <p className="text-sm text-slate-300">Select a ticket to watch the agents work</p>
          <p className="mt-1 text-xs text-slate-500">or fire one of the demo presets on the right</p>
        </div>
      </div>
    )
  }
  return <TicketStreamView key={ticketId} ticketId={ticketId} operatorToken={operatorToken} />
}

function TicketStreamView({ ticketId, operatorToken }: { ticketId: number; operatorToken: string | null }) {
  const { detail, events, status, connected, error, replaceDetail } = useTicketStream(ticketId)

  if (error && !detail) return <p className="p-8 text-sm text-rose-300">Couldn't load ticket #{ticketId}: {error}</p>
  if (!detail) {
    return (
      <div className="grid h-full place-items-center text-slate-500">
        <Spinner className="size-5" />
      </div>
    )
  }

  const byAgent = (agent: StreamedEvent['agent']) => events.filter((e) => e.agent === agent)
  const triageEvents = byAgent('triage')
  const researchEvents = byAgent('research')
  const actionEvents = byAgent('action')
  const phases = [phaseOf(triageEvents), phaseOf(researchEvents), phaseOf(actionEvents)]
  const flow = (i: number): FlowState => (phases[i] === 'active' ? 'flowing' : phases[i] === 'idle' ? 'idle' : 'done')
  const { triage, research, action } = detail.state
  const currentStatus = status ?? detail.status
  const finished = currentStatus !== 'processing' && phases[2] !== 'idle'

  return (
    <motion.div
      key={ticketId}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="mx-auto max-w-3xl px-1 pb-10"
    >
      {error && <p role="alert" className="mb-3 rounded-xl bg-rose-500/10 px-3 py-2 text-xs text-rose-200 ring-1 ring-rose-400/25">{error}</p>}
      <TicketHeader detail={detail} status={currentStatus} connected={connected} operatorToken={operatorToken} onDetail={replaceDetail} />

      <FlowConnector state={phases[0] === 'idle' ? (currentStatus === 'processing' ? 'flowing' : 'idle') : 'done'} />
      <AgentCard
        emoji="🧭"
        title="Triage"
        model={recordedModel(detail.state.usage, 'triage')}
        provenance={detail.state.provenance?.triage ?? 'unknown'}
        provenanceError={detail.state.provenance_errors?.triage}
        role="Classifies category, urgency, sentiment and manipulation risk"
        events={triageEvents}
      >
        {triage && <TriageResults triage={triage} />}
      </AgentCard>

      <FlowConnector state={flow(1)} />
      <AgentCard
        emoji="🔎"
        title="Research"
        model={recordedModel(detail.state.usage, 'research')}
        provenance={detail.state.provenance?.research ?? 'unknown'}
        provenanceError={detail.state.provenance_errors?.research}
        role="Checks customer records, payments, history and help docs"
        events={researchEvents}
      >
        {research && <ResearchResults research={research} />}
      </AgentCard>

      <FlowConnector state={flow(2)} />
      <AgentCard
        emoji="⚡"
        title="Action"
        model={recordedModel(detail.state.usage, 'action')}
        provenance={detail.state.provenance?.action ?? 'unknown'}
        provenanceError={detail.state.provenance_errors?.action}
        role="Proposes a resolution; Python guardrails have the final say"
        events={actionEvents}
      >
        {action && <ActionResults action={action} />}
      </AgentCard>

      <FlowConnector state={finished ? 'done' : 'idle'} />
      <Outcome detail={detail} status={currentStatus} events={events} operatorToken={operatorToken} onDetail={replaceDetail} />
    </motion.div>
  )
}

function TicketHeader({
  detail,
  status,
  connected,
  operatorToken,
  onDetail,
}: {
  detail: TicketDetail
  status: TicketDetail['status']
  connected: boolean
  operatorToken: string | null
  onDetail: (detail: TicketDetail) => void
}) {
  const t = detail.state.ticket
  const meta = statusMeta(status, detail.resolution)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const canRun = operatorToken !== null && detail.can_reprocess && !detail.running && status !== 'processing'
  const run = async () => {
    if (operatorToken === null) return
    setBusy(true)
    setError(null)
    try {
      await api.processTicket(detail.id, operatorToken)
      // Processing is accepted asynchronously; fetch its authoritative snapshot immediately.
      onDetail(await api.getTicket(detail.id))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-gradient-to-b from-white/[0.04] to-white/[0.015] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            <span className="font-mono">TICKET #{detail.id}</span>
            <span>·</span>
            <span>{timeAgo(t.created_at)}</span>
            <span>·</span>
            <LiveDot on={connected} label={connected ? 'Streaming' : 'Reconnecting'} />
            {detail.execution_mode && <><span>·</span><span>Execution: {detail.execution_mode}</span></>}
          </div>
          <h1 className="mt-1 text-lg font-semibold tracking-tight text-white">{t.subject}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
            <span className="text-slate-200">{detail.customer_name ?? 'Unknown customer'}</span>
            {detail.customer_email && <span className="text-slate-500">{detail.customer_email}</span>}
            {detail.customer_tier && <Badge tone={tierTone[detail.customer_tier] ?? 'slate'}>{detail.customer_tier.toUpperCase()}</Badge>}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium ring-1 ring-inset ${toneClasses[meta.tone]}`}>
            {status === 'processing' ? <Spinner className="size-3" /> : <span aria-hidden>{meta.icon}</span>}
            {meta.label}
          </span>
          {detail.can_reprocess && !detail.running && status !== 'processing' && (
            <button
              type="button"
              onClick={() => void run()}
              disabled={busy || !canRun}
              className="text-[11px] text-slate-500 transition-colors hover:text-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
              title={operatorToken !== null ? undefined : 'Unlock operator actions first'}
            >
              ↻ {status === 'open' ? 'Run agents' : 'Re-run agents'}
            </button>
          )}
        </div>
      </div>
      <p className="mt-3 max-h-36 overflow-y-auto rounded-xl bg-black/25 px-3 py-2.5 text-[13px] leading-relaxed whitespace-pre-line text-slate-300 ring-1 ring-white/[0.04]">
        {t.body}
      </p>
      {error && <p role="alert" className="mt-2 text-xs text-rose-300">{error}</p>}
    </div>
  )
}

function TriageResults({ triage }: { triage: TriageResult }) {
  const urgency = urgencyMeta[triage.urgency]
  return (
    <>
      <div className="flex flex-wrap gap-1.5">
        <Badge tone="cyan">{triage.category}</Badge>
        <Badge tone={urgency.tone}>
          <span className={`size-1.5 rounded-full ${urgency.dot}`} />
          {urgency.label} urgency
        </Badge>
        <Badge tone={triage.sentiment === 'angry' ? 'rose' : triage.sentiment === 'frustrated' ? 'orange' : 'slate'}>
          {sentimentEmoji[triage.sentiment]} {triage.sentiment}
        </Badge>
        <Badge>🌐 {triage.language.toUpperCase()}</Badge>
      </div>
      {triage.is_suspicious && (
        <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-200 ring-1 ring-rose-400/25 ring-inset">
          ⚠ Flagged as suspicious: possible attempt to manipulate the system
        </div>
      )}
      <p className="text-[13px] text-slate-300">{triage.summary}</p>
      <ConfidenceBar value={triage.confidence} />
    </>
  )
}

function ResearchResults({ research }: { research: ResearchResult }) {
  const c = research.customer
  return (
    <>
      {c && (
        <div className="flex items-center gap-3 rounded-xl bg-white/[0.03] px-3 py-2.5 ring-1 ring-white/[0.05]">
          <div className="grid size-8 place-items-center rounded-full bg-gradient-to-br from-cyan-400/30 to-violet-400/30 text-xs font-semibold text-white">
            {c.name.split(' ').map((p) => p[0]).join('').slice(0, 2)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="text-[13px] font-medium text-slate-100">{c.name}</span>
              <Badge tone={tierTone[c.tier]}>{c.tier === 'vip' ? '★ VIP' : c.tier.toUpperCase()}</Badge>
            </div>
            <p className="truncate text-[11px] text-slate-500">
              {c.plan} · since {new Date(c.signup_date).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })}
            </p>
          </div>
          <div className="text-right">
            <p className="font-mono text-sm text-slate-100 tabular-nums">{money(c.lifetime_value, 0)}</p>
            <p className="text-[10px] tracking-wide text-slate-500 uppercase">LTV</p>
          </div>
        </div>
      )}
      <ul className="space-y-1.5">
        {research.key_findings.map((f, i) => (
          <motion.li
            key={f}
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.06 }}
            className="flex gap-2 text-[13px] text-slate-300"
          >
            <span className="mt-[7px] size-1 shrink-0 rounded-full bg-cyan-300" />
            {f}
          </motion.li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-1.5 text-[11px] text-slate-500">
        <span>💳 {research.payments.length} payments</span>
        <span>·</span>
        <span>📂 {research.past_tickets.length} past tickets</span>
        {research.relevant_docs.map((d) => (
          <span key={d.title} className="rounded bg-white/[0.04] px-1.5 text-slate-400">
            📚 {d.title}
          </span>
        ))}
      </div>
    </>
  )
}

function ActionResults({ action }: { action: ActionResult }) {
  const meta = decisionMeta[action.decision]
  const proposed = action.proposal
  const overridden = proposed !== null && proposed.decision !== action.decision
  const reasoning = action.reasoning.split('\n\nPolicy:')[0]
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-sm font-semibold ring-1 ring-inset ${toneClasses[meta.tone]}`}>
          {meta.icon} {meta.label}
          {action.refund_amount ? <span className="font-mono">{money(action.refund_amount)}</span> : null}
        </span>
        {overridden && (
          <span className="text-xs text-slate-500">
            AI proposed{' '}
            <span className="text-slate-400 line-through decoration-amber-400/70">
              {decisionMeta[proposed.decision].label}
              {proposed.refund_amount ? ` ${money(proposed.refund_amount)}` : ''}
            </span>{' '}
            → <span className="text-amber-300">policy override</span>
          </span>
        )}
      </div>

      {action.rules_fired.length > 0 && (
        <ul className="space-y-1 rounded-xl bg-amber-400/[0.06] px-3 py-2.5 ring-1 ring-amber-400/20 ring-inset">
          {action.rules_fired.map((rule) => (
            <li key={rule} className="flex items-center gap-2 text-xs text-amber-100">
              <span aria-hidden>🛡️</span>
              <code className="rounded bg-black/30 px-1 font-mono text-[10px] text-amber-300">{rule}</code>
              <span>{ruleLabels[rule] ?? rule}</span>
            </li>
          ))}
        </ul>
      )}

      <ConfidenceBar value={action.confidence} />
      <p className="text-xs leading-relaxed text-slate-400">{reasoning}</p>

      <figure className="rounded-xl bg-black/25 px-3.5 py-3 ring-1 ring-white/[0.05]">
        <figcaption className="mb-1.5 text-[10px] font-semibold tracking-[0.12em] text-slate-500 uppercase">
          {action.decision === 'escalate' ? 'Draft reply for review' : 'Reply to customer'}
        </figcaption>
        <p className="text-[13px] leading-relaxed whitespace-pre-line text-slate-200">{action.reply_draft}</p>
      </figure>
    </>
  )
}

function HumanReviewCard({ detail, token, onDetail }: { detail: TicketDetail; token: string | null; onDetail: (detail: TicketDetail) => void }) {
  const draft = detail.state.action?.reply_draft ?? ''
  const [editing, setEditing] = useState(false)
  const [customReply, setCustomReply] = useState(draft)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleAction = async (action: 'approved' | 'edited' | 'rejected') => {
    if (token === null) {
      setError('Unlock operator actions before reviewing a ticket.')
      return
    }
    if (action === 'edited' && !customReply.trim()) {
      setError('An edited reply cannot be blank.')
      return
    }
    if (action === 'approved' && !draft.trim()) {
      setError('There is no reply draft to approve.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      onDetail(await api.reviewTicket(detail.id, {
        action,
        reply: action === 'edited' ? customReply.trim() : undefined,
        run_id: detail.run_id,
        expected_version: detail.version,
      }, token))
    } catch (e) {
      setError((e as Error).message || 'Review submission failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-500/[0.05] p-3.5 text-slate-200">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-400/10 pb-2.5">
        <div className="flex items-center gap-2">
          <span className="flex size-2 rounded-full bg-amber-400 animate-pulse" />
          <h4 className="text-xs font-semibold uppercase tracking-wider text-amber-300">
            Mission Control Human Review
          </h4>
        </div>
        <span className="text-[11px] text-slate-500">Server records the configured operator.</span>
      </div>

      {editing ? (
        <div className="mt-3">
          <label className="block text-[11px] font-medium text-slate-400 mb-1">
            Customize Draft Reply:
          </label>
          <textarea
            value={customReply}
            onChange={(e) => setCustomReply(e.target.value)}
            rows={5}
            className="w-full rounded-lg bg-black/60 p-2.5 font-sans text-xs text-white ring-1 ring-white/15 focus:ring-amber-400 focus:outline-none"
          />
          <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400">
            <span>{customReply.length} characters</span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setEditing(false)}
                disabled={submitting}
                className="rounded-lg px-2.5 py-1 text-xs text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => handleAction('edited')}
                disabled={submitting || token === null || !customReply.trim()}
                className="flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-1 text-xs font-semibold text-black hover:bg-amber-400 disabled:opacity-50"
              >
                {submitting ? <Spinner className="size-3" /> : '✓'} Save & Approve
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-3">
          <p className="text-[11px] text-slate-400 mb-1 font-medium">Proposed Response Draft:</p>
          <div className="rounded-lg bg-black/40 p-2.5 text-xs leading-relaxed text-slate-300 whitespace-pre-line ring-1 ring-white/10">
            {draft || '(No reply draft available)'}
          </div>

          <div className="mt-3.5 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => handleAction('approved')}
              disabled={submitting || token === null || !draft.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-emerald-500/20 px-3.5 py-1.5 text-xs font-medium text-emerald-300 ring-1 ring-emerald-500/30 hover:bg-emerald-500/30 disabled:opacity-50 transition"
            >
              {submitting ? <Spinner className="size-3" /> : '✓'} Approve & Send
            </button>
            <button
              type="button"
              onClick={() => {
                setCustomReply(draft)
                setEditing(true)
              }}
              disabled={submitting || token === null}
              className="flex items-center gap-1.5 rounded-lg bg-white/5 px-3 py-1.5 text-xs font-medium text-slate-300 ring-1 ring-white/10 hover:bg-white/10 disabled:opacity-50 transition"
            >
              ✏️ Edit Draft
            </button>
            <button
              type="button"
              onClick={() => handleAction('rejected')}
              disabled={submitting || token === null}
              className="flex items-center gap-1.5 rounded-lg bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-300 ring-1 ring-rose-500/20 hover:bg-rose-500/20 disabled:opacity-50 transition ml-auto"
            >
              ✕ Reject Draft
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-2 text-xs text-rose-400 font-mono">{error}</p>
      )}
    </div>
  )
}

function Outcome({
  detail,
  status,
  events,
  operatorToken,
  onDetail,
}: {
  detail: TicketDetail
  status: TicketDetail['status']
  events: StreamedEvent[]
  operatorToken: string | null
  onDetail: (detail: TicketDetail) => void
}) {
  const notes = events.filter((e) => e.agent === 'human' || (e.agent === 'orchestrator' && e.status !== 'running' && !e.message.startsWith('All agents')))
  const tokens = detail.state.usage.reduce((n, u) => n + u.input_tokens + u.output_tokens, 0)
  const cost = detail.state.usage.reduce((n, u) => n + (u.cost_usd ?? 0), 0)
  const edited = detail.resolution === 'edited'
  const banner = outcomeBanner(detail, status)

  return (
    <motion.div layout="position" className={`rounded-2xl p-4 ring-1 ring-inset ${toneClasses[banner.tone]}`}>
      <div className="flex items-start gap-3">
        <motion.div
          className="grid size-9 shrink-0 place-items-center rounded-xl bg-black/20 text-base"
          animate={status === 'escalated' ? { scale: [1, 1.08, 1] } : { scale: 1 }}
          transition={{ duration: 1.8, repeat: status === 'escalated' ? Infinity : 0 }}
        >
          {banner.icon === 'loading' ? <Spinner className="size-4" /> : banner.icon}
        </motion.div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{banner.title}</p>
          <p className="text-xs opacity-75">{banner.body}</p>
        </div>
        {tokens > 0 && (
          <div className="text-right font-mono text-[11px] text-slate-400 tabular-nums">
            <p>{(tokens / 1000).toFixed(1)}k tokens</p>
            <p>{money(cost, 4)}</p>
          </div>
        )}
      </div>

      {status === 'escalated' && detail.can_review && <HumanReviewCard detail={detail} token={operatorToken} onDetail={onDetail} />}
      <OperationOutcome detail={detail} token={operatorToken} onDetail={onDetail} />

      <AnimatePresence initial={false}>
        {notes.map((e) => (
          <motion.p
            key={e.id}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            className="mt-2 text-xs text-slate-300"
          >
            {e.message}
          </motion.p>
        ))}
      </AnimatePresence>
      {edited && detail.final_reply && (
        <p className="mt-3 rounded-xl bg-black/25 px-3 py-2.5 text-[13px] whitespace-pre-line text-slate-200">{detail.final_reply}</p>
      )}
    </motion.div>
  )
}
