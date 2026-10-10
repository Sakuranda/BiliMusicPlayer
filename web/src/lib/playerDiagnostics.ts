// Bounded, local-only diagnostics. Never retain stream URLs, cookies or tokens.
type Entry = { at: string; event: string; songId?: number; hidden: boolean; detail?: string }
const entries: Entry[] = []
export function recordPlayerEvent(event: string, songId?: number, detail?: string) {
  entries.push({ at: new Date().toISOString(), event, songId, hidden: document.hidden, detail })
  if (entries.length > 100) entries.shift()
}
export function downloadPlayerDiagnostics() {
  const blob = new Blob([JSON.stringify({ userAgent: navigator.userAgent, events: entries }, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'musicplayer-playback-diagnostics.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
