import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { AgentInfo } from '../api'
import { Badge, Panel, Spinner } from './ui'

const pct = (value: number | null) => (value === null ? '—' : `${Math.round(value * 100)}%`)
const ms = (value: number | null) => (value === null ? '—' : value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`)

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-3 py-2 ring-1 ring-white/[0.05]">
      <p className="text-[10px] tracking-wider text-slate-500 uppercase">{label}</p>
      <p className="mt-0.5 font-mono text-sm text-white tabular-nums">{value}</p>
    </div>
  )
}

function AgentPanel({ agent, token, onChange }: { agent: AgentInfo; token: string | null; onChange: (next: AgentInfo) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const s = agent.stats

  const toggle = () => {
    if (!token) return
    setBusy(true)
    setError(null)
    api.updateAgent(agent.id, !agent.model_enabled, token)
      .then(onChange)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false))
  }

  return (
    <Panel
      title={<span className="flex items-center gap-2">{agent.name} <span className="normal-case tracking-normal text-slate-500">· {agent.role}</span></span>}
      action={<Badge tone={agent.mode === 'live' ? 'emerald' : 'amber'}>{agent.mode === 'live' ? 'Live model' : 'Simulated'}</Badge>}
    >
      <div className="space-y-4 p-4">
        <p className="text-sm leading-relaxed text-slate-300">{agent.description}</p>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge tone="slate">{agent.tier} tier</Badge>
          {agent.route ? (
            <Badge tone="cyan">{agent.route.provider} · {agent.route.model}</Badge>
          ) : (
            <span className="text-amber-300">{agent.unavailable_reason}</span>
          )}
          {agent.tools.map((tool) => <Badge key={tool} tone="violet">{tool}</Badge>)}
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="Runs" value={s.runs} />
          <Stat label="Real-model rate" value={pct(s.success_rate)} />
          <Stat label="Avg latency" value={ms(s.avg_latency_ms)} />
          <Stat label="Tokens in/out" value={`${s.input_tokens}/${s.output_tokens}`} />
          <Stat label="Real model" value={s.real_model} />
          <Stat label="Simulated" value={s.simulated} />
          <Stat label="Failed" value={s.failed} />
          <Stat label="Cost" value={s.cost_usd === null ? 'n/a' : `$${s.cost_usd.toFixed(4)}`} />
        </div>

        {Object.keys(s.models).length > 0 && (
          <p className="text-xs text-slate-500">
            Models used: {Object.entries(s.models).map(([model, n]) => `${model} ×${n}`).join(', ')}
          </p>
        )}

        <div className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.02] px-3 py-2 ring-1 ring-white/[0.05]">
          <div className="text-xs">
            <p className="text-slate-200">Real model calls</p>
            <p className="text-slate-500">
              {agent.updated_by ? `Changed by ${agent.updated_by}` : 'Off runs labeled simulated analysis instead.'}
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={agent.model_enabled}
            aria-label={`Real model calls for ${agent.name}`}
            disabled={!token || busy}
            onClick={toggle}
            title={token ? undefined : 'Unlock operator access to change this'}
            className={`relative h-6 w-11 rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${agent.model_enabled ? 'bg-accent' : 'bg-white/[0.12]'}`}
          >
            <span className={`absolute top-0.5 size-5 rounded-full bg-page shadow transition-transform ${agent.model_enabled ? 'translate-x-5' : 'translate-x-0.5'}`} />
          </button>
        </div>
        {error && <p className="text-xs text-rose-300">{error}</p>}

        <div>
          <p className="mb-2 text-[10px] tracking-wider text-slate-500 uppercase">Recent executions</p>
          {s.recent.length === 0 ? (
            <p className="text-xs text-slate-500">No runs yet. Process a ticket in Mission control.</p>
          ) : (
            <ul className="divide-y divide-white/[0.05] text-xs">
              {s.recent.map((run) => (
                <li key={run.run_id} className="flex items-center justify-between gap-2 py-1.5">
                  <span className="text-slate-300">Ticket #{run.ticket_id} · run {run.run_number}</span>
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-slate-500">{ms(run.latency_ms)}</span>
                    <Badge tone={run.provenance === 'real_model' ? 'emerald' : run.provenance === 'failed' ? 'rose' : 'amber'}>
                      {run.provenance.replace('_', ' ')}
                    </Badge>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  )
}

export function AgentsPage({ operatorToken, refreshKey }: { operatorToken: string | null; refreshKey: number }) {
  const [agents, setAgents] = useState<AgentInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.agents().then((next) => { setAgents(next); setError(null) }).catch((e: Error) => setError(e.message))
  }, [])
  useEffect(load, [load, refreshKey])

  if (error) return <p className="p-6 text-sm text-rose-300">Could not load agents: {error}</p>
  if (!agents) return <div className="grid h-64 place-items-center text-slate-500"><Spinner className="size-5" /></div>

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-white">Agents</h1>
        <p className="text-sm text-slate-400">
          Each ticket flows triage → research → action. Statistics come from stored runs; policy always has the final word.
        </p>
      </header>
      <div className="grid gap-4 xl:grid-cols-3">
        {agents.map((agent) => (
          <AgentPanel key={agent.id} agent={agent} token={operatorToken}
            onChange={(next) => setAgents((list) => list?.map((a) => (a.id === next.id ? next : a)) ?? null)} />
        ))}
      </div>
    </div>
  )
}
