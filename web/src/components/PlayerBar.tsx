import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronUp,
  Disc3,
  ListMusic,
  Eye,
  EyeOff,
  Pencil,
  Pause,
  Play,
  Repeat1,
  Repeat2,
  Shuffle,
  SkipBack,
  SkipForward,
  Volume2,
  X,
} from 'lucide-react'
import SongEditModal from './SongEditModal'
import type { Song } from '../types'
import { usePlayer } from '../hooks/playerContext'
import { api } from '../lib/api'
import { currentLineIndex, fmtTime, lrcToPlain, parseLrc } from '../lib/lrc'

function PlayerCover({ song, className = '' }: { song: Song; className?: string }) {
  const url = api.coverUrl(song)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  if (!url || failedUrl === url) {
    return <div className={`flex items-center justify-center bg-panel2 ${className}`}><Disc3 size={24} className="text-accent" /></div>
  }
  return <img src={url} alt="" onError={() => setFailedUrl(url)} className={`object-cover ${className}`} />
}

export default function PlayerBar({ onSongSaved }: { onSongSaved?: (song: Song) => void }) {
  const [editing, setEditing] = useState<Song | null>(null)
  const {
    current,
    updateSongInfo,
    playing,
    buffering,
    playbackError,
    retry,
    time,
    duration,
    volume,
    expanded,
    playbackMode,
    toggle,
    next,
    prev,
    seek,
    setVolume,
    setExpanded,
    setPlaybackMode,
    queue,
  } = usePlayer()
  const [lyrics, setLyrics] = useState<string | null>(null)
  const [lyricsVisible, setLyricsVisible] = useState(() => {
    try { return localStorage.getItem('mp_lyrics_visible') !== 'false' }
    catch { return true }
  })
  const lyricBoxRef = useRef<HTMLDivElement>(null)

  const currentId = current?.id

  useEffect(() => {
    if (!currentId) {
      setLyrics(null)
      return
    }
    setLyrics(null)
    let alive = true
    api.song(currentId)
      .then((s) => alive && setLyrics(s.lrc || s.lyrics || null))
      .catch(() => alive && setLyrics(null))
    return () => { alive = false }
  }, [currentId, expanded])

  const toggleLyrics = () => {
    setLyricsVisible((visible) => {
      const next = !visible
      try { localStorage.setItem('mp_lyrics_visible', String(next)) } catch { /* storage unavailable */ }
      return next
    })
  }

  const lrcLines = useMemo(() => parseLrc(lyrics), [lyrics])
  const plain = useMemo(() => (lyrics && !lrcLines.length ? lrcToPlain(lyrics) : ''), [lyrics, lrcLines])
  const activeLine = useMemo(() => currentLineIndex(lrcLines, time), [lrcLines, time])

  useEffect(() => {
    if (!expanded || !lrcLines.length) return
    const box = lyricBoxRef.current
    if (!box) return
    const el = box.querySelector<HTMLElement>(`[data-line="${activeLine}"]`)
    if (el) {
      box.scrollTo({ top: el.offsetTop - box.clientHeight / 2 + el.clientHeight / 2, behavior: 'smooth' })
    }
  }, [activeLine, expanded, lrcLines])

  useEffect(() => {
    if (!expanded) return
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false)
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = originalOverflow
      document.removeEventListener('keydown', onKey)
    }
  }, [expanded, setExpanded])

  if (!current) return null

  const pct = duration > 0 ? Math.min(100, Math.max(0, (time / duration) * 100)) : 0
  const volPct = volume * 100
  const modeMeta = playbackMode === 'shuffle'
    ? { label: '随机播放', next: 'one' as const, Icon: Shuffle }
    : playbackMode === 'one'
      ? { label: '单曲循环', next: 'repeat' as const, Icon: Repeat1 }
      : { label: '列表循环', next: 'shuffle' as const, Icon: Repeat2 }
  const playbackModeButton = (large = false) => (
    <button
      onClick={() => setPlaybackMode(modeMeta.next)}
      className={`${large ? 'p-3' : 'p-2'} rounded-full text-accent hover:text-accent-soft transition-colors`}
      title={`${modeMeta.label}（点击切换）`}
      aria-label={`${modeMeta.label}，点击切换播放模式`}
    >
      <modeMeta.Icon size={large ? 21 : 18} />
    </button>
  )

  const sliderStyle = (p: number) =>
    ({ '--range-bg': `linear-gradient(to right, var(--color-accent) ${p}%, var(--color-line) ${p}%)` }) as React.CSSProperties


  return (
    <>
      {playbackError && <div role="alert" className="fixed inset-x-4 z-[60] mx-auto max-w-lg rounded-xl border border-danger/30 bg-panel p-3 text-sm text-ink shadow-xl" style={{ bottom: 'calc(6rem + env(safe-area-inset-bottom, 0px))' }}>
        {playbackError}<button onClick={retry} className="ml-2 min-h-11 px-2 text-accent underline">重试</button>
      </div>}
      {/* 底部播放条 */}
      <div className="player-dock fixed bottom-0 left-0 right-0 z-40 border-t border-line bg-bg2/90 backdrop-blur-xl">
        <div className="max-w-[1400px] mx-auto px-3 sm:px-4 h-[80px] flex items-center gap-2 sm:gap-4">
          <div className="flex items-center gap-2.5 flex-1 sm:flex-none sm:w-56 md:w-64 min-w-0">
            <button onClick={() => setExpanded(true)} aria-label="展开封面与歌词" className="shrink-0">
              <PlayerCover song={current} className="w-12 h-12 rounded-xl" />
            </button>
            <div className="min-w-0">
              <div className="text-sm font-medium truncate">{current.title}</div>
              <div className="text-xs text-muted truncate">{current.artist}</div>
              {buffering && <div role="status" className="text-[11px] text-accent">正在缓冲…</div>}
            </div>
          </div>

          <div className="flex items-center gap-1.5 sm:mx-auto">
            <span className="hidden sm:block">{playbackModeButton()}</span>
            <button aria-label="上一曲" onClick={prev} className="min-w-11 min-h-11 flex items-center justify-center rounded-full text-muted hover:text-ink transition-colors">
              <SkipBack size={19} fill="currentColor" />
            </button>
            <button
              aria-label={playing ? '暂停' : '播放'}
              onClick={toggle}
              className="w-11 h-11 shrink-0 rounded-full bg-primary hover:bg-primary/85 text-white flex items-center justify-center transition-colors"
            >
              {playing ? <Pause size={19} fill="currentColor" /> : <Play size={19} fill="currentColor" className="ml-0.5" />}
            </button>
            <button aria-label="下一曲" onClick={next} className="min-w-11 min-h-11 flex items-center justify-center rounded-full text-muted hover:text-ink transition-colors">
              <SkipForward size={19} fill="currentColor" />
            </button>
          </div>

          <div className="hidden lg:flex flex-1 max-w-xl items-center gap-3">
            <span className="text-[11px] text-faint tabular-nums w-9 text-right">{fmtTime(time)}</span>
            <input
              type="range"
              aria-label="播放进度"
              min={0}
              max={duration || 1}
              step={0.5}
              value={time}
              onChange={(e) => seek(Number(e.target.value))}
              className="flex-1"
              style={sliderStyle(pct)}
            />
            <span className="text-[11px] text-faint tabular-nums w-9">{fmtTime(duration)}</span>
          </div>

          <div className="hidden md:flex items-center gap-2 w-40">
            <Volume2 size={16} className="text-faint shrink-0" />
            <input
              type="range"
              min={0}
              aria-label="音量"
              max={1}
              step={0.02}
              value={volume}
              onChange={(e) => setVolume(Number(e.target.value))}
              className="w-20"
              style={sliderStyle(volPct)}
            />
          </div>

          <button
            onClick={() => setExpanded(!expanded)}
            aria-label={expanded ? '收起播放器' : '展开播放器'}
            aria-expanded={expanded}
            className="ml-auto min-w-11 min-h-11 flex items-center justify-center rounded-lg text-muted hover:text-ink transition-colors"
            title={expanded ? '收起' : '展开播放器（歌词）'}
          >
            {expanded ? <ChevronDown size={18} /> : <ChevronUp size={18} />}
          </button>
        </div>
      </div>

      {/* 展开的全屏播放器 + 歌词 */}
      {expanded && (
        <div role="dialog" aria-label="正在播放" className="player-full fixed inset-0 z-50 bg-bg/97 backdrop-blur-2xl flex flex-col fade-in-up">
          <div className="flex items-center justify-between px-4 pt-4 sm:px-8 sm:pt-6">
            <div className="flex items-center gap-2 text-sm text-muted">
              <ListMusic size={15} className="text-accent" />
              {queue.length > 1 ? `播放队列 ${queue.length} 首` : '单曲播放'}
            </div>
            <div className="flex items-center gap-2">
              <button aria-label="编辑当前歌曲信息" title="编辑歌曲名、歌手与专辑" onClick={() => setEditing(current)} className="grid h-11 w-11 place-items-center rounded-xl border border-line bg-panel text-muted hover:text-accent"><Pencil size={16} /></button>
              <button
                onClick={toggleLyrics}
                className="inline-flex items-center gap-1.5 px-3 py-2.5 rounded-xl bg-panel border border-line text-xs text-muted hover:text-ink transition-colors"
              >
                {lyricsVisible ? <EyeOff size={15} /> : <Eye size={15} />}
                {lyricsVisible ? '关闭歌词' : '显示歌词'}
              </button>
              <button
                aria-label="收起播放器"
                onClick={() => setExpanded(false)}
                className="p-2.5 rounded-xl bg-panel border border-line text-muted hover:text-ink transition-colors"
              >
                <X size={17} />
              </button>
            </div>
          </div>

          <div className="flex-1 min-h-0 flex items-center justify-center gap-8 lg:gap-16 px-4 sm:px-8 lg:px-12 py-6 lg:py-8 max-w-6xl mx-auto w-full">
            {/* 封面（静态） */}
            <div className="hidden md:flex flex-col items-center gap-8">
              <div className="now-artwork w-72 h-72 rounded-3xl overflow-hidden">
                <PlayerCover song={current} className="w-full h-full" />
              </div>
              <div className="text-center">
                <div className="text-xl font-semibold">{current.title}</div>
                <div className="text-sm text-muted mt-1">{current.artist}</div>
                {current.album && <div className="text-xs text-faint mt-1">{current.album}</div>}
              </div>
            </div>

            {/* 歌词 */}
            <div className="flex-1 min-w-0 max-w-xl h-full flex flex-col">
              <div className="md:hidden flex items-center gap-4 pb-5">
                <PlayerCover song={current} className="now-artwork h-24 w-24 shrink-0 rounded-2xl" />
                <div className="min-w-0">
                  <p className="text-[10px] tracking-[.14em] text-accent mb-2">正在播放</p>
                  <h2 className="text-xl font-semibold line-clamp-2">{current.title}</h2>
                  <p className="mt-1 truncate text-sm text-muted">{current.artist}</p>
                </div>
              </div>
              <div className="relative flex-1 min-h-0 overflow-y-auto py-8 md:py-16" ref={lyricBoxRef}>
                {!lyricsVisible ? (
                  <div className="h-full flex flex-col items-center justify-center text-center">
                    <EyeOff size={22} className="text-faint mb-3" />
                    <p className="text-sm text-muted">歌词已关闭</p>
                    <button onClick={toggleLyrics} className="text-xs text-accent mt-2 hover:text-accent-soft">
                      重新显示
                    </button>
                  </div>
                ) : lrcLines.length > 0 ? (
                  <div className="flex flex-col items-center gap-3.5">
                    {lrcLines.map((line, i) => {
                      const active = i === activeLine
                      return (
                        <button
                          key={`${line.time}-${i}`}
                          data-line={i}
                          onClick={() => seek(line.time)}
                          className={`lyric-line text-center transition-all ${
                            active
                              ? 'text-ink text-2xl font-medium scale-105'
                              : 'text-muted text-lg hover:text-ink/80'
                          }`}
                        >
                          {line.text || '♪'}
                        </button>
                      )
                    })}
                  </div>
                ) : plain ? (
                  <p className="whitespace-pre-wrap text-center text-muted leading-loose text-base">{plain}</p>
                ) : (
                  <p className="text-center text-faint text-sm pt-20">这首歌没有找到歌词</p>
                )}
              </div>

              {/* 控制区 */}
              <div className="flex flex-col items-center gap-4 pb-2">
                {buffering && <div role="status" className="flex items-center gap-2 text-xs text-muted">
                  正在缓冲…<button onClick={retry} className="min-h-11 px-2 text-accent underline">重新加载</button>
                </div>}
                <div className="flex items-center gap-3 w-full max-w-md">
                  <span className="text-[11px] text-faint tabular-nums w-9 text-right">{fmtTime(time)}</span>
                  <input
                    type="range"
                    aria-label="播放进度"
                    min={0}
                    max={duration || 1}
                    step={0.5}
                    value={time}
                    onChange={(e) => seek(Number(e.target.value))}
                    className="flex-1"
                    style={sliderStyle(pct)}
                  />
                  <span className="text-[11px] text-faint tabular-nums w-9">{fmtTime(duration)}</span>
                </div>
                <div className="flex items-center gap-2">
                  {playbackModeButton(true)}
                  <button aria-label="上一曲" onClick={prev} className="p-3 rounded-full text-muted hover:text-ink transition-colors">
                    <SkipBack size={22} fill="currentColor" />
                  </button>
                  <button
                    aria-label={playing ? '暂停' : '播放'}
                    onClick={toggle}
                    className="w-14 h-14 rounded-full bg-primary hover:bg-primary/85 text-white flex items-center justify-center transition-colors"
                  >
                    {playing ? (
                      <Pause size={24} fill="currentColor" />
                    ) : (
                      <Play size={24} fill="currentColor" className="ml-1" />
                    )}
                  </button>
                  <button aria-label="下一曲" onClick={next} className="p-3 rounded-full text-muted hover:text-ink transition-colors">
                    <SkipForward size={22} fill="currentColor" />
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
      {editing && <SongEditModal key={editing.id} song={editing} onClose={() => setEditing(null)} onSaved={song => {
        updateSongInfo(song); onSongSaved?.(song)
        if (current?.id === song.id) setLyrics(song.lrc || song.lyrics || null)
      }} />}
    </>
  )
}
