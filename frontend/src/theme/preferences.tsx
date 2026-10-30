import { MotionConfig } from 'framer-motion'
import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { themeById, themeVars } from './themes'

export interface Preferences {
  theme: string // a theme id or 'system'
  accent: string | null
  density: 'comfortable' | 'compact'
  fontSize: number // root px
  reducedMotion: boolean
  sidebarCollapsed: boolean
}

const DEFAULTS: Preferences = {
  theme: 'system', accent: null, density: 'comfortable', fontSize: 16, reducedMotion: false, sidebarCollapsed: false,
}
const KEY = 'relayy.preferences'

// Per-viewer conveniences only; storage can be unavailable, so every access is guarded.
function load(): Preferences {
  try {
    const raw = window.localStorage.getItem(KEY)
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Preferences>) } : DEFAULTS
  } catch {
    return DEFAULTS
  }
}

function systemDark(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return true
  }
}

interface Ctx {
  prefs: Preferences
  resolvedTheme: string
  update: (patch: Partial<Preferences>) => void
}

const PreferencesContext = createContext<Ctx | null>(null)

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Preferences>(load)
  const [dark, setDark] = useState(systemDark)

  useEffect(() => {
    try {
      const query = window.matchMedia('(prefers-color-scheme: dark)')
      const onChange = () => setDark(query.matches)
      query.addEventListener('change', onChange)
      return () => query.removeEventListener('change', onChange)
    } catch {
      return undefined
    }
  }, [])

  const resolvedTheme = prefs.theme === 'system' ? (dark ? 'dark' : 'light') : prefs.theme

  useEffect(() => {
    const theme = themeById(resolvedTheme)
    const root = document.documentElement
    for (const [name, value] of Object.entries(themeVars(theme, prefs.accent))) root.style.setProperty(name, value)
    root.style.colorScheme = theme.scheme
    root.style.fontSize = `${prefs.fontSize}px`
    root.dataset.theme = theme.id
    root.dataset.density = prefs.density
    root.dataset.scheme = theme.scheme
    try {
      window.localStorage.setItem(KEY, JSON.stringify(prefs))
    } catch {
      // storage unavailable: preferences last for this session only
    }
  }, [prefs, resolvedTheme])

  const value = useMemo<Ctx>(() => ({
    prefs, resolvedTheme, update: (patch) => setPrefs((current) => ({ ...current, ...patch })),
  }), [prefs, resolvedTheme])

  return (
    <PreferencesContext.Provider value={value}>
      <MotionConfig reducedMotion={prefs.reducedMotion ? 'always' : 'user'}>{children}</MotionConfig>
    </PreferencesContext.Provider>
  )
}

export function usePreferences(): Ctx {
  const ctx = useContext(PreferencesContext)
  if (!ctx) throw new Error('usePreferences must be used inside PreferencesProvider')
  return ctx
}
