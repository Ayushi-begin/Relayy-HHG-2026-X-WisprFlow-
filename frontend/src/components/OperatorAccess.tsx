import { useEffect, useState } from 'react'
import { api } from '../api'
import type { DeliveryReadiness, Health } from '../api'
import { Badge } from './ui'

interface Props {
  token: string | null
  onUnlock: (token: string) => void
  onLock: () => void
}

export function OperatorAccess({ token, onUnlock, onLock }: Props) {
  const [value, setValue] = useState('')
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    let active = true
    api.health().then((result) => {
      if (!active) return
      setHealth(result)
      // Offline analysis demos intentionally permit mutations without a configured token.
      if (result.operator_required === false) onUnlock('')
    }).catch(() => active && setHealth(null))
    return () => {
      active = false
    }
  }, [onUnlock])

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!value.trim()) return
    onUnlock(value)
    setValue('')
  }

  const providers: Array<[string, DeliveryReadiness]> = health?.providers
    ? Object.entries(health.providers)
    : health
      ? [
          ['email', { configured: health.email_configured, ready: health.provider_access_verified, state: health.email_configured ? 'configured' : 'missing' }],
          ['refund', { configured: health.refund_configured, ready: health.provider_access_verified, state: health.refund_configured ? 'configured' : 'missing' }],
        ]
      : []
  const unlocked = token !== null
  const needsToken = health?.operator_required !== false
  return (
    <section className="mx-3 mt-3 flex flex-col gap-3 rounded-2xl border border-amber-400/20 bg-amber-400/[0.04] p-3.5 lg:mx-5 lg:flex-row lg:items-center lg:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-amber-200">{unlocked ? (needsToken ? 'Operator token supplied' : 'Operator actions available') : 'Operator access locked'}</h2>
          <Badge tone="amber">Loopback only</Badge>
          {health?.delivery_mode && <Badge tone={health.delivery_mode === 'test' ? 'cyan' : 'slate'}>Delivery: {health.delivery_mode}</Badge>}
        </div>
        <p className="mt-1 text-xs text-slate-300">
          Viewing is available. Unlock to create tickets, run agents, review replies, or recover operations.
        </p>
        <p className="mt-1 text-[11px] text-slate-500">The token stays only in this tab’s memory, clears on reload, and is never included in URLs or browser storage.</p>
        {providers.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-slate-400">
            {providers.map(([name, readiness]) => (
              <Badge key={name} tone={readiness.ready ? 'emerald' : readiness.configured ? 'amber' : 'slate'}>
                {name}: {readiness.state ?? (readiness.ready ? 'ready' : 'blocked')}
              </Badge>
            ))}
          </div>
        )}
      </div>
      {unlocked ? (
        <button type="button" onClick={onLock} className="shrink-0 rounded-xl bg-white/[0.06] px-3 py-2 text-xs font-medium text-slate-200 ring-1 ring-white/10 hover:bg-white/10">
          Lock actions
        </button>
      ) : (
        <form onSubmit={submit} className="w-full shrink-0 lg:w-[390px]">
          <label className="mb-1 block text-[11px] text-slate-400" htmlFor="operator-token">Operator token</label>
          <div className="flex gap-2">
            <input
              id="operator-token"
              type="password"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="Enter operator token"
              className="min-w-0 flex-1 rounded-xl border border-white/[0.09] bg-black/30 px-3 py-2 text-xs text-slate-100 outline-none focus:border-amber-300/50 focus:ring-2 focus:ring-amber-300/10"
            />
            <button type="submit" disabled={!value.trim()} className="rounded-xl bg-amber-400 px-3 py-2 text-xs font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-50">
              Unlock actions
            </button>
          </div>
        </form>
      )}
    </section>
  )
}
