import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import type { ReactNode } from 'react'
import type { StreamedEvent } from '../hooks/useTicketStream'
import { clock } from '../lib/format'
import { phaseOf } from '../lib/phase'
import type { FlowState, Phase } from '../lib/phase'
import { Spinner } from './ui'

const phaseStyles: Record<Phase, string> = {
  idle: 'border-white/[0.05] bg-white/[0.015] opacity-55',
  active: 'border-cyan-400/40 bg-cyan-400/[0.04]',
  done: 'border-white/[0.08] bg-white/[0.025]',
  error: 'border-rose-400/40 bg-rose-400/[0.04]',
}

interface Props {
  emoji: string
  title: string
  model: string
  role: string
  events: StreamedEvent[]
  children?: ReactNode // results, shown once the agent is done
}

export function AgentCard({ emoji, title, model, role, events, children }: Props) {
  const phase = phaseOf(events)
  const [showLog, setShowLog] = useState(false)
  const log = events.filter((e) => e.status !== 'done')
  const seconds =
    events.length > 1 ? (new Date(events.at(-1)!.timestamp).getTime() - new Date(events[0].timestamp).getTime()) / 1000 : null

  return (
    <motion.article
      layout="position"
      className={`relative overflow-hidden rounded-2xl border transition-colors duration-500 ${phaseStyles[phase]}`}
      animate={
        phase === 'active'
          ? { boxShadow: ['0 0 0 0 rgba(34,211,238,0)', '0 0 32px 2px rgba(34,211,238,0.22)', '0 0 0 0 rgba(34,211,238,0)'] }
          : { boxShadow: '0 0 0 0 rgba(34,211,238,0)' }
      }
      transition={phase === 'active' ? { duration: 2.2, repeat: Infinity, ease: 'easeInOut' } : { duration: 0.4 }}
    >
      {phase === 'active' && (
        <motion.div
          className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-300 to-transparent"
          animate={{ x: ['-100%', '100%'] }}
          transition={{ duration: 1.6, repeat: Infinity, ease: 'linear' }}
        />
      )}

      <header className="flex items-center gap-3 px-4 pt-3.5 pb-3">
        <div
          className={`grid size-9 shrink-0 place-items-center rounded-xl text-lg ring-1 ring-inset transition-colors ${
            phase === 'active' ? 'bg-cyan-400/15 ring-cyan-300/40' : 'bg-white/[0.04] ring-white/[0.08]'
          }`}
        >
          {emoji}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
            <span className="font-mono text-[10px] text-slate-500">{model}</span>
          </div>
          <p className="truncate text-xs text-slate-500">{role}</p>
        </div>
        <PhaseLabel phase={phase} seconds={seconds} />
      </header>

      <AnimatePresence initial={false} mode="wait">
        {phase === 'active' && (
          <motion.div
            key="live"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="border-t border-cyan-400/10 px-4 py-3"
          >
            <EventLog events={log} live />
          </motion.div>
        )}
        {(phase === 'done' || phase === 'error') && (
          <motion.div
            key="result"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
            className="border-t border-white/[0.06]"
          >
            <div className="space-y-3 px-4 py-3.5">
              {phase === 'error' && <EventLog events={log.filter((e) => e.status === 'error')} />}
              {children}
            </div>
            {log.length > 0 && (
              <div className="border-t border-white/[0.04] px-4 py-2">
                <button
                  type="button"
                  onClick={() => setShowLog((v) => !v)}
                  className="text-[11px] text-slate-500 transition-colors hover:text-slate-300"
                >
                  {showLog ? '▾' : '▸'} {log.length} step{log.length === 1 ? '' : 's'}
                </button>
                <AnimatePresence>
                  {showLog && (
                    <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}>
                      <div className="pt-2">
                        <EventLog events={log} />
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  )
}

function PhaseLabel({ phase, seconds }: { phase: Phase; seconds: number | null }) {
  switch (phase) {
    case 'idle':
      return <span className="text-[11px] text-slate-600">Waiting</span>
    case 'active':
      return (
        <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-cyan-300">
          <Spinner className="size-3" /> Working
        </span>
      )
    case 'done':
      return (
        <span className="text-[11px] text-emerald-300">
          ✓ Done{seconds !== null && <span className="ml-1 font-mono text-slate-500">{seconds.toFixed(1)}s</span>}
        </span>
      )
    case 'error':
      return <span className="text-[11px] font-medium text-rose-300">✕ Failed</span>
  }
}

function EventLog({ events, live = false }: { events: StreamedEvent[]; live?: boolean }) {
  return (
    <ol className="space-y-1.5">
      <AnimatePresence initial={false}>
        {events.map((e, i) => {
          const latest = live && i === events.length - 1
          return (
            <motion.li
              key={e.id}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex items-start gap-2.5 text-xs"
            >
              <span className="mt-px shrink-0 font-mono text-[10px] text-slate-600 tabular-nums">{clock(e.timestamp)}</span>
              <span
                className={
                  e.status === 'error'
                    ? 'text-rose-300'
                    : e.status === 'policy'
                      ? 'text-amber-300'
                      : latest
                        ? 'text-cyan-100'
                        : 'text-slate-400'
                }
              >
                {e.message}
                {latest && <span className="ml-0.5 inline-block h-3 w-1.5 translate-y-0.5 animate-pulse bg-cyan-300/80" />}
              </span>
            </motion.li>
          )
        })}
      </AnimatePresence>
    </ol>
  )
}

/** Vertical arrow between stages; animated while data is flowing into the next stage. */
export function FlowConnector({ state }: { state: FlowState }) {
  const color = state === 'idle' ? 'rgba(148,163,184,0.25)' : state === 'flowing' ? '#67e8f9' : 'rgba(103,232,249,0.55)'
  return (
    <div className="relative mx-auto h-9 w-6" aria-hidden>
      <svg viewBox="0 0 24 36" className="h-full w-full overflow-visible">
        <motion.line
          x1="12"
          y1="0"
          x2="12"
          y2="28"
          stroke={color}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeDasharray={state === 'done' ? '0' : '3 4'}
          animate={state === 'flowing' ? { strokeDashoffset: [0, -14] } : { strokeDashoffset: 0 }}
          transition={state === 'flowing' ? { duration: 0.6, repeat: Infinity, ease: 'linear' } : { duration: 0 }}
        />
        <path d="M7 26 L12 32 L17 26" fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {state === 'flowing' && (
        <motion.span
          className="absolute left-1/2 size-1.5 -translate-x-1/2 rounded-full bg-cyan-200 shadow-[0_0_10px_3px_rgba(103,232,249,0.7)]"
          animate={{ top: ['0%', '78%'], opacity: [0, 1, 0] }}
          transition={{ duration: 0.9, repeat: Infinity, ease: 'easeIn' }}
        />
      )}
    </div>
  )
}
