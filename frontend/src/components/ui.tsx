import { motion } from 'framer-motion'
import type { ReactNode } from 'react'
import { toneClasses } from '../lib/format'
import type { Tone } from '../lib/format'

export function Badge({ tone = 'slate', children, className = '' }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium leading-4 ring-1 ring-inset ${toneClasses[tone]} ${className}`}
    >
      {children}
    </span>
  )
}

export function Panel({ title, action, children, className = '' }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`flex min-h-0 flex-col rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-sm ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-3">
          <h2 className="text-[11px] font-semibold tracking-[0.14em] text-slate-400 uppercase">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

/** Horizontal confidence meter; amber below the policy threshold (0.75). */
export function ConfidenceBar({ value, threshold = 0.75 }: { value: number; threshold?: number }) {
  const ok = value >= threshold
  return (
    <div className="flex items-center gap-2.5">
      <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
        <motion.div
          className={`h-full rounded-full ${ok ? 'bg-gradient-to-r from-cyan-400 to-emerald-400' : 'bg-gradient-to-r from-amber-500 to-amber-300'}`}
          initial={{ width: 0 }}
          animate={{ width: `${Math.round(value * 100)}%` }}
          transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
        />
        <div className="absolute inset-y-0 w-px bg-white/30" style={{ left: `${threshold * 100}%` }} title="Policy threshold" />
      </div>
      <span className={`w-10 text-right font-mono text-xs tabular-nums ${ok ? 'text-slate-200' : 'text-amber-300'}`}>
        {Math.round(value * 100)}%
      </span>
    </div>
  )
}

export function LiveDot({ on, label }: { on: boolean; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-400">
      <span className="relative flex size-2">
        {on && <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-60" />}
        <span className={`relative inline-flex size-2 rounded-full ${on ? 'bg-emerald-400' : 'bg-slate-600'}`} />
      </span>
      {label ?? (on ? 'Live' : 'Offline')}
    </span>
  )
}

export function Spinner({ className = 'size-3.5' }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}
