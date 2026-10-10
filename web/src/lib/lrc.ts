export interface LrcLine {
  time: number
  text: string
}

/** 解析 LRC 文本为有序时间轴。 */
export function parseLrc(lrc: string | null | undefined): LrcLine[] {
  if (!lrc) return []
  const lines: LrcLine[] = []
  for (const raw of lrc.split('\n')) {
    const stamps = [...raw.matchAll(/\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g)]
    const text = raw.replace(/\[[^\]]*\]/g, '').trim()
    for (const m of stamps) {
      const ms = (m[3] || '0').padEnd(3, '0')
      const t = Number(m[1]) * 60 + Number(m[2]) + Number(ms) / 1000
      if (text) lines.push({ time: t, text })
    }
  }
  return lines.sort((a, b) => a.time - b.time)
}

/** 当前时间对应的歌词行下标。 */
export function currentLineIndex(lines: LrcLine[], time: number): number {
  let idx = -1
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].time <= time + 0.2) idx = i
    else break
  }
  return idx
}

/** 把 LRC 转成纯文本（无时间轴时展示用）。 */
export function lrcToPlain(lrc: string | null | undefined): string {
  if (!lrc) return ''
  return lrc
    .split('\n')
    .map((l) => l.replace(/\[[^\]]*\]/g, '').trim())
    .filter(Boolean)
    .join('\n')
}

export function fmtTime(t: number): string {
  if (!Number.isFinite(t) || t < 0) t = 0
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}
