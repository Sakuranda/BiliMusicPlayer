import { useRef, useState } from 'react'
import { ExternalLink, FileText, Loader2, Upload, X, RefreshCw } from 'lucide-react'
import { api } from '../lib/api'
import type { Song } from '../types'
import MetadataEvidence from './MetadataEvidence'

export default function SongEditModal({
  song,
  onClose,
  onSaved,
}: {
  song: Song
  onClose: () => void
  onSaved: (s: Song) => void
}) {
  const [title, setTitle] = useState(song.title)
  const [artist, setArtist] = useState(song.artist)
  const [album, setAlbum] = useState(song.album || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [coverBusy, setCoverBusy] = useState(false)
  const [coverSong, setCoverSong] = useState(song)
  const [coverMessage, setCoverMessage] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [suggestion, setSuggestion] = useState<import('../types').MetadataSuggestion | null>(null)
  const analyze = async () => {
    setAnalyzing(true)
    setError('')
    try { setSuggestion(await api.analyzeSong(song.id)) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setAnalyzing(false) }
  }
  const [lyricsSource, setLyricsSource] = useState(song.lyrics_source)
  const [lyricsBusy, setLyricsBusy] = useState(false)
  const busy = saving || lyricsBusy || coverBusy
  const lyricInputRef = useRef<HTMLInputElement>(null)

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const updated = await api.updateSong(song.id, {
        title: title.trim() || song.title,
        artist: artist.trim() || song.artist,
        album: album.trim(),
      })
      onSaved(updated)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const uploadLyrics = async (file: File | undefined) => {
    if (!file) return
    setLyricsBusy(true)
    setError('')
    try {
      const updated = await api.uploadLyrics(song.id, file)
      setLyricsSource(updated.lyrics_source)
      onSaved(updated)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLyricsBusy(false)
      if (lyricInputRef.current) lyricInputRef.current.value = ''
    }
  }

  const removeLyrics = async () => {
    setLyricsBusy(true)
    setError('')
    try {
      const updated = await api.deleteLyrics(song.id)
      setLyricsSource(null)
      onSaved(updated)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLyricsBusy(false)
    }
  }

  return (
    <div
      role="dialog" aria-modal="true" aria-label="编辑歌曲信息"
      className="fixed inset-0 z-[80] bg-black/60 backdrop-blur-sm flex items-center justify-center fade-in-up"
      onClick={onClose}
    >
      <div
        className="w-[480px] max-w-[92vw] max-h-[90vh] overflow-y-auto bg-panel border border-line rounded-2xl p-5 sm:p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-base font-medium">编辑歌曲信息</h3>
          <button onClick={onClose} className="p-1.5 rounded-lg text-muted hover:text-ink transition-colors">
            <X size={16} />
          </button>
        </div>

        <label className="block text-xs text-muted mb-1.5">歌曲名</label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          autoFocus
          className="w-full bg-bg2 border border-line rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-accent/60 transition-colors mb-4"
        />
        <label className="block text-xs text-muted mb-1.5">歌手</label>
        <input
          value={artist}
          onChange={(e) => setArtist(e.target.value)}
          className="w-full bg-bg2 border border-line rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-accent/60 transition-colors mb-4"
        />
        <label className="block text-xs text-muted mb-1.5">专辑（可选）</label>
        <input
          value={album}
          onChange={(e) => setAlbum(e.target.value)}
          className="w-full bg-bg2 border border-line rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-accent/60 transition-colors"
        />

        <div className="text-[11px] text-faint mt-2">
          原标题：{song.raw_title} · UP主：{song.uploader}
        </div>
        <a
          href={api.videoUrl(song)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-accent hover:text-accent-soft mt-1.5 transition-colors"
          onClick={(e) => e.stopPropagation()}
        >
          在B站打开视频 <ExternalLink size={11} />
        </a>

        <div className="mt-4 flex items-center gap-3 rounded-xl border border-line p-3">
          {coverSong.cover && <img src={api.coverUrl(coverSong)} alt="歌曲封面" className="h-14 w-14 rounded-lg object-cover" />}
          <div className="min-w-0 flex-1"><button disabled={busy} onClick={async () => {
            setCoverBusy(true); setError(''); setCoverMessage('')
            try { const updated = await api.refreshCover(song.id); setCoverSong(updated); onSaved(updated); setCoverMessage('封面已更新，客户端扫描并刷新后可见') }
            catch (e) { setError(e instanceof Error ? e.message : String(e)) }
            finally { setCoverBusy(false) }
          }} className="inline-flex min-h-11 items-center gap-2 text-xs text-accent disabled:opacity-50"><RefreshCw size={14} className={coverBusy ? 'spin' : ''} />{coverBusy ? '正在下载封面…' : '重新下载歌曲封面'}</button>
          {coverMessage && <p role="status" className="text-xs text-muted">{coverMessage}</p>}</div>
        </div>
        <div className="mt-4 rounded-xl border border-line p-3">
          <button onClick={() => void analyze()} disabled={analyzing || busy} className="min-h-10 text-xs text-accent disabled:opacity-50">
            {analyzing ? '正在综合标题、标签和曲库歌手…' : '重新识别歌名与演唱者'}
          </button>
          {suggestion && <div className="text-xs leading-relaxed">
            <div className="break-words">建议：{suggestion.title} · {suggestion.artist}</div>
            <MetadataEvidence metadata={suggestion} />
            <button className="mt-2 min-h-10 rounded-lg bg-panel2 px-3 text-xs" onClick={() => { setTitle(suggestion.title); setArtist(suggestion.artist) }}>填入建议</button>
            <p className="mt-1 text-faint">核对后点保存，演唱者会写入音频并同步到 iOS。</p>
          </div>}
          {!suggestion && <MetadataEvidence metadata={song.metadata} />}
        </div>

        <div className="mt-5 rounded-xl border border-line bg-bg2/70 p-3.5">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-accent-dim flex items-center justify-center">
              <FileText size={15} className="text-accent" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium">歌词文件</div>
              <div className="text-[11px] text-faint mt-0.5">
                {lyricsSource ? `当前来源：${lyricsSource === 'upload' ? '手动上传' : lyricsSource}` : '暂时没有歌词'}
              </div>
            </div>
            <input
              ref={lyricInputRef}
              type="file"
              accept=".lrc,.txt,text/plain"
              className="hidden"
              onChange={(e) => uploadLyrics(e.target.files?.[0])}
            />
            <button
              onClick={() => lyricInputRef.current?.click()}
              disabled={busy}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-panel2 border border-line text-xs hover:border-accent/50 transition-colors disabled:opacity-50"
            >
              {lyricsBusy ? <Loader2 size={13} className="spin" /> : <Upload size={13} />}
              {lyricsSource ? '替换' : '上传'}
            </button>
            {lyricsSource && (
              <button
                onClick={removeLyrics}
                disabled={busy}
                className="px-2.5 py-2 rounded-lg text-xs text-muted hover:text-danger hover:bg-danger/10 transition-colors disabled:opacity-50"
              >
                删除
              </button>
            )}
          </div>
          <p className="text-[10px] text-faint mt-2.5">支持 .lrc 时间轴歌词和 .txt 纯文本，最大 1 MB</p>
        </div>

        {error && <div className="text-xs text-danger mt-3">{error}</div>}

        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            className="px-4 py-2.5 rounded-xl bg-panel2 border border-line text-sm text-muted hover:text-ink transition-colors"
          >
            取消
          </button>
          <button
            onClick={save}
            disabled={busy}
            className="px-4 py-2.5 rounded-xl bg-primary hover:bg-primary/85 text-white text-sm font-medium transition-colors disabled:opacity-50"
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
