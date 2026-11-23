import { useEffect, useRef, useState } from 'react'
import { getRecognizer, transcriptFrom, voiceErrorMessage, type Recognizer } from '../lib/voice'

/**
 * A microphone button that listens for one spoken sentence or phrase and hands the
 * text to `onTranscript`. It renders nothing in browsers without speech recognition.
 */
export default function VoiceInputButton({
  onTranscript,
  disabled = false,
}: {
  onTranscript: (text: string) => void
  disabled?: boolean
}) {
  // Looked up once; it doesn't change while the page is open.
  const [Recognition] = useState(() => getRecognizer())
  const recognizer = useRef<Recognizer | null>(null)
  const [listening, setListening] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  // Stop listening when the form is submitting or the page is left.
  useEffect(() => {
    if (disabled) recognizer.current?.abort()
  }, [disabled])
  useEffect(() => () => recognizer.current?.abort(), [])

  if (!Recognition) return null

  function start() {
    if (!Recognition) return
    const rec = new Recognition()
    rec.lang = 'en-US'
    rec.continuous = false // stop by itself after the speaker pauses
    rec.interimResults = false // only deliver the finished text
    rec.maxAlternatives = 1
    rec.onresult = (event) => {
      const text = transcriptFrom(event)
      if (text) onTranscript(text)
    }
    rec.onerror = (event) => setMessage(voiceErrorMessage(event.error))
    rec.onend = () => {
      setListening(false)
      recognizer.current = null
    }
    recognizer.current = rec
    setMessage(null)
    try {
      rec.start()
      setListening(true)
    } catch {
      recognizer.current = null
      setMessage(voiceErrorMessage(''))
    }
  }

  function stop() {
    recognizer.current?.stop() // finishes and still delivers what was heard
  }

  return (
    <div className="voice-input">
      <button
        type="button"
        className={listening ? 'voice-button listening' : 'voice-button'}
        onClick={listening ? stop : start}
        disabled={disabled}
        aria-pressed={listening}
      >
        {listening ? 'Listening… click to stop' : '🎤 Speak instead of typing'}
      </button>
      <div className="hint">
        Optional. Chrome sends your voice to Google's speech service to turn it into text.
        CampusFix gets only the text, and you can edit it before submitting.
      </div>
      {message && (
        <div className="hint voice-error" role="status">
          {message}
        </div>
      )}
    </div>
  )
}
