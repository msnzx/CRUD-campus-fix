import { useEffect, useState } from 'react'

const CAMPUS_SLIDES = [
  '/cover-slides/student-center.png',
  '/cover-slides/aerial-campus.png',
  '/cover-slides/student-life.png',
  '/cover-slides/dining-hall.png',
  '/cover-slides/student-lounge.png',
] as const

// Reversible signed-in background preview. It is intentionally isolated from
// page data and uses only the already supplied cover-photo assets.
export default function AppPhotoBackdrop() {
  const [activeSlide, setActiveSlide] = useState(0)
  const [reducedMotion] = useState(() =>
    localStorage.getItem('caldwellfix-reduced-motion') === 'true' ||
    window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    if (reducedMotion) return
    const timer = window.setInterval(() => {
      setActiveSlide((current) => (current + 1) % CAMPUS_SLIDES.length)
    }, 7000)
    return () => window.clearInterval(timer)
  }, [reducedMotion])

  return (
    <div className="app-photo-backdrop" aria-hidden="true">
      {CAMPUS_SLIDES.map((src, index) => (
        <img key={src} className={index === activeSlide ? 'active' : ''} src={src} alt="" />
      ))}
      <div className="app-photo-backdrop-overlay" />
    </div>
  )
}
