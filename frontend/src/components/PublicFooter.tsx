import { BrandMark } from './ui'

/**
 * Reversible public-page footer preview. It is intentionally used only by the
 * cover and login pages, so signed-in work pages keep their full working area.
 */
export default function PublicFooter() {
  return (
    <footer className="public-footer">
      <BrandMark size={18} />
      <span><strong>CaldwellFix</strong> · Caldwell University</span>
      <span className="public-footer-slogan">Fixing Our Campus, One Report at a Time.</span>
    </footer>
  )
}
