import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, RefreshCw, X, Upload } from 'lucide-react'
import { api } from '../lib/api'
import type { LyricsFailure } from '../types'

export default function LyricsFailures({ onChanged }: { onChanged: () => void }) {
  const [entries, setEntries] = useState<LyricsFailure[]>([])
  const [error, setError] = useState('')
  const [working, setWorking] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const uploadId = useRef<number | null>(null)
  const generation = useRef(0)
  const invalidate = useCallback(() => { generation.current++ }, [])
  const previous = useRef<string | null>(null)
  const callback = useRef(onChanged)
  callback.current = onChanged
  const load = useCallback(async () => {
    const request = ++generation.current
    setLoading(true)
    try {
      const items = await api.lyricsFailures()
      if (request !== generation.current) return
      const signature = JSON.stringify(items.map(item => [item.song_id, item.status, item.attempts]))
      if (previous.current !== null && signature !== previous.current) callback.current()
      previous.current = signature
      setEntries(items.filter(item => !item.dismissed)); setError('')
    } catch (err) { if (request === generation.current) setError(err instanceof Error ? err.message : String(err)) }
    finally { if (request === generation.current) setLoading(false) }
  }, [])
  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      await load()
      if (!stopped) timer = setTimeout(() => void poll(), 15_000)
    }
    void poll()
    return () => { stopped = true; clearTimeout(timer); invalidate() }
  }, [load, invalidate])
  const act = async (id: number, dismiss: boolean) => {
    if (working !== null) return
    setWorking(id)
    try {
      if (dismiss) await api.dismissLyricsFailure(id)
      else { await api.retryLyrics(id); callback.current() }
      await load()
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setWorking(null) }
  }
  const upload = async (file: File | undefined) => {
    const id = uploadId.current
    if (!file || id === null || working !== null) return
    setWorking(id)
    try { await api.uploadLyrics(id, file); callback.current(); await load() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setWorking(null); if (fileInput.current) fileInput.current.value = '' }
  }
  if (!entries.length && !error) return null
  return <section aria-label="歌词匹配通知" className="mx-4 mt-4 space-y-2 sm:mx-6 md:mx-8">
    <input ref={fileInput} type="file" accept=".lrc,.txt,text/plain" className="hidden" onChange={event => void upload(event.target.files?.[0])} />
    {entries.map(entry => {
      const active = entry.status === 'queued' || entry.status === 'downloading'
      const completed = entry.status === 'ready'
      return <article key={entry.song_id} aria-label={`${entry.title} 歌词通知`} className={`rounded-xl border bg-panel p-3 ${completed ? 'border-accent/30' : 'border-danger/30'}`}>
        <div className="flex flex-wrap items-start gap-2">
          {completed ? <Check size={17} className="mt-0.5 shrink-0 text-accent" /> : <AlertTriangle size={17} className="mt-0.5 shrink-0 text-danger" />}
          <div className="min-w-0 flex-1 basis-32"><p className="break-words text-sm font-medium">{entry.title} · {completed ? '重试成功' : active ? '正在匹配' : '歌词未匹配'}</p><p className="mt-1 break-words text-xs text-muted">{entry.artist}</p></div>
          {!completed && <button disabled={working !== null || active} onClick={() => void act(entry.song_id, false)} aria-label={`重试歌词 ${entry.title}`} className="inline-flex min-h-11 shrink-0 items-center gap-1 rounded-lg border border-line px-2 text-xs disabled:opacity-40"><RefreshCw size={13} className={active ? 'animate-spin' : ''} />重试</button>}
          {!completed && <button disabled={working !== null || active} onClick={() => { uploadId.current = entry.song_id; fileInput.current?.click() }} aria-label={`上传 ${entry.title} 的歌词`} className="inline-flex min-h-11 shrink-0 items-center gap-1 rounded-lg border border-line px-2 text-xs disabled:opacity-40"><Upload size={13} />上传歌词</button>}
          <button disabled={working !== null} onClick={() => void act(entry.song_id, true)} aria-label={`关闭 ${entry.title} 的歌词通知`} title="跳过此歌词通知，并停止自动重试" className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-muted disabled:opacity-40"><X size={17} /></button>
        </div>
        {!completed && <p className="mt-2 whitespace-pre-wrap break-words text-xs text-danger">{entry.error}</p>}
        <p className="mt-2 text-[11px] text-muted">{completed ? '歌词已保存，此通知保留到手动关闭。' : active ? '已加入歌词匹配队列，结果会在这里更新。' : `每五分钟自动重试${entry.next_retry_at ? ` · 下次 ${new Date(entry.next_retry_at).toLocaleTimeString('zh-CN')}` : ''}。关闭后停止自动重试。`}</p>
      </article>
    })}
    {error && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-danger/30 bg-panel p-3 text-xs text-danger"><span>{error}</span><button disabled={loading} onClick={() => void load()} className="min-h-11 px-3 underline">重试读取通知</button></div>}
  </section>
}
