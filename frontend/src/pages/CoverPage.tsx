import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { BrandMark } from '../components/ui'

const COVER_SLIDES = [
  { src: '/cover-slides/student-center.png', alt: 'Caldwell University Student Center in spring' },
  { src: '/cover-slides/aerial-campus.png', alt: 'Aerial view of the Caldwell University campus' },
  { src: '/cover-slides/student-life.png', alt: 'Students gathered outside on the Caldwell University campus' },
  { src: '/cover-slides/dining-hall.png', alt: 'Caldwell University dining area' },
  { src: '/cover-slides/student-lounge.png', alt: 'Caldwell University student lounge' },
] as const

export default function CoverPage() {
  const [accessibilityOpen, setAccessibilityOpen] = useState(false)
  const [largeText, setLargeText] = useState(() => localStorage.getItem('caldwellfix-large-text') === 'true')
  const [highContrast, setHighContrast] = useState(() => localStorage.getItem('caldwellfix-high-contrast') === 'true')
  const [reducedMotion, setReducedMotion] = useState(() => localStorage.getItem('caldwellfix-reduced-motion') === 'true')
  const [strongFocus, setStrongFocus] = useState(() => localStorage.getItem('caldwellfix-strong-focus') === 'true')
  const [activeSlide, setActiveSlide] = useState(0)
  const [systemReducedMotion, setSystemReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)

  useEffect(() => {
    document.documentElement.dataset.textSize = largeText ? 'large' : 'normal'
    localStorage.setItem('caldwellfix-large-text', String(largeText))
  }, [largeText])

  useEffect(() => {
    document.documentElement.dataset.highContrast = String(highContrast)
    localStorage.setItem('caldwellfix-high-contrast', String(highContrast))
  }, [highContrast])

  useEffect(() => {
    document.documentElement.dataset.reducedMotion = String(reducedMotion)
    localStorage.setItem('caldwellfix-reduced-motion', String(reducedMotion))
  }, [reducedMotion])

  useEffect(() => {
    document.documentElement.dataset.strongFocus = String(strongFocus)
    localStorage.setItem('caldwellfix-strong-focus', String(strongFocus))
  }, [strongFocus])

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setSystemReducedMotion(media.matches)
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    if (reducedMotion || systemReducedMotion) return
    const timer = window.setInterval(() => {
      setActiveSlide((current) => (current + 1) % COVER_SLIDES.length)
    }, 7000)
    return () => window.clearInterval(timer)
  }, [reducedMotion, systemReducedMotion])

  return (
    <main className="cover-page">
      <div className="cover-slideshow" aria-live="polite" aria-label="Caldwell University campus scenes">
        {COVER_SLIDES.map((slide, index) => (
          <img
            key={slide.src}
            className={`cover-slide ${index === activeSlide ? 'active' : ''}`}
            src={slide.src}
            alt={index === activeSlide ? slide.alt : ''}
            aria-hidden={index !== activeSlide}
          />
        ))}
      </div>
      <div className="cover-photo-overlay" aria-hidden="true" />
      <div className="cover-ambient cover-ambient-one" aria-hidden="true" />
      <div className="cover-ambient cover-ambient-two" aria-hidden="true" />
      <div className="cover-grid" aria-hidden="true" />
      <div className="cover-utilities">
        <a href="mailto:campusfix@caldwell.edu">Contact us</a>
        <button type="button" className="link" onClick={() => setAccessibilityOpen(!accessibilityOpen)} aria-expanded={accessibilityOpen}>
          Accessibility
        </button>
      </div>
      <div className="cover-card">
        <p className="cover-kicker">Caldwell University · Campus care made simple</p>
        <div className="cover-logo" aria-label="CaldwellFix">
          <BrandMark size={116} />
        </div>
        <h1>CaldwellFix</h1>
        <p className="cover-slogan">Fixing Our Campus, One Report at a Time.</p>
        <p className="cover-intro">A clearer, faster way to report campus issues and follow every step toward a solution.</p>

        <div className="cover-actions" aria-label="Get started">
          <Link className="primary cover-action" to="/login"><span aria-hidden="true">↗</span> Log in</Link>
          <Link className="secondary cover-action" to="/login?mode=signup"><span aria-hidden="true">＋</span> Sign up</Link>
        </div>
        <p className="cover-trust"><span aria-hidden="true">●</span> Built for the Caldwell community</p>

        <div className="cover-slide-indicators" aria-label={`Campus scene ${activeSlide + 1} of ${COVER_SLIDES.length}`}>
          {COVER_SLIDES.map((slide, index) => <span key={slide.src} className={index === activeSlide ? 'active' : ''} aria-hidden="true" />)}
        </div>

        {accessibilityOpen && (
          <section className="cover-accessibility" aria-label="Accessibility options">
            <div>
              <strong>Accessibility options</strong>
              <p>These settings are saved on this device and apply throughout CaldwellFix.</p>
            </div>
            <div className="accessibility-options">
              <button type="button" aria-pressed={largeText} onClick={() => setLargeText(!largeText)}>
                {largeText ? '✓ ' : ''}Larger text
              </button>
              <button type="button" aria-pressed={highContrast} onClick={() => setHighContrast(!highContrast)}>
                {highContrast ? '✓ ' : ''}High contrast
              </button>
              <button type="button" aria-pressed={reducedMotion} onClick={() => setReducedMotion(!reducedMotion)}>
                {reducedMotion ? '✓ ' : ''}Reduce motion
              </button>
              <button type="button" aria-pressed={strongFocus} onClick={() => setStrongFocus(!strongFocus)}>
                {strongFocus ? '✓ ' : ''}Extra keyboard focus
              </button>
            </div>
            <p className="accessibility-tip">Keyboard tip: use Tab to move between controls, Shift + Tab to move back, and Enter or Space to select.</p>
          </section>
        )}
      </div>
      <button type="button" className="cover-quit" onClick={() => window.location.assign('about:blank')}>× Quit</button>
    </main>
  )
}
