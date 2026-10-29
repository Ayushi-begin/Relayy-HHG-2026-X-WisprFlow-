import { useState } from 'react'
import { api } from '../api'
import type { DeliveryOperation, TicketDetail } from '../api'
import { operationMeta, operationStateMeta, moneyMinor, toneClasses } from '../lib/format'
import { Badge, Spinner } from './ui'

interface Props {
  detail: TicketDetail
  token: string | null
  onDetail: (detail: TicketDetail) => void
}

export function OperationOutcome({ detail, token, onDetail }: Props) {
  const [busyOperation, setBusyOperation] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const operations = detail.operations ?? []
  if (!operations.length && !detail.fulfillment_state) return null

  const refund = operations.find((operation) => operation.kind === 'refund')
  const email = operations.find((operation) => operation.kind === 'email')
  const unknownRefund = refund?.state === 'unknown'
  const completed = Boolean(refund?.state === 'succeeded' && email?.state === 'accepted') || (Boolean(email) && !refund && email?.state === 'accepted')
  const partial = refund?.state === 'succeeded' && email && email.state !== 'accepted'
  const failed = operations.some((operation) => operation.state === 'failed' || operation.state === 'blocked')
  const tone = completed ? 'emerald' : unknownRefund ? 'amber' : failed || partial ? 'rose' : 'cyan'
  const title = completed
    ? 'Email accepted · test refund succeeded'
    : unknownRefund
      ? 'Refund outcome unknown'
      : partial
        ? 'Refund complete · email needs attention'
        : 'Delivery status'
  const intro = completed
    ? 'Provider operations completed. Inbox delivery is not confirmed.'
    : unknownRefund
      ? 'Check Stripe before taking another action. Re-run and completion stay blocked.'
      : partial
        ? 'The refund is preserved. Recover only the email operation.'
        : detail.manual_followup_required
          ? 'Delivery needs manual follow-up.'
          : 'Provider status is shown separately from authorization.'

  const recover = async (operation: DeliveryOperation, action: 'retry' | 'reconcile') => {
    if (token === null) return
    setBusyOperation(`${operation.id}:${action}`)
    setError(null)
    try {
      onDetail(await api.recoverOperation(detail.id, operation.id, action, detail.version, token))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyOperation(null)
    }
  }

  return (
    <section aria-label="Operation outcome" className={`mt-4 rounded-2xl p-4 ring-1 ring-inset ${toneClasses[tone]}`}>
      <div className="flex items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-black/20 text-base" aria-hidden>
          {completed ? '✓' : unknownRefund ? '◌' : failed || partial ? '!' : '•'}
        </div>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="text-xs opacity-75">{intro}</p>
        </div>
      </div>

      <div className="mt-3 space-y-2">
        {operations.map((operation) => (
          <OperationRow
            key={operation.id}
            operation={operation}
            locked={token === null}
            busy={busyOperation}
            onRecover={recover}
          />
        ))}
      </div>

      {error && <p role="alert" className="mt-3 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-200 ring-1 ring-rose-400/25">{error}</p>}
      {unknownRefund && <p className="mt-3 text-xs text-amber-100">Do not submit another refund. Reconcile the existing operation first.</p>}
      {partial && <p className="mt-3 text-xs text-rose-100">The completed refund will not run again; only an email action may be offered by the server.</p>}
      {operations.length > 0 && <p className="mt-3 text-[11px] text-slate-400">Stripe test mode · approved test recipients only · no live refunds</p>}
    </section>
  )
}

function OperationRow({
  operation,
  locked,
  busy,
  onRecover,
}: {
  operation: DeliveryOperation
  locked: boolean
  busy: string | null
  onRecover: (operation: DeliveryOperation, action: 'retry' | 'reconcile') => void
}) {
  const meta = operationMeta(operation)
  const state = operationStateMeta(operation.state)
  const amount = operation.amount_minor === null ? null : moneyMinor(operation.amount_minor, operation.currency ?? 'usd')
  return (
    <article className="rounded-xl bg-black/20 px-3 py-2.5 ring-1 ring-white/[0.06]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <strong className="text-xs text-slate-100">{meta.label}</strong>
          <span className="text-[11px] text-slate-500">· {meta.provider}</span>
        </div>
        <Badge tone={state.tone}>{state.label}{operation.test_mode ? ' · test mode' : ''}</Badge>
      </div>
      <p className="mt-1 text-xs text-slate-300">{meta.description(operation, amount)}</p>
      {operation.recipient && <p className="mt-1 text-[11px] text-slate-500">Approved test recipient: {operation.recipient}</p>}
      {operation.provider_status && <p className="mt-1 text-[11px] text-slate-500">Provider status: {operation.provider_status}</p>}
      {(operation.error_code || operation.error_message) && (
        <p role="alert" className="mt-2 text-xs text-rose-200">{[operation.error_code, operation.error_message].filter(Boolean).join(' · ')}</p>
      )}
      {operation.allowed_actions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {operation.allowed_actions.map((action) => {
            const loading = busy === `${operation.id}:${action}`
            return (
              <button
                key={action}
                type="button"
                disabled={locked || busy !== null}
                onClick={() => onRecover(operation, action)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.06] px-2.5 py-1 text-xs font-medium text-slate-100 ring-1 ring-white/10 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loading && <Spinner className="size-3" />}
                {action === 'reconcile' ? 'Check status' : `Retry ${meta.label.toLowerCase()} only`}
              </button>
            )
          })}
        </div>
      )}
    </article>
  )
}
