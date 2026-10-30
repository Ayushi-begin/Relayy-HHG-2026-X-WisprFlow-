import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ProvidersInfo } from '../api'
import { Badge, Panel, Spinner } from './ui'

const LABELS: Record<string, string> = { gemini: 'Google Gemini', anthropic: 'Anthropic Claude', openai: 'OpenAI' }

export function ProvidersPage({ refreshKey }: { refreshKey: number }) {
  const [info, setInfo] = useState<ProvidersInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => api.providers()
      .then((next) => { if (!cancelled) { setInfo(next); setError(null) } })
      .catch((e: Error) => !cancelled && setError(e.message))
    load()
    const timer = window.setInterval(load, 10_000) // health is in-process and changes with traffic
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [refreshKey])

  if (error) return <p className="p-6 text-sm text-rose-300">Could not load providers: {error}</p>
  if (!info) return <div className="grid h-64 place-items-center text-slate-500"><Spinner className="size-5" /></div>

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-white">LLM providers</h1>
        <p className="text-sm text-slate-400">
          Default <span className="text-slate-200">{LABELS[info.default_provider] ?? info.default_provider}</span> ·
          routing <span className="text-slate-200">{info.routing}</span> ·
          fallback <span className="text-slate-200">{info.fallback_provider ?? 'none'}</span>.
          Keys stay on the server; change providers with environment variables.
        </p>
      </header>

      <div className="grid gap-4 md:grid-cols-3">
        {Object.entries(info.providers).map(([name, provider]) => {
          const health = info.health[name]
          const tone = !provider.configured ? 'slate' : health?.status === 'open' ? 'rose' : health?.status === 'degraded' ? 'amber' : 'emerald'
          const label = !provider.configured ? 'Not configured' : health?.status === 'open' ? 'Circuit open' : health?.status === 'degraded' ? 'Degraded' : health ? 'Healthy' : 'No calls yet'
          return (
            <Panel key={name} title={LABELS[name] ?? name} action={<Badge tone={tone}>{label}</Badge>}>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 p-4 text-xs">
                <dt className="text-slate-500">Fast model</dt><dd className="truncate font-mono text-slate-200">{provider.models.fast || '—'}</dd>
                <dt className="text-slate-500">Smart model</dt><dd className="truncate font-mono text-slate-200">{provider.models.smart || '—'}</dd>
                <dt className="text-slate-500">Calls</dt><dd className="font-mono text-slate-200">{health?.calls ?? 0}</dd>
                <dt className="text-slate-500">Failures</dt><dd className="font-mono text-slate-200">{health?.failures ?? 0}</dd>
                <dt className="text-slate-500">Avg latency</dt>
                <dd className="font-mono text-slate-200">{health?.avg_latency_ms != null ? `${Math.round(health.avg_latency_ms)}ms` : '—'}</dd>
                <dt className="text-slate-500">Last error</dt><dd className="truncate font-mono text-slate-200">{health?.last_error ?? '—'}</dd>
              </dl>
            </Panel>
          )
        })}
      </div>

      <Panel title="Routes">
        <table className="w-full text-left text-sm">
          <thead className="text-[11px] tracking-wider text-slate-500 uppercase">
            <tr><th className="px-4 py-2 font-medium">Agent</th><th className="px-4 py-2 font-medium">Provider · model</th><th className="px-4 py-2 font-medium">Why</th></tr>
          </thead>
          <tbody className="divide-y divide-white/[0.05]">
            {Object.entries(info.routes).map(([agent, route]) => (
              <tr key={agent}>
                <td className="px-4 py-2 capitalize text-slate-200">{agent}</td>
                <td className="px-4 py-2 font-mono text-xs text-slate-300">{route ? `${route.provider} · ${route.model}` : 'simulated'}</td>
                <td className="px-4 py-2 text-xs text-slate-400">{route?.reason ?? 'No configured provider and model, or disabled by an operator'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <p className="text-xs text-slate-500">Health counters live in the API process and reset when it restarts.</p>
    </div>
  )
}
