import { useMemo, useState } from 'react'
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

type Filter = 'all' | 'escalated' | 'processing' | 'resolved' | 'suspicious'

export function Inbox({ tickets, selectedId, onSelect, loading }: Props) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')

  const waiting = tickets.filter((t) => t.status === 'escalated').length

  const filteredTickets = useMemo(() => {
    return tickets.filter((t) => {
      // 1. Status Filter
      if (filter === 'escalated' && t.status !== 'escalated') return false
      if (filter === 'processing' && t.status !== 'processing') return false
      if (filter === 'resolved' && t.status !== 'resolved') return false
      if (filter === 'suspicious' && !t.is_suspicious) return false

      // 2. Search query filter
      if (!search.trim()) return true
      const query = search.toLowerCase()
      const matchSubject = t.subject.toLowerCase().includes(query)
      const matchCustomer = t.customer_name?.toLowerCase().includes(query) ?? false
      const matchId = String(t.id).includes(query)
      return matchSubject || matchCustomer || matchId
    })
  }, [tickets, filter, search])

  return (
    <Panel
      title="Inbox"
      className="h-full flex flex-col"
      action={
        <span className="text-[11px] text-slate-500">
          {tickets.length} tickets{waiting > 0 && <span className="text-amber-300"> · {waiting} review</span>}
        </span>
      }
    >
      <div className="shrink-0 border-b border-white/[0.06] p-2 space-y-2">
        {/* Search Input */}
        <div className="relative">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search tickets, customers, #id..."
            className="w-full rounded-lg bg-black/30 py-1.5 pl-7 pr-7 text-xs text-slate-200 placeholder-slate-500 ring-1 ring-white/[0.08] focus:ring-cyan-400/40 focus:outline-none"
          />
          <span className="absolute left-2.5 top-2 text-[11px] text-slate-500">🔍</span>
          {search && (
            <button
              type="button"
              onClick={() => setSearch('')}
              className="absolute right-2.5 top-1.5 text-xs text-slate-500 hover:text-slate-300"
            >
              ✕
            </button>
          )}
        </div>

        {/* Filter Pills */}
        <div className="flex flex-wrap gap-1 text-[11px]">
          {(['all', 'escalated', 'processing', 'resolved', 'suspicious'] as const).map((key) => {
            const active = filter === key
            const count =
              key === 'all'
                ? tickets.length
                : key === 'suspicious'
                ? tickets.filter((t) => t.is_suspicious).length
                : tickets.filter((t) => t.status === key).length

            if (count === 0 && key !== 'all') return null

            return (
              <button
                key={key}
                type="button"
                onClick={() => setFilter(key)}
                className={`rounded-md px-2 py-0.5 font-medium transition-colors ${
                  active
                    ? 'bg-cyan-500/20 text-cyan-300 ring-1 ring-cyan-500/40'
                    : 'bg-white/[0.03] text-slate-400 hover:text-slate-200 ring-1 ring-white/[0.06]'
                }`}
              >
                {key === 'all' ? 'All' : key === 'escalated' ? 'Escalated' : key === 'processing' ? 'Active' : key === 'resolved' ? 'Resolved' : 'Suspicious'}
                <span className="ml-1 opacity-60">({count})</span>
              </button>
            )
          })}
        </div>
      </div>

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
        {!loading && tickets.length > 0 && filteredTickets.length === 0 && (
          <p className="px-3 py-8 text-center text-xs text-slate-500">No tickets match your search or filter.</p>
        )}
        <ul className="space-y-1.5">
          <AnimatePresence initial={false}>
            {filteredTickets.map((t) => (
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
