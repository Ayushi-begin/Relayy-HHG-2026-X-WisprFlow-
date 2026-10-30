import { AnimatePresence, motion } from 'framer-motion'
import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { TicketSummary } from './api'
import { ActivityPage } from './components/ActivityPage'
import { AgentsPage } from './components/AgentsPage'
import { Inbox } from './components/Inbox'
import { NewTicketForm } from './components/NewTicketForm'
import { OperatorAccess } from './components/OperatorAccess'
import { ProvidersPage } from './components/ProvidersPage'
import { SettingsPage } from './components/SettingsPage'
import { TicketView } from './components/TicketView'
import { LiveDot, Spinner } from './components/ui'
import { useLiveUpdates } from './hooks/useLiveUpdates'
import { usePreferences } from './theme/preferences'

// Recharts is heavy; load the Impact tab only when it's opened.
const MetricsDashboard = lazy(() => import('./components/MetricsDashboard').then((m) => ({ default: m.MetricsDashboard })))

type Tab = 'control' | 'metrics' | 'activity' | 'agents' | 'providers' | 'settings'

// Icons are inline SVG paths (24px grid) so the sidebar needs no icon dependency.
const TABS: { key: Tab; label: string; icon: string; shortcut: string }[] = [
  { key: 'control', label: 'Mission control', shortcut: '1', icon: 'M4 5h16v10H4zM8 19h8M12 15v4' },
  { key: 'metrics', label: 'Impact', shortcut: '2', icon: 'M4 19V9m6 10V5m6 14v-7m4 7H2' },
  { key: 'activity', label: 'Live activity', shortcut: '3', icon: 'M3 12h4l3-8 4 16 3-8h4' },
  { key: 'agents', label: 'Agents', shortcut: '4', icon: 'M12 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM4 21a8 8 0 0 1 16 0' },
  { key: 'providers', label: 'Providers', shortcut: '5', icon: 'M5 7h14v4H5zM5 13h14v4H5zM8 9h.01M8 15h.01' },
  { key: 'settings', label: 'Appearance', shortcut: '6', icon: 'M12 3v2m0 14v2M5 12H3m18 0h-2M6.3 6.3 4.9 4.9m14.2 14.2-1.4-1.4M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8z' },
]

const tabFromHash = (): Tab => {
  const key = window.location.hash.slice(1)
  return TABS.some((t) => t.key === key) ? (key as Tab) : 'control'
}

function Icon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" className="size-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={path} />
    </svg>
  )
}

function App() {
  const { prefs, update } = usePreferences()
  const collapsed = prefs.sidebarCollapsed
  const [tab, setTabState] = useState<Tab>(tabFromHash)
  const setTab = useCallback((next: Tab) => {
    setTabState(next)
    window.history.replaceState(null, '', next === 'control' ? window.location.pathname : `#${next}`)
  }, [])
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

  // Alt+1..6 switches pages; ignored while typing in a field.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (!event.altKey || target?.closest('input, textarea, select, [contenteditable]')) return
      const match = TABS.find((t) => t.shortcut === event.key)
      if (match) {
        event.preventDefault()
        setTab(match.key)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setTab])

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
  const fade = { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: { duration: 0.15 } }

  return (
    <div className="flex h-screen overflow-hidden">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded focus:bg-accent focus:px-3 focus:py-1 focus:text-on-accent">
        Skip to content
      </a>
      <aside className={`hidden shrink-0 flex-col border-r border-white/[0.06] bg-header transition-[width] duration-200 md:flex ${collapsed ? 'w-14' : 'w-56'}`}>
        <div className="flex items-center gap-2.5 px-3 py-3">
          <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-accent text-sm font-bold text-on-accent">R</div>
          {!collapsed && (
            <div className="leading-tight">
              <p className="text-sm font-semibold tracking-tight text-white">Relayy</p>
              <p className="text-[10px] tracking-[0.16em] text-slate-500 uppercase">Support agent swarm</p>
            </div>
          )}
        </div>
        <nav aria-label="Main" className="flex flex-1 flex-col gap-0.5 px-2 py-2">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              aria-current={tab === t.key ? 'page' : undefined}
              title={collapsed ? `${t.label} (Alt+${t.shortcut})` : `Alt+${t.shortcut}`}
              className={`relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] transition-colors ${
                tab === t.key ? 'text-white' : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'
              }`}
            >
              {tab === t.key && (
                <motion.span layoutId="nav-pill" className="absolute inset-0 rounded-lg bg-white/[0.07] ring-1 ring-white/[0.06]"
                  transition={{ type: 'spring', stiffness: 500, damping: 40 }} />
              )}
              <span className="relative flex items-center gap-2.5">
                <Icon path={t.icon} />
                {!collapsed && t.label}
              </span>
            </button>
          ))}
        </nav>
        <button
          type="button"
          onClick={() => update({ sidebarCollapsed: !collapsed })}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className="m-2 flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
        >
          <Icon path={collapsed ? 'M9 6l6 6-6 6' : 'M15 6l-6 6 6 6'} />
          {!collapsed && 'Collapse'}
        </button>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="relative z-10 flex shrink-0 items-center gap-4 border-b border-white/[0.06] bg-header/80 px-4 py-2.5 backdrop-blur-xl">
          <h1 className="text-sm font-medium text-slate-200">{TABS.find((t) => t.key === tab)?.label}</h1>
          {/* Compact navigation for small screens, where the sidebar is hidden. */}
          <select
            aria-label="Page"
            value={tab}
            onChange={(e) => setTab(e.target.value as Tab)}
            className="ml-auto rounded-md bg-white/[0.05] px-2 py-1 text-xs text-slate-200 ring-1 ring-white/[0.08] md:hidden"
          >
            {TABS.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
          </select>
          <div className="ml-auto hidden items-center gap-4 md:flex">
            {processing > 0 && (
              <span className="text-[11px] text-cyan-300">
                {processing} ticket{processing === 1 ? '' : 's'} in flight
              </span>
            )}
            <LiveDot on={live} />
          </div>
        </header>

        {error && (
          <div role="alert" className="shrink-0 border-b border-rose-400/20 bg-rose-500/10 px-5 py-2 text-xs text-rose-200">
            Can't reach the backend ({error}). Check that the API is running and <code className="font-mono">VITE_API_URL</code> points to it.
          </div>
        )}

        <OperatorAccess token={operatorToken} onUnlock={setOperatorToken} onLock={() => setOperatorToken(null)} />

        <main id="main" className="min-h-0 flex-1">
          <AnimatePresence mode="wait" initial={false}>
            {tab === 'control' ? (
              <motion.div
                key="control"
                {...fade}
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
              <motion.div key={tab} {...fade} className="h-full overflow-y-auto p-4 sm:p-6">
                {tab === 'metrics' && (
                  <Suspense fallback={<div className="grid h-64 place-items-center text-slate-500"><Spinner className="size-5" /></div>}>
                    <MetricsDashboard refreshKey={refreshKey} />
                  </Suspense>
                )}
                {tab === 'activity' && <ActivityPage />}
                {tab === 'agents' && <AgentsPage operatorToken={operatorToken} refreshKey={refreshKey} />}
                {tab === 'providers' && <ProvidersPage refreshKey={refreshKey} />}
                {tab === 'settings' && <SettingsPage />}
              </motion.div>
            )}
          </AnimatePresence>
        </main>
      </div>
    </div>
  )
}

export default App
