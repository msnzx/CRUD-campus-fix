import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/lib/voice.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
})
const exports = {}
runInNewContext(outputText, { exports })
const { getRecognizer, transcriptFrom, appendTranscript, voiceErrorMessage } = exports

// A result as the browser shapes it: an array-like of guesses with an isFinal flag.
const result = (transcript, isFinal = true) => Object.assign([{ transcript }], { isFinal })

test('no recognizer means the button is hidden', () => {
  assert.equal(getRecognizer({}), null)
})

test('Chrome\'s prefixed recognizer is found, and the standard name wins if both exist', () => {
  class Webkit {}
  class Standard {}
  assert.equal(getRecognizer({ webkitSpeechRecognition: Webkit }), Webkit)
  assert.equal(getRecognizer({ SpeechRecognition: Standard, webkitSpeechRecognition: Webkit }), Standard)
})

test('spoken text fills an empty description', () => {
  assert.equal(appendTranscript('', ' water is dripping from the ceiling '), 'water is dripping from the ceiling')
})

test('spoken text is added after typed text with one space, never replacing it', () => {
  assert.equal(appendTranscript('Room 214.', 'the light flickers'), 'Room 214. the light flickers')
  assert.equal(appendTranscript('Room 214.\n', 'the light flickers'), 'Room 214.\nthe light flickers')
  assert.equal(appendTranscript('Room 214. ', 'the light flickers'), 'Room 214. the light flickers')
})

test('silence leaves the description exactly as it was', () => {
  const typed = '  Typed text, untouched  '
  assert.equal(appendTranscript(typed, ''), typed)
  assert.equal(appendTranscript(typed, '   '), typed)
})

test('only final, best-guess transcripts from the new results are used', () => {
  const event = {
    resultIndex: 1,
    results: [result('old phrase'), result(' the sink '), result('still guessing', false), result('is leaking')],
  }
  assert.equal(transcriptFrom(event), 'the sink is leaking')
  assert.equal(transcriptFrom({ resultIndex: 0, results: [] }), '')
})

test('error codes become plain messages; a deliberate stop says nothing', () => {
  assert.equal(voiceErrorMessage('aborted'), null)
  assert.match(voiceErrorMessage('not-allowed'), /blocked/)
  assert.match(voiceErrorMessage('no-speech'), /didn't hear/)
  assert.match(voiceErrorMessage('network'), /internet/)
  assert.match(voiceErrorMessage('something-new'), /type instead/)
})
