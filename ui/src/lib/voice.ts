/* Browser-native speech for the SME interview.

   Deliberately uses the Web Speech API rather than a cloud service: no backend
   route, no audio leaving the browser, no per-minute cost, and nothing to
   configure per deployment. The trade is support — recognition is Chromium and
   Safari only — so EVERY entry point here is capability-checked and the caller
   is expected to keep its text path working unchanged. Voice is an addition to
   the typed interview, never a replacement for it. */

export interface Recognizer {
  start(): void
  stop(): void
}

type SpeechWindow = Window & {
  SpeechRecognition?: new () => any
  webkitSpeechRecognition?: new () => any
}

function recognitionCtor(): (new () => any) | null {
  if (typeof window === 'undefined') return null
  const w = window as SpeechWindow
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export function canSpeak(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

export function canListen(): boolean {
  return recognitionCtor() !== null
}

/* ---- output: reading the agent's turn aloud ---- */

let current: SpeechSynthesisUtterance | null = null

function preferredVoice(): SpeechSynthesisVoice | null {
  try {
    const voices = window.speechSynthesis.getVoices()
    if (!voices.length) return null
    // A natural-sounding English voice if one exists; otherwise let the OS pick.
    return (
      voices.find((v) => /en-GB|en_GB/.test(v.lang) && /natural|neural|premium/i.test(v.name)) ??
      voices.find((v) => /en-US|en_US/.test(v.lang) && /natural|neural|premium/i.test(v.name)) ??
      voices.find((v) => v.lang.startsWith('en')) ??
      null
    )
  } catch {
    return null
  }
}

export function speak(text: string, onEnd?: () => void): void {
  if (!canSpeak() || !text.trim()) return
  try {
    stopSpeaking()
    const u = new SpeechSynthesisUtterance(text)
    const v = preferredVoice()
    if (v) u.voice = v
    u.rate = 0.98 // a touch under default: these are dense, unfamiliar questions
    u.pitch = 1
    u.onend = () => { current = null; onEnd?.() }
    u.onerror = () => { current = null; onEnd?.() }
    current = u
    window.speechSynthesis.speak(u)
  } catch {
    onEnd?.()
  }
}

export function stopSpeaking(): void {
  if (!canSpeak()) return
  try {
    window.speechSynthesis.cancel()
  } catch { /* nothing useful to do */ }
  current = null
}

export function isSpeaking(): boolean {
  try {
    return canSpeak() && (window.speechSynthesis.speaking || current !== null)
  } catch {
    return false
  }
}

/* Some browsers populate getVoices() asynchronously; warming it early means the
   first utterance is not stuck with the default voice. */
export function warmVoices(): void {
  if (!canSpeak()) return
  try {
    window.speechSynthesis.getVoices()
  } catch { /* ignore */ }
}

/* ---- input: dictating an answer ---- */

export interface ListenHandlers {
  onInterim: (text: string) => void   // live, still changing
  onFinal: (text: string) => void     // settled phrase
  onError: (message: string) => void
  onEnd: () => void
}

const ERRORS: Record<string, string> = {
  'not-allowed': 'Microphone permission was refused — allow it in the address bar to dictate.',
  'service-not-allowed': 'Microphone permission was refused — allow it in the address bar to dictate.',
  'no-speech': "I didn't catch anything — try again, a little closer to the mic.",
  'audio-capture': 'No microphone was found.',
  network: 'Speech recognition needs a network connection and could not reach it.',
  aborted: '',   // user-initiated stop: not an error worth showing
}

export function createRecognizer(handlers: ListenHandlers): Recognizer | null {
  const Ctor = recognitionCtor()
  if (!Ctor) return null

  const rec = new Ctor()
  rec.lang = 'en-GB'
  rec.continuous = true        // let someone think mid-sentence without being cut off
  rec.interimResults = true
  rec.maxAlternatives = 1

  rec.onresult = (event: any) => {
    let interim = ''
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i]
      const text = result[0]?.transcript ?? ''
      if (result.isFinal) handlers.onFinal(text.trim())
      else interim += text
    }
    if (interim) handlers.onInterim(interim.trim())
  }
  rec.onerror = (event: any) => {
    const message = ERRORS[event?.error] ?? `Speech recognition failed (${event?.error ?? 'unknown'}).`
    if (message) handlers.onError(message)
  }
  rec.onend = () => handlers.onEnd()

  return {
    start() {
      // Never listen while we are talking, or the mic transcribes our own voice.
      stopSpeaking()
      try { rec.start() } catch { /* already started */ }
    },
    stop() {
      try { rec.stop() } catch { /* already stopped */ }
    },
  }
}
