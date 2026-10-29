import { AnimatePresence, motion } from 'framer-motion'
import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { TicketSummary } from './api'
import { Inbox } from './components/Inbox'
import { NewTicketForm } from './components/NewTicketForm'
import { OperatorAccess } from './components/OperatorAccess'
import { TicketView } from './components/TicketView'
import { LiveDot, Spinner } from './components/ui'
import { useLiveUpdates } from './hooks/useLiveUpdates'

// Recharts is heavy; load the Impact tab only when it's opened.
const MetricsDashboard = lazy(() => import('./components/MetricsDashboard').then((m) => ({ default: m.MetricsDashboard })))

type Tab = 'control' | 'metrics'

const tabFromHash = (): Tab => (window.location.hash === '#metrics' ? 'metrics' : 'control')

const TABS: { key: Tab; label: string }[] = [
  { key: 'control', label: 'Mission control' },
  { key: 'metrics', label: 'Impact' },
]

function App() {
  const [tab, setTabState] = useState<Tab>(tabFromHash)
  const setTab = (next: Tab) => {
    setTabState(next)
    window.history.replaceState(null, '', next === 'metrics' ? '#metrics' : window.location.pathname)
  }
  const [tickets, setTickets] = useState<TicketSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  // Deliberately React state only: never persist the operator token in browser storage.
  const [operatorToken, setOperatorToken] = useState<string | null>(null)

  const loadTickets = useCallback(() => {
    api
      .listTickets()
      .then((list) => {
        setTickets(list)
        setError(null)
        setSelectedId((current) => current ?? list[0]?.id ?? null)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(loadTickets, [loadTickets])

  const live = useLiveUpdates(() => {
    loadTickets()
    setRefreshKey((k) => k + 1)
  })

  const onCreated = (id: number) => {
    setSelectedId(id)
    setTab('control')
    loadTickets()
  }

  const processing = tickets.filter((t) => t.status === 'processing').length

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="relative z-10 flex shrink-0 items-center gap-6 border-b border-white/[0.06] bg-[#0b1018]/80 px-5 py-3 backdrop-blur-xl">
        <div className="flex items-center gap-2.5">
          <div className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-cyan-400 to-violet-500 text-sm font-bold text-slate-950 shadow-[0_0_20px_-4px_rgba(34,211,238,0.6)]">
            R
          </div>
          <div className="leading-tight">
            <p className="text-sm font-semibold tracking-tight text-white">Relayy</p>
            <p className="text-[10px] tracking-[0.18em] text-slate-500 uppercase">Support agent swarm</p>
          </div>
        </div>

        <nav className="flex rounded-xl bg-white/[0.03] p-1 ring-1 ring-white/[0.06]">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={`relative rounded-lg px-3.5 py-1.5 text-xs font-medium transition-colors ${
                tab === t.key ? 'text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {tab === t.key && (
                <motion.span
                  layoutId="tab-pill"
                  className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/[0.08]"
                  transition={{ type: 'spring', stiffness: 500, damping: 38 }}
                />
              )}
              <span className="relative">{t.label}</span>
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-4">
          {processing > 0 && (
            <span className="hidden text-[11px] text-cyan-300 sm:inline">
              {processing} ticket{processing === 1 ? '' : 's'} in flight
            </span>
          )}
          <LiveDot on={live} />
        </div>
      </header>

      {error && (
        <div className="shrink-0 border-b border-rose-400/20 bg-rose-500/10 px-5 py-2 text-xs text-rose-200">
          Can't reach the backend ({error}). Is <code className="font-mono">uvicorn main:app</code> running on port 8000?
        </div>
      )}

      <OperatorAccess token={operatorToken} onUnlock={setOperatorToken} onLock={() => setOperatorToken(null)} />

      <main className="min-h-0 flex-1">
        <AnimatePresence mode="wait" initial={false}>
          {tab === 'control' ? (
            <motion.div
              key="control"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="grid h-full grid-cols-1 gap-3 overflow-y-auto p-3 lg:grid-cols-[300px_minmax(0,1fr)] lg:overflow-hidden xl:grid-cols-[320px_minmax(0,1fr)_340px]"
            >
              <div className="h-[420px] lg:h-full lg:min-h-0">
                <Inbox tickets={tickets} selectedId={selectedId} onSelect={setSelectedId} loading={loading} />
              </div>
              <div className="lg:min-h-0 lg:overflow-y-auto lg:pr-1">
                <TicketView ticketId={selectedId} operatorToken={operatorToken} />
              </div>
              <div className="lg:col-span-2 lg:min-h-0 xl:col-span-1">
                <NewTicketForm onCreated={onCreated} operatorToken={operatorToken} />
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="metrics"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="h-full overflow-y-auto p-4"
            >
              <Suspense fallback={<div className="grid h-64 place-items-center text-slate-500"><Spinner className="size-5" /></div>}>
                <MetricsDashboard refreshKey={refreshKey} />
              </Suspense>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  )
}

export default App
