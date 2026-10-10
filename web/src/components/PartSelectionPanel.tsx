import { useState } from 'react'
import { api } from '../lib/api'
import { fmtTime } from '../lib/lrc'
import type { SavedCollection, Song } from '../types'

export default function PartSelectionPanel({ collection, songs, onSaved }: {
  collection: SavedCollection; songs: Song[]; onSaved: () => void
}) {
  const groups = new Map<string, Song[]>()
  for (const song of songs.filter(song => (song.parts?.length || 0) > 1)) groups.set(song.bvid, [...(groups.get(song.bvid) || []), song])
  const [selected, setSelected] = useState(() => new Set(songs.filter(song => (song.parts?.length || 0) > 1).filter(song => song.download_requested === 1 || (!song.file_path && song.part_index === 1 && song.download_requested == null)).map(song => song.id)))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const choose = (tracks: Song[], mode: 'first' | 'all' | 'none') => setSelected(previous => {
    const next = new Set(previous)
    for (const song of tracks) {
      if (mode === 'all' || mode === 'first' && song.part_index === 1) next.add(song.id)
      else next.delete(song.id)
    }
    return next
  })
  const save = async () => {
    setBusy(true); setError('')
    try { await api.selectCollectionParts(collection.id, [...selected], [...groups.keys()]); onSaved() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <section aria-label={`${collection.title} 多P歌曲列表`} className="flex w-full flex-col rounded-2xl border border-accent/30 bg-panel p-3 sm:p-5">
      <h3 className="mb-2 text-sm font-medium">{collection.title} · 多 P 歌曲</h3>
      <p className="mb-3 text-xs text-muted">勾选每个 P 独立下载，已有音频自动跳过。未选分P不会在每日更新时自动下载。</p>
      <div className="mb-3 flex flex-wrap gap-2">{(['first', 'all', 'none'] as const).map(mode => <button disabled={busy} key={mode} onClick={() => choose([...groups.values()].flat(), mode)} className="min-h-11 rounded-lg border border-line px-3 text-xs">{mode === 'first' ? '只下载首P' : mode === 'all' ? '下载全部' : '取消全选'}</button>)}</div>
      <div className="space-y-4">
        {[...groups.entries()].map(([bv, tracks]) => <div key={bv} className="rounded-xl border border-line p-3">
          <p className="mb-2 break-words text-sm">{tracks[0].raw_title}</p>
          <div className="mb-2 flex flex-wrap gap-2 text-xs text-accent">
            <button className="min-h-11 rounded-lg border border-line px-3" aria-label={`此视频只下载首P：${tracks[0].raw_title}`} disabled={busy} onClick={() => choose(tracks, 'first')}>只下载首P</button>
            <button className="min-h-11 rounded-lg border border-line px-3" aria-label={`此视频下载全部：${tracks[0].raw_title}`} disabled={busy} onClick={() => choose(tracks, 'all')}>下载全部</button>
            <button className="min-h-11 rounded-lg border border-line px-3" aria-label={`此视频取消全选：${tracks[0].raw_title}`} disabled={busy} onClick={() => choose(tracks, 'none')}>取消全选</button>
          </div>
          {tracks.map(song => <label key={song.id} className="flex min-h-11 cursor-pointer items-center gap-3 border-t border-line py-2 text-sm">
            <input disabled={busy} aria-label={`选择 P${song.part_index} ${song.part_title}`} type="checkbox" checked={selected.has(song.id)} onChange={() => setSelected(prev => { const next = new Set(prev); if (next.has(song.id)) next.delete(song.id); else next.add(song.id); return next })} className="h-4 w-4 shrink-0 accent-primary" />
            <span className="min-w-0 flex-1 break-words">P{song.part_index} {song.part_title || song.title}<span className="ml-2 text-xs text-faint">{fmtTime(song.duration || 0)}{song.file_path ? ' · 已有音频' : ''}</span></span>
          </label>)}
        </div>)}
      </div>
      {error && <p role="alert" className="mt-3 text-xs text-danger">{error}</p>}
      <button disabled={busy} onClick={() => void save()} className="mt-4 min-h-11 rounded-xl bg-primary px-4 text-sm text-white disabled:opacity-50">{busy ? '正在加入下载队列…' : selected.size ? `保存选择并下载 ${selected.size} 首` : '保存选择，暂不下载'}</button>
  </section>
}
