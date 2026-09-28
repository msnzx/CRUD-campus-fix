// Emergency detection.
//
// This runs client-side, at submission time, on purpose. It must be instant
// and it must work when the AI classifier is unavailable, because the whole
// point is to interrupt someone before they wait in a maintenance queue
// during an actual emergency. See product-spec.md section 13.

const EMERGENCY_PATTERNS: Array<{ pattern: RegExp; reason: string }> = [
  { pattern: /\bfires?\b|\bburning\b|\bsmoke\b|\bflames?\b/i, reason: 'possible fire' },
  { pattern: /\bgas\s*(leak|smell)|\bsmell(s|ing)?\s+gas\b|\bcarbon\s*monoxide\b/i, reason: 'possible gas leak' },
  { pattern: /\bflood(ing|ed)?\b|\bburst\s+pipe\b|\bwater\s+pouring\b/i, reason: 'possible flooding' },
  { pattern: /\binjur(y|ed|ies)\b|\bbleeding\b|\bunconscious\b|\bnot\s+breathing\b|\boverdose\b/i, reason: 'possible injury' },
  { pattern: /\bassault(ed)?\b|\bweapons?\b|\bgun\b|\bknife\b|\bthreat(ening|ened)?\b|\bintruder\b/i, reason: 'possible threat to safety' },
  { pattern: /\belectrical\s+fire\b|\bsparking\b|\blive\s+wire\b|\bexposed\s+wir(e|ing)\b/i, reason: 'electrical hazard' },
  { pattern: /\bcollapse[d]?\b|\bceiling\s+(fell|falling)\b/i, reason: 'possible structural failure' },
  { pattern: /\btrapped\b|\bstuck\s+in\s+(the\s+)?elevator\b/i, reason: 'someone may be trapped' },
]

export interface EmergencyMatch {
  isEmergency: boolean
  reasons: string[]
}

export function detectEmergency(...texts: Array<string | undefined | null>): EmergencyMatch {
  const haystack = texts.filter(Boolean).join(' \n ')
  const reasons = EMERGENCY_PATTERNS.filter(({ pattern }) => pattern.test(haystack)).map(
    ({ reason }) => reason,
  )
  return { isEmergency: reasons.length > 0, reasons: [...new Set(reasons)] }
}

// Caldwell University Campus Safety. Confirmed by the project owner.
export const CAMPUS_SAFETY_PHONE = '(973) 618-3259'
export const EMERGENCY_PHONE = '911'
