// Theme definitions. Each theme is a background, a foreground and an accent; the neutral scale
// every component uses (Tailwind's slate/white) is derived from them, so all pages adapt.

export interface ThemeDef {
  id: string
  name: string
  scheme: 'dark' | 'light'
  bg: string
  fg: string
  accent: string
  /** Stronger separation between neutrals, for accessibility themes. */
  contrast?: number
  font?: string
}

export const THEMES: ThemeDef[] = [
  { id: 'dark', name: 'Dark', scheme: 'dark', bg: '#0a0e15', fg: '#e6edf6', accent: '#22d3ee' },
  { id: 'light', name: 'Light', scheme: 'light', bg: '#f7f8fa', fg: '#0f172a', accent: '#0891b2' },
  { id: 'solarized-dark', name: 'Solarized Dark', scheme: 'dark', bg: '#002b36', fg: '#eee8d5', accent: '#2aa198' },
  { id: 'solarized-light', name: 'Solarized Light', scheme: 'light', bg: '#fdf6e3', fg: '#073642', accent: '#268bd2' },
  { id: 'dracula', name: 'Dracula', scheme: 'dark', bg: '#21222c', fg: '#f8f8f2', accent: '#bd93f9' },
  { id: 'nord', name: 'Nord', scheme: 'dark', bg: '#2e3440', fg: '#eceff4', accent: '#88c0d0' },
  { id: 'monokai', name: 'Monokai', scheme: 'dark', bg: '#1e1f1c', fg: '#f8f8f2', accent: '#a6e22e' },
  { id: 'midnight', name: 'Midnight OLED', scheme: 'dark', bg: '#000000', fg: '#e5e7eb', accent: '#38bdf8' },
  { id: 'high-contrast', name: 'High Contrast', scheme: 'dark', bg: '#000000', fg: '#ffffff', accent: '#ffd400', contrast: 1.6 },
  {
    id: 'polaroid', name: 'Polaroid', scheme: 'light', bg: '#f3ede3', fg: '#2a241f', accent: '#c2410c',
    font: '"Iowan Old Style", "Palatino Linotype", Georgia, serif',
  },
]

// Share of the background mixed into the foreground for each neutral step.
const STEPS: [number, number][] = [
  [50, 4], [100, 8], [200, 16], [300, 30], [400, 45], [500, 58], [600, 70], [700, 80], [800, 88], [900, 93], [950, 97],
]

const mix = (a: string, b: string, pct: number) => `color-mix(in oklab, ${a}, ${b} ${Math.max(0, Math.min(100, pct))}%)`

export function themeVars(theme: ThemeDef, accentOverride?: string | null): Record<string, string> {
  const accent = accentOverride || theme.accent
  const k = theme.contrast ?? 1
  const vars: Record<string, string> = {
    '--t-bg': theme.bg,
    '--t-fg': theme.fg,
    '--t-white': theme.fg,
    '--t-header': mix(theme.bg, theme.fg, 3),
    '--t-glow': mix(accent, 'transparent', 92),
    '--t-grid': mix(theme.fg, 'transparent', theme.scheme === 'dark' ? 97 : 94),
    '--t-accent': accent,
    '--t-accent-200': mix(accent, theme.fg, 40),
    '--t-accent-300': mix(accent, theme.fg, 20),
    '--t-accent-400': accent,
    '--t-accent-500': mix(accent, theme.bg, 15),
    '--t-on-accent': theme.scheme === 'dark' ? theme.bg : '#ffffff',
    '--t-font': theme.font ?? '"Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
  }
  for (const [step, pct] of STEPS) {
    // On light themes the scale flips: "slate-100" is still the strongest neutral text.
    vars[`--t-slate-${step}`] = mix(theme.fg, theme.bg, Math.round(pct / k))
  }
  return vars
}

export const themeById = (id: string) => THEMES.find((t) => t.id === id) ?? THEMES[0]
