import { THEMES } from '../theme/themes'
import type { ThemeDef } from '../theme/themes'
import { usePreferences } from '../theme/preferences'
import { Panel } from './ui'

const ACCENTS = ['#22d3ee', '#818cf8', '#a78bfa', '#f472b6', '#fb923c', '#facc15', '#4ade80', '#2dd4bf']

/** A miniature of the app drawn in the theme's own colors, so previews work before selecting. */
function ThemePreview({ theme, selected, onSelect }: { theme: ThemeDef; selected: boolean; onSelect: () => void }) {
  const muted = `color-mix(in oklab, ${theme.fg}, ${theme.bg} 55%)`
  const surface = `color-mix(in oklab, ${theme.fg}, ${theme.bg} 94%)`
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`group overflow-hidden rounded-xl text-left ring-2 transition ${selected ? 'ring-accent' : 'ring-transparent hover:ring-white/20'}`}
    >
      <div className="flex h-24 gap-1.5 p-2" style={{ background: theme.bg, fontFamily: theme.font }}>
        <div className="w-6 rounded-md" style={{ background: surface }} />
        <div className="flex flex-1 flex-col gap-1.5">
          <div className="h-2 w-2/3 rounded" style={{ background: theme.fg }} />
          <div className="h-1.5 w-1/2 rounded" style={{ background: muted }} />
          <div className="mt-auto flex gap-1.5">
            <div className="h-5 flex-1 rounded" style={{ background: surface }} />
            <div className="h-5 w-8 rounded" style={{ background: theme.accent }} />
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between bg-white/[0.03] px-3 py-2 text-xs">
        <span className="text-slate-200">{theme.name}</span>
        {selected && <span className="text-cyan-300">Active</span>}
      </div>
    </button>
  )
}

function Segmented<T extends string | number>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg bg-white/[0.04] p-1 ring-1 ring-white/[0.06]">
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          onClick={() => onChange(option.value)}
          className={`rounded-md px-3 py-1 text-xs transition-colors ${value === option.value ? 'bg-white/[0.1] text-white' : 'text-slate-400 hover:text-slate-200'}`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

function Row({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div>
        <p className="text-sm text-slate-200">{title}</p>
        <p className="text-xs text-slate-500">{hint}</p>
      </div>
      {children}
    </div>
  )
}

export function SettingsPage() {
  const { prefs, resolvedTheme, update } = usePreferences()
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-white">Appearance</h1>
        <p className="text-sm text-slate-400">Saved in this browser only. Changes apply instantly to every page.</p>
      </header>

      <Panel title="Theme" action={
        <button type="button" onClick={() => update({ theme: 'system' })}
          className={`text-xs ${prefs.theme === 'system' ? 'text-cyan-300' : 'text-slate-400 hover:text-slate-200'}`}>
          {prefs.theme === 'system' ? `Following system (${resolvedTheme})` : 'Follow system'}
        </button>
      }>
        <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 lg:grid-cols-5">
          {THEMES.map((theme) => (
            <ThemePreview key={theme.id} theme={theme} selected={resolvedTheme === theme.id}
              onSelect={() => update({ theme: theme.id })} />
          ))}
        </div>
      </Panel>

      <Panel title="Customize">
        <div className="divide-y divide-white/[0.05]">
          <Row title="Accent color" hint="Overrides the theme accent for buttons, links and focus rings.">
            <div className="flex items-center gap-1.5">
              {ACCENTS.map((color) => (
                <button key={color} type="button" aria-label={`Accent ${color}`} onClick={() => update({ accent: color })}
                  className={`size-6 rounded-full ring-2 ring-offset-2 ring-offset-page ${prefs.accent === color ? 'ring-white/70' : 'ring-transparent'}`}
                  style={{ background: color }} />
              ))}
              <input type="color" aria-label="Custom accent" value={prefs.accent ?? '#22d3ee'}
                onChange={(e) => update({ accent: e.target.value })} className="size-7 cursor-pointer rounded bg-transparent" />
              <button type="button" onClick={() => update({ accent: null })} className="ml-1 text-xs text-slate-400 hover:text-slate-200">
                Reset
              </button>
            </div>
          </Row>
          <Row title="Density" hint="Compact fits more tickets and events on screen.">
            <Segmented label="Density" value={prefs.density} onChange={(density) => update({ density })}
              options={[{ value: 'comfortable', label: 'Comfortable' }, { value: 'compact', label: 'Compact' }]} />
          </Row>
          <Row title="Font size" hint="Scales the whole interface.">
            <Segmented label="Font size" value={prefs.fontSize} onChange={(fontSize) => update({ fontSize })}
              options={[{ value: 14, label: 'Small' }, { value: 16, label: 'Default' }, { value: 18, label: 'Large' }]} />
          </Row>
          <Row title="Reduce motion" hint="Turns off animated transitions. Your OS setting is always respected.">
            <Segmented label="Reduce motion" value={prefs.reducedMotion ? 'on' : 'off'} onChange={(v) => update({ reducedMotion: v === 'on' })}
              options={[{ value: 'off', label: 'Off' }, { value: 'on', label: 'On' }]} />
          </Row>
          <Row title="Sidebar" hint="Collapse navigation to icons.">
            <Segmented label="Sidebar" value={prefs.sidebarCollapsed ? 'collapsed' : 'expanded'}
              onChange={(v) => update({ sidebarCollapsed: v === 'collapsed' })}
              options={[{ value: 'expanded', label: 'Expanded' }, { value: 'collapsed', label: 'Collapsed' }]} />
          </Row>
        </div>
      </Panel>
    </div>
  )
}
