import { AnimatePresence, motion } from 'framer-motion'
import type { TicketSummary } from '../api'
import { statusMeta, timeAgo, toneClasses, urgencyMeta } from '../lib/format'
import { Badge, Panel } from './ui'

interface Props {
  tickets: TicketSummary[]
  selectedId: number | null
  onSelect: (id: number) => void
  loading: boolean
}

export function Inbox({ tickets, selectedId, onSelect, loading }: Props) {
  const waiting = tickets.filter((t) => t.status === 'escalated').length
  return (
    <Panel
      title="Inbox"
      className="h-full"
      action={
        <span className="text-[11px] text-slate-500">
          {tickets.length} tickets{waiting > 0 && <span className="text-amber-300"> · {waiting} awaiting review</span>}
        </span>
      }
    >
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading && tickets.length === 0 && (
          <div className="space-y-2 p-1">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[76px] animate-pulse rounded-xl bg-white/[0.03]" />
            ))}
          </div>
        )}
        {!loading && tickets.length === 0 && (
          <p className="px-3 py-10 text-center text-sm text-slate-500">No tickets yet. Submit one from the right panel.</p>
        )}
        <ul className="space-y-1.5">
          <AnimatePresence initial={false}>
            {tickets.map((t) => (
              <motion.li
                key={t.id}
                layout
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ type: 'spring', stiffness: 400, damping: 34 }}
              >
                <InboxRow ticket={t} selected={t.id === selectedId} onSelect={() => onSelect(t.id)} />
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      </div>
    </Panel>
  )
}

function InboxRow({ ticket: t, selected, onSelect }: { ticket: TicketSummary; selected: boolean; onSelect: () => void }) {
  const status = statusMeta(t.status, t.resolution)
  const urgency = t.urgency ? urgencyMeta[t.urgency] : null
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`group relative w-full overflow-hidden rounded-xl border px-3 py-2.5 text-left transition-colors ${
        selected
          ? 'border-cyan-400/30 bg-cyan-400/[0.06]'
          : 'border-transparent hover:border-white/[0.06] hover:bg-white/[0.03]'
      }`}
    >
      {selected && (
        <motion.span layoutId="inbox-selected" className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-cyan-400" />
      )}
      <div className="flex items-start justify-between gap-2">
        <p className="line-clamp-1 text-[13px] font-medium text-slate-100">{t.subject}</p>
        <span className="shrink-0 font-mono text-[10px] text-slate-500">#{t.id}</span>
      </div>
      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-400">
        <span className="truncate">{t.customer_name ?? 'Unknown customer'}</span>
        {t.customer_tier === 'vip' && <span className="text-violet-300">★ VIP</span>}
        <span className="text-slate-600">·</span>
        <span className="shrink-0">{timeAgo(t.created_at)}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${toneClasses[status.tone]}`}>
          {t.status === 'processing' ? (
            <span className="relative flex size-1.5">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-cyan-300 opacity-75" />
              <span className="relative inline-flex size-1.5 rounded-full bg-cyan-300" />
            </span>
          ) : (
            <span aria-hidden>{status.icon}</span>
          )}
          {status.label}
        </span>
        {urgency && (
          <Badge tone={urgency.tone}>
            <span className={`size-1.5 rounded-full ${urgency.dot}`} />
            {urgency.label}
          </Badge>
        )}
        {t.is_suspicious && <Badge tone="rose">⚠ Suspicious</Badge>}
      </div>
    </button>
  )
}
