/**
 * Voice input helpers for the ticket description (Chrome's Web Speech API).
 *
 * Everything here is plain logic with no React and no direct `window` access,
 * so it can be unit-tested in Node (see tests/voice.test.mjs).
 */

/** The parts of the browser's speech recognizer that CampusFix uses. */
export interface Recognizer {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  start(): void
  stop(): void
  abort(): void
  onresult: ((event: RecognizerResultEvent) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
}

export type RecognizerConstructor = new () => Recognizer

/** One recognition result: a list of guesses (best first), plus whether it is final. */
export type RecognizerResult = ArrayLike<{ transcript: string }> & { isFinal?: boolean }

export interface RecognizerResultEvent {
  resultIndex: number
  results: ArrayLike<RecognizerResult>
}

/**
 * Find the browser's speech recognizer, or null if there is none.
 * Chrome provides it under the prefixed name `webkitSpeechRecognition`.
 */
export function getRecognizer(scope: object = globalThis): RecognizerConstructor | null {
  const s = scope as {
    SpeechRecognition?: RecognizerConstructor
    webkitSpeechRecognition?: RecognizerConstructor
  }
  return s.SpeechRecognition ?? s.webkitSpeechRecognition ?? null
}

/** Join the final, best-guess transcripts in a result event into one string. */
export function transcriptFrom(event: RecognizerResultEvent): string {
  const parts: string[] = []
  for (let i = event.resultIndex; i < event.results.length; i++) {
    const result = event.results[i]
    if (result.isFinal === false) continue
    const best = result[0]
    const text = best ? best.transcript.trim() : ''
    if (text) parts.push(text)
  }
  return parts.join(' ')
}

/**
 * Add spoken text after what is already typed, never replacing it.
 * A space is inserted only when the existing text doesn't already end in whitespace.
 */
export function appendTranscript(current: string, spoken: string): string {
  const text = spoken.trim()
  if (!text) return current
  if (current === '' || /\s$/.test(current)) return current + text
  return current + ' ' + text
}

/** A plain-language message for a recognizer error code, or null when nothing needs saying. */
export function voiceErrorMessage(code: string): string | null {
  switch (code) {
    case 'aborted':
      return null // the user (or the page) stopped it on purpose
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone access is blocked. Allow it from the icon in the address bar, or type instead.'
    case 'no-speech':
      return "I didn't hear anything. Try again, or type instead."
    case 'audio-capture':
      return 'No microphone was found. You can type instead.'
    case 'network':
      return 'Voice input needs an internet connection. You can type instead.'
    default:
      return 'Voice input stopped. You can try again, or type instead.'
  }
}
