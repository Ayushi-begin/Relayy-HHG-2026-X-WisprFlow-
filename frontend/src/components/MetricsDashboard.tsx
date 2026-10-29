import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../api'
import type { Decision, Metrics } from '../api'
import { money, pct, ruleLabels } from '../lib/format'
import { Panel, Spinner } from './ui'

// Validated categorical slots (dark mode) — see the dataviz palette; order matters for CVD separation.
const SERIES: { key: Decision; label: string; color: string }[] = [
  { key: 'auto_resolve', label: 'Auto-resolved', color: '#3987e5' },
  { key: 'escalate', label: 'Escalated', color: '#d95926' },
  { key: 'auto_refund', label: 'Auto-refunded', color: '#199e70' },
]
const BAR_COLOR = '#3987e5'
const AXIS = '#898781'
const GRID = 'rgba(255,255,255,0.06)'

export function MetricsDashboard({ refreshKey }: { refreshKey: number }) {
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .metrics()
      .then((m) => {
        if (!cancelled) {
          setMetrics(m)
          setError(null)
        }
      })
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  if (!metrics) {
    return (
      <div className="grid h-64 place-items-center text-slate-500">
        {error ? <p className="text-sm text-rose-300">Couldn't load metrics: {error}</p> : <Spinner className="size-5" />}
      </div>
    )
  }

  const m = metrics
  return (
    <div className="mx-auto max-w-7xl space-y-4 pb-10">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Total tickets" value={String(m.tickets_total)} sub={`${m.processed} processed by agents`} />
        <StatTile label="Auto-resolved" value={pct(m.auto_resolved_rate)} sub="no human needed (incl. refunds)" accent="text-emerald-300" />
        <StatTile label="Escalated" value={pct(m.escalated_rate)} sub="sent to human review" accent="text-amber-300" />
        <StatTile
          label="Time to decision"
          value={m.avg_time_to_decision_seconds === null ? '—' : `${m.avg_time_to_decision_seconds.toFixed(1)}s`}
          sub="average, ticket in → decision"
        />
        <StatTile
          label="Agent hours saved"
          value={m.agent_hours_saved.toFixed(1)}
          sub={`at ${m.minutes_per_manual_ticket} min per manual ticket`}
          accent="text-cyan-300"
        />
        <StatTile
          label="Cost per ticket"
          value={m.cost_per_ticket_usd === null ? '—' : money(m.cost_per_ticket_usd, 4)}
          sub={`${money(m.total_cost_usd, 2)} total model spend`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-5">
        <Panel title="Decisions over time" className="lg:col-span-3" action={<Legend metrics={m} />}>
          <div className="h-72 p-3 pr-5">
            <DecisionsChart metrics={m} />
          </div>
        </Panel>
        <Panel title="Tickets by category" className="lg:col-span-2">
          <div className="h-72 p-3 pr-5">
            <CategoryChart metrics={m} />
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Panel title="Why tickets were escalated">
          <BarList
            rows={Object.entries(m.escalations_by_rule).map(([rule, n]) => ({ label: ruleLabels[rule] ?? rule, code: rule, value: n }))}
            empty="No policy overrides yet"
          />
        </Panel>
        <Panel title="Model spend by agent">
          <BarList
            rows={(['triage', 'research', 'action'] as const).map((a) => ({ label: a[0].toUpperCase() + a.slice(1), value: m.cost_by_agent[a] ?? 0 }))}
            format={(v) => money(v, 4)}
            empty="No model calls yet"
          />
        </Panel>
        <Panel title="Human review in Slack">
          <div className="grid grid-cols-3 gap-2 p-4">
            <MiniStat label="Approved" value={m.human_reviews.approved} />
            <MiniStat label="Edited" value={m.human_reviews.edited} />
            <MiniStat label="Rejected" value={m.human_reviews.rejected} />
          </div>
          <p className="px-4 pb-4 text-xs text-slate-500">
            AI draft accepted as-is: <span className="text-slate-300">{pct(m.ai_reply_acceptance_rate)}</span> ·{' '}
            {m.suspicious_flagged} suspicious flagged · {money(m.auto_refunded_total)} auto-refunded
          </p>
        </Panel>
      </div>
    </div>
  )
}

function StatTile({ label, value, sub, accent = 'text-white' }: { label: string; value: string; sub: string; accent?: string }) {
  return (
    <motion.div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4">
      <p className="text-[11px] font-medium tracking-wide text-slate-400 uppercase">{label}</p>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.p
          key={value}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          className={`mt-1.5 text-3xl font-semibold tracking-tight ${accent}`}
        >
          {value}
        </motion.p>
      </AnimatePresence>
      <p className="mt-1 text-[11px] text-slate-500">{sub}</p>
    </motion.div>
  )
}

function MiniStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-white/[0.03] p-3 text-center ring-1 ring-white/[0.05]">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-[11px] text-slate-500">{label}</p>
    </div>
  )
}

function Legend({ metrics }: { metrics: Metrics }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      {SERIES.map((s) => (
        <span key={s.key} className="inline-flex items-center gap-1.5 text-[11px] text-slate-300">
          <span className="size-2.5 rounded-sm" style={{ background: s.color }} />
          {s.label}
          <span className="font-mono text-slate-500">{metrics.by_decision[s.key] ?? 0}</span>
        </span>
      ))}
    </div>
  )
}

function ChartTooltip({ title, rows }: { title: string; rows: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="rounded-xl border border-white/10 bg-slate-950/95 px-3 py-2 text-xs shadow-xl backdrop-blur">
      <p className="mb-1 text-slate-400">{title}</p>
      {rows.map((r) => (
        <p key={r.label} className="flex items-center gap-2 text-slate-200">
          {r.color && <span className="size-2 rounded-sm" style={{ background: r.color }} />}
          <span className="flex-1">{r.label}</span>
          <span className="font-mono tabular-nums">{r.value}</span>
        </p>
      ))}
    </div>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="grid h-full place-items-center text-sm text-slate-500">{children}</div>
}

type Point = { t: number } & Record<Decision, number>

function DecisionsChart({ metrics }: { metrics: Metrics }) {
  // Cumulative decisions, one step per decided ticket.
  const data = useMemo(() => {
    const points = metrics.timeline.filter((p) => p.decided_at)
    if (points.length === 0) return []
    const acc: Record<Decision, number> = { auto_resolve: 0, escalate: 0, auto_refund: 0 }
    const first = new Date(points[0].decided_at!).getTime()
    const out: Point[] = [{ t: first - 1000, ...acc }]
    for (const p of points) {
      acc[p.decision] += 1
      out.push({ t: new Date(p.decided_at!).getTime(), ...acc })
    }
    return out
  }, [metrics.timeline])

  if (data.length === 0) return <Empty>Decisions will appear here as tickets finish</Empty>

  const span = data.at(-1)!.t - data[0].t
  const tick = (t: number) =>
    new Date(t).toLocaleTimeString([], span > 86_400_000 ? { month: 'short', day: 'numeric' } : { hour: '2-digit', minute: '2-digit' })

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -18 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={['dataMin', 'dataMax']}
          tickFormatter={tick}
          tick={{ fill: AXIS, fontSize: 11 }}
          axisLine={{ stroke: GRID }}
          tickLine={false}
          minTickGap={40}
        />
        <YAxis allowDecimals={false} tick={{ fill: AXIS, fontSize: 11 }} axisLine={false} tickLine={false} />
        <Tooltip
          cursor={{ stroke: 'rgba(255,255,255,0.25)', strokeWidth: 1 }}
          content={({ active, payload, label }) =>
            active && payload?.length ? (
              <ChartTooltip
                title={new Date(Number(label)).toLocaleTimeString()}
                rows={SERIES.map((s) => ({ label: s.label, value: String(payload[0].payload[s.key]), color: s.color }))}
              />
            ) : null
          }
        />
        {SERIES.map((s) => (
          <Area
            key={s.key}
            type="stepAfter"
            dataKey={s.key}
            stackId="decisions"
            stroke={s.color}
            strokeWidth={2}
            fill={s.color}
            fillOpacity={0.28}
            isAnimationActive
            animationDuration={500}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}

function CategoryChart({ metrics }: { metrics: Metrics }) {
  const data = Object.entries(metrics.by_category).map(([category, count]) => ({ category, count }))
  if (data.length === 0) return <Empty>No categorized tickets yet</Empty>
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 20, right: 4, bottom: 0, left: -18 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="category" tick={{ fill: AXIS, fontSize: 11 }} axisLine={{ stroke: GRID }} tickLine={false} />
        <YAxis allowDecimals={false} tick={{ fill: AXIS, fontSize: 11 }} axisLine={false} tickLine={false} />
        <Tooltip
          cursor={{ fill: 'rgba(255,255,255,0.04)' }}
          content={({ active, payload }) =>
            active && payload?.length ? (
              <ChartTooltip
                title={String(payload[0].payload.category)}
                rows={[{ label: 'Tickets', value: String(payload[0].value), color: BAR_COLOR }]}
              />
            ) : null
          }
        />
        <Bar dataKey="count" fill={BAR_COLOR} radius={[4, 4, 0, 0]} maxBarSize={44} animationDuration={500}>
          <LabelList dataKey="count" position="top" fill="#cbd5e1" fontSize={11} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function BarList({
  rows,
  format = (v) => String(v),
  empty,
}: {
  rows: { label: string; value: number; code?: string }[]
  format?: (v: number) => string
  empty: string
}) {
  const max = Math.max(0, ...rows.map((r) => r.value))
  if (max === 0) return <p className="p-4 text-sm text-slate-500">{empty}</p>
  return (
    <ul className="space-y-3 p-4">
      {rows.map((r) => (
        <li key={r.code ?? r.label}>
          <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate text-slate-300" title={r.code}>
              {r.label}
            </span>
            <span className="font-mono text-slate-400 tabular-nums">{format(r.value)}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
            <motion.div
              className="h-full rounded-full"
              style={{ background: BAR_COLOR }}
              initial={{ width: 0 }}
              animate={{ width: `${(r.value / max) * 100}%` }}
              transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}
