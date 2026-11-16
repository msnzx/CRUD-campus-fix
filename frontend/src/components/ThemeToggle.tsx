import { useEffect, useState } from 'react'
import type { MouseEvent } from 'react'

type Theme = 'light' | 'dark'

function preferredTheme(): Theme {
  const saved = localStorage.getItem('caldwellfix-theme')
  if (saved === 'light' || saved === 'dark') return saved
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(preferredTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('caldwellfix-theme', theme)
  }, [theme])

  function changeTheme(event: MouseEvent<HTMLButtonElement>) {
    const next: Theme = theme === 'light' ? 'dark' : 'light'
    const reducedMotion = document.documentElement.dataset.reducedMotion === 'true' ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const rect = event.currentTarget.getBoundingClientRect()
    document.documentElement.style.setProperty('--theme-transition-x', `${rect.left + rect.width / 2}px`)
    document.documentElement.style.setProperty('--theme-transition-y', `${rect.top + rect.height / 2}px`)

    if (!reducedMotion) {
      const traveler = document.createElement('span')
      traveler.className = `theme-celestial ${next === 'dark' ? 'moon' : 'sun'}`
      traveler.textContent = next === 'dark' ? '☾' : '☀'
      traveler.setAttribute('aria-hidden', 'true')
      document.body.appendChild(traveler)
      window.setTimeout(() => traveler.remove(), 1450)
    }

    const documentWithTransition = document as Document & {
      startViewTransition?: (callback: () => void) => unknown
    }
    if (!reducedMotion && documentWithTransition.startViewTransition) {
      documentWithTransition.startViewTransition(() => {
        document.documentElement.dataset.theme = next
        localStorage.setItem('caldwellfix-theme', next)
        setTheme(next)
      })
      return
    }
    setTheme(next)
  }

  const isDark = theme === 'dark'
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={changeTheme}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      <span className="theme-toggle-track" aria-hidden="true">
        <span className="theme-toggle-icon">{isDark ? '☾' : '☀'}</span>
      </span>
      <span className="theme-toggle-label">{isDark ? 'Dark' : 'Light'}</span>
    </button>
  )
}
