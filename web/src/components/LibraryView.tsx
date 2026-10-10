import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Disc3,
  Download,
  Loader2,
  FileText,
  FolderPlus,
  LayoutGrid,
  List,
  ListPlus,
  Pencil,
  Play,
  RefreshCw,
  Search,
  Shuffle,
  Trash2,
  X,
} from 'lucide-react'
import { api } from '../lib/api'
import SongEditModal from './SongEditModal'
import { usePlayer } from '../hooks/playerContext'
import { fmtTime } from '../lib/lrc'
import type { Playlist, SavedCollection, Song } from '../types'

interface Props {
  songs: Song[]
  serverOk: boolean | null
  onRefresh: () => void | Promise<void>
  onGoImport: () => void
}

type LibraryLayout = 'grid' | 'list' | 'artists'

const LS_LIBRARY_LAYOUT = 'mp_library_layout'
const LS_LIBRARY_COVERS = 'mp_library_covers'
const LS_LIBRARY_SORT = 'mp_library_sort'
const SORT_OPTIONS = {
  'added-desc': '添加时间 · 最新在前',
  'added-asc': '添加时间 · 最早在前',
  'name-asc': '名称 · 正序',
  'name-desc': '名称 · 倒序',
} as const
type LibrarySort = keyof typeof SORT_OPTIONS
const nameCollator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' })
function isLibrarySort(value: string | null): value is LibrarySort {
  return value !== null && Object.hasOwn(SORT_OPTIONS, value)
}

function Cover({ song, className }: { song: Song; className?: string }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const url = api.coverUrl(song)
  if (!url || failedUrl === url) {
    return (
      <div className={`flex items-center justify-center bg-panel2 ${className ?? ''}`}>
        <Disc3 size={28} className="text-faint" />
      </div>
    )
  }
  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      onError={() => setFailedUrl(url)}
      className={`object-cover ${className ?? ''}`}
    />
  )
}

function SongActions({
  song,
  onEdit,
  onDelete,
  onPlaylist,
}: {
  song: Song
  onEdit: (song: Song) => void
  onDelete: (song: Song) => void
  onPlaylist: (song: Song) => void
}) {
  return (
    <div className="relative z-10 flex shrink-0 items-center gap-0.5 pointer-events-auto">
      <button
        onClick={(event) => {
          event.stopPropagation()
          onPlaylist(song)
        }}
        className="p-1.5 rounded-lg text-faint hover:text-accent hover:bg-accent-dim transition-colors"
        title="添加到歌单"
        aria-label={`添加 ${song.title} 到歌单`}
      >
        <ListPlus size={13} />
      </button>
      <button
        onClick={(event) => {
          event.stopPropagation()
          onEdit(song)
        }}
        className="p-1.5 rounded-lg text-faint hover:text-accent hover:bg-accent-dim transition-colors"
        title="编辑歌曲信息"
        aria-label={`编辑 ${song.title}`}
      >
        <Pencil size={13} />
      </button>
      <button
        onClick={(event) => {
          event.stopPropagation()
          onDelete(song)
        }}
        className="p-1.5 rounded-lg text-faint hover:text-danger hover:bg-danger/10 transition-colors"
        title="删除歌曲"
        aria-label={`删除 ${song.title}`}
      >
        <Trash2 size={13} />
      </button>
    </div>
  )
}

function PlaylistManager({
  playlists,
  onClose,
  onChanged,
}: {
  playlists: Playlist[]
  onClose: () => void
  onChanged: () => Promise<void>
}) {
  const [name, setName] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingName, setEditingName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError('')
    try {
      await action()
      await onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const create = () => {
    const clean = name.trim()
    if (!clean) return
    void run(async () => {
      await api.createPlaylist(clean)
      setName('')
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm fade-in-up" onClick={onClose}>
      <div className="w-[440px] max-w-[92vw] rounded-2xl border border-line bg-panel p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h3 className="text-base font-medium">管理歌单</h3>
            <p className="mt-0.5 text-[11px] text-faint">创建自己的播放集合，原始音频不会重复占用空间</p>
          </div>
          <button onClick={onClose} className="p-1.5 text-muted hover:text-ink"><X size={16} /></button>
        </div>
        <div className="flex gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') create() }}
            placeholder="新歌单名称"
            maxLength={80}
            className="min-w-0 flex-1 rounded-xl border border-line bg-bg2 px-3 py-2.5 text-sm focus:border-accent/60 focus:outline-none"
          />
          <button onClick={create} disabled={busy || !name.trim()} className="rounded-xl bg-primary px-4 text-sm font-medium text-white disabled:opacity-40">
            创建
          </button>
        </div>
        <div className="mt-4 max-h-72 space-y-2 overflow-y-auto">
          {playlists.length ? playlists.map((playlist) => (
            <div key={playlist.id} className="flex items-center gap-2 rounded-xl border border-line bg-bg2 px-3 py-2.5">
              {editingId === playlist.id ? (
                <input
                  autoFocus
                  value={editingName}
                  onChange={(e) => setEditingName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setEditingId(null)
                    if (e.key === 'Enter' && editingName.trim()) void run(async () => {
                      await api.renamePlaylist(playlist.id, editingName.trim())
                      setEditingId(null)
                    })
                  }}
                  className="min-w-0 flex-1 rounded-lg border border-line bg-panel px-2 py-1 text-sm focus:outline-none"
                />
              ) : (
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">{playlist.name}</div>
                  <div className="text-[10px] text-faint">{playlist.song_count} 首</div>
                </div>
              )}
              <button
                onClick={() => { setEditingId(playlist.id); setEditingName(playlist.name) }}
                className="p-1.5 text-faint hover:text-accent"
                title="重命名"
              ><Pencil size={13} /></button>
              <button
                onClick={() => void run(() => api.deletePlaylist(playlist.id))}
                disabled={busy}
                className="p-1.5 text-faint hover:text-danger disabled:opacity-40"
                title="删除歌单（不会删除歌曲）"
              ><Trash2 size={13} /></button>
            </div>
          )) : <p className="py-8 text-center text-xs text-faint">还没有歌单</p>}
        </div>
        {error && <p className="mt-3 text-xs text-danger">{error}</p>}
      </div>
    </div>
  )
}

function AddToPlaylistModal({
  song,
  playlists,
  activePlaylistId,
  onClose,
  onChanged,
}: {
  song: Song
  playlists: Playlist[]
  activePlaylistId: number | null
  onClose: () => void
  onChanged: () => Promise<void>
}) {
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const add = async (playlist: Playlist) => {
    setBusyId(playlist.id)
    setError('')
    try {
      await api.addPlaylistSong(playlist.id, song.id)
      await onChanged()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }
  const remove = async () => {
    if (activePlaylistId === null) return
    setBusyId(activePlaylistId)
    setError('')
    try {
      await api.removePlaylistSong(activePlaylistId, song.id)
      await onChanged()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm fade-in-up" onClick={onClose}>
      <div className="w-[380px] max-w-[92vw] rounded-2xl border border-line bg-panel p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <div className="min-w-0">
            <h3 className="text-base font-medium">添加到歌单</h3>
            <p className="mt-0.5 truncate text-[11px] text-faint">{song.title} · {song.artist}</p>
          </div>
          <button onClick={onClose} className="p-1.5 text-muted hover:text-ink"><X size={16} /></button>
        </div>
        <div className="max-h-72 space-y-2 overflow-y-auto">
          {playlists.map((playlist) => (
            <button
              key={playlist.id}
              onClick={() => void add(playlist)}
              disabled={busyId !== null}
              className="flex w-full items-center justify-between rounded-xl border border-line bg-bg2 px-3 py-3 text-left transition-colors hover:border-accent/50 disabled:opacity-50"
            >
              <span className="truncate text-sm">{playlist.name}</span>
              <span className="text-[10px] text-faint">{playlist.song_count} 首</span>
            </button>
          ))}
          {!playlists.length && <p className="py-8 text-center text-xs text-faint">请先在曲库顶部创建歌单</p>}
        </div>
        {activePlaylistId !== null && (
          <button onClick={() => void remove()} disabled={busyId !== null} className="mt-3 w-full rounded-xl px-3 py-2.5 text-xs text-danger hover:bg-danger/10 disabled:opacity-50">
            从当前歌单移除
          </button>
        )}
        {error && <p className="mt-3 text-xs text-danger">{error}</p>}
      </div>
    </div>
  )
}


function DeleteModal({
  song,
  onClose,
  onDeleted,
}: {
  song: Song
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')

  const doDelete = async () => {
    setDeleting(true)
    setError('')
    try {
      await api.deleteSong(song.id)
      onDeleted()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setDeleting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center fade-in-up"
      onClick={onClose}
    >
      <div
        className="w-[360px] max-w-[92vw] bg-panel border border-line rounded-2xl p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-medium mb-2">删除歌曲</h3>
        <p className="text-sm text-muted leading-relaxed mb-5">
          确定删除 <span className="text-ink">{song.title}</span> — {song.artist} 吗？
          音频文件和歌词将一并从服务器移除，此操作不可撤销。
        </p>
        {error && <div className="text-xs text-danger mb-4">{error}</div>}
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2.5 rounded-xl bg-panel2 border border-line text-sm text-muted hover:text-ink transition-colors"
          >
            取消
          </button>
          <button
            onClick={doDelete}
            disabled={deleting}
            className="px-4 py-2.5 rounded-xl bg-danger hover:bg-danger/85 text-white text-sm font-medium transition-colors disabled:opacity-50"
          >
            {deleting ? '删除中…' : '确认删除'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function LibraryView({ songs, serverOk, onRefresh, onGoImport }: Props) {
  const { playSong, playQueue, current, playing, setExpanded, updateSongInfo, setPlaybackMode, lyricsVisible, setLyricsVisible } = usePlayer()
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState('')
  const [editing, setEditing] = useState<Song | null>(null)
  const [deleting, setDeleting] = useState<Song | null>(null)
  const [playlistTarget, setPlaylistTarget] = useState<Song | null>(null)
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [collections, setCollections] = useState<SavedCollection[]>([])
  const [selectedPlaylist, setSelectedPlaylist] = useState<number | 'all'>('all')
  const [playlistSongIds, setPlaylistSongIds] = useState<Set<number>>(new Set())
  const [selectedCollection, setSelectedCollection] = useState<number | 'all'>('all')
  const [collectionSongIds, setCollectionSongIds] = useState<Set<number>>(new Set())
  const [artist, setArtist] = useState('all')
  const [artistMessage, setArtistMessage] = useState('')
  const [artistBusy, setArtistBusy] = useState(false)
  const [managingPlaylists, setManagingPlaylists] = useState(false)
  const [localSongs, setLocalSongs] = useState(songs)
  const [layout, setLayoutState] = useState<LibraryLayout>(() =>
    localStorage.getItem(LS_LIBRARY_LAYOUT) === 'artists' ? 'artists' : localStorage.getItem(LS_LIBRARY_LAYOUT) === 'list' ? 'list' : 'grid',
  )
  const [showCovers, setShowCoversState] = useState(
    () => localStorage.getItem(LS_LIBRARY_COVERS) !== 'false',
  )

  const [sort, setSort] = useState<LibrarySort>(() => {
    const saved = localStorage.getItem(LS_LIBRARY_SORT)
    return isLibrarySort(saved) ? saved : 'added-desc'
  })

  // 外部刷新时同步
  useEffect(() => {
    setLocalSongs(songs)
  }, [songs])

  const refreshPlaylists = async () => {
    const next = await api.playlists()
    setPlaylists(next)
    if (selectedPlaylist !== 'all' && !next.some((playlist) => playlist.id === selectedPlaylist)) {
      setSelectedPlaylist('all')
    }
    return next
  }

  useEffect(() => {
    Promise.all([api.playlists(), api.collections()])
      .then(([nextPlaylists, nextCollections]) => {
        setPlaylists(nextPlaylists)
        setCollections(nextCollections)
      })
      .catch(() => {
        setPlaylists([])
        setCollections([])
      })
  }, [])

  useEffect(() => {
    setArtist('all')
    if (selectedPlaylist === 'all') {
      setPlaylistSongIds(new Set())
      return
    }
    let alive = true
    api.playlistSongs(selectedPlaylist)
      .then((items) => { if (alive) setPlaylistSongIds(new Set(items.map((song) => song.id))) })
      .catch(() => { if (alive) setPlaylistSongIds(new Set()) })
    return () => { alive = false }
  }, [selectedPlaylist])

  useEffect(() => {
    setArtist('all')
    if (selectedCollection === 'all') {
      setCollectionSongIds(new Set())
      return
    }
    let alive = true
    api.collectionSongs(selectedCollection)
      .then((items) => { if (alive) setCollectionSongIds(new Set(items.map((song) => song.id))) })
      .catch(() => { if (alive) setCollectionSongIds(new Set()) })
    return () => { alive = false }
  }, [selectedCollection])

  const ready = useMemo(() => localSongs.filter((s) => s.status === 'ready'), [localSongs])
  const others = useMemo(() => localSongs.filter((s) => s.status !== 'ready'), [localSongs])
  const collectionReady = useMemo(
    () => selectedCollection === 'all' ? ready : ready.filter((song) => collectionSongIds.has(song.id)),
    [collectionSongIds, ready, selectedCollection],
  )
  const playlistReady = useMemo(
    () => selectedPlaylist === 'all'
      ? collectionReady
      : collectionReady.filter((song) => playlistSongIds.has(song.id)),
    [collectionReady, playlistSongIds, selectedPlaylist],
  )
  const artists = useMemo(
    () => Array.from(new Set(playlistReady.map((song) => song.artist).filter(Boolean)))
      .sort((a, b) => a.localeCompare(b, 'zh-CN')),
    [playlistReady],
  )

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return playlistReady.filter(
      (s) =>
        (artist === 'all' || s.artist === artist) &&
        (!q ||
          s.title.toLowerCase().includes(q) ||
          s.artist.toLowerCase().includes(q) ||
          (s.album || '').toLowerCase().includes(q) ||
          (s.raw_title || '').toLowerCase().includes(q)),
    ).sort((a, b) => {
      const direction = sort.endsWith('asc') ? 1 : -1
      const comparison = sort.startsWith('name')
        ? nameCollator.compare(a.title, b.title)
        : (Date.parse(a.created_at) || 0) - (Date.parse(b.created_at) || 0)
      return direction * (comparison || a.id - b.id)
    })
  }, [artist, playlistReady, query, sort])

  const refreshPlaylistContext = async () => {
    const [next, nextCollections] = await Promise.all([refreshPlaylists(), api.collections()])
    setCollections(nextCollections)
    if (selectedPlaylist !== 'all' && next.some((playlist) => playlist.id === selectedPlaylist)) {
      const items = await api.playlistSongs(selectedPlaylist)
      setPlaylistSongIds(new Set(items.map((song) => song.id)))
    }
    if (selectedCollection !== 'all') {
      if (nextCollections.some((item) => item.id === selectedCollection)) {
        const items = await api.collectionSongs(selectedCollection)
        setCollectionSongIds(new Set(items.map((song) => song.id)))
      } else setSelectedCollection('all')
    }
  }

  const refreshLibrary = async () => {
    if (refreshing) return
    setRefreshing(true)
    setRefreshError('')
    try { await Promise.all([onRefresh(), refreshPlaylistContext()]) }
    catch (error) { setRefreshError(error instanceof Error ? error.message : String(error)) }
    finally { setRefreshing(false) }
  }

  const setLayout = (next: LibraryLayout) => {
    setLayoutState(next)
    localStorage.setItem(LS_LIBRARY_LAYOUT, next)
  }

  const setShowCovers = (next: boolean) => {
    setShowCoversState(next)
    localStorage.setItem(LS_LIBRARY_COVERS, String(next))
  }

  const play = (song: Song) => playSong(song, filtered)

  const openArtworkAndLyrics = (event: React.MouseEvent, song: Song) => {
    event.stopPropagation()
    // 当前歌曲已在播放时只展开播放器，不要因为再次调用 playSong 而暂停。
    if (current?.id !== song.id || !playing) playSong(song, filtered)
    setExpanded(true)
  }

  return (
    <div className="max-w-6xl mx-auto px-4 pt-6 sm:px-6 md:px-8 md:pt-10">
      <div className="flex flex-col">
        <div className="library-heading flex items-end justify-between gap-4">
          <div>
          <p className="mb-2 text-xs font-medium tracking-[.15em] text-accent">MusicPlayer / 私人收藏</p>
          <h1 className="text-3xl sm:text-[42px] font-semibold tracking-tight mb-2">我的曲库</h1>
          <p className="text-sm text-muted">
            {selectedPlaylist === 'all' && selectedCollection === 'all'
              ? `${ready.length} 首歌曲`
              : `${filtered.length} 首筛选结果`}
            {others.length > 0 ? ` · ${others.length} 首处理中/失败` : ''}
          </p>
          </div>
          <button onClick={onGoImport} className="hidden sm:flex items-center gap-2 rounded-full border border-line px-4 py-2.5 text-sm text-muted hover:text-ink hover:border-accent/50">
            <Download size={16} /> 添加收藏
          </button>
        </div>
        <div className="library-toolbar flex flex-col items-stretch gap-3">
          <div className="flex flex-wrap self-end items-center gap-1 rounded-xl border border-line bg-panel p-1">
            <button type="button" role="switch" aria-label="显示歌词" aria-checked={lyricsVisible}
              onClick={() => setLyricsVisible(!lyricsVisible)}
              className={`inline-flex min-h-11 items-center gap-1 rounded-lg px-2 text-[11px] ${lyricsVisible ? 'bg-panel2 text-ink' : 'text-faint'}`}>
              <FileText size={13} /> 歌词{lyricsVisible ? '开' : '关'}
            </button>
            <span className="mx-1 h-4 w-px bg-line" aria-hidden="true" />
            <span className="pl-2 pr-1 text-[11px] text-muted">封面</span>
            <button
              type="button"
              role="switch"
              aria-label="显示歌曲封面"
              aria-checked={showCovers}
              onClick={() => setShowCovers(!showCovers)}
              className={`relative h-5 w-9 rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
                showCovers ? 'bg-accent' : 'bg-panel2'
              }`}
              title={showCovers ? '隐藏歌曲封面' : '显示歌曲封面'}
            >
              <span
                className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
                  showCovers ? 'translate-x-4' : 'translate-x-0'
                }`}
              />
            </button>
            <span className="mx-1 h-4 w-px bg-line" aria-hidden="true" />
            <button
              type="button"
              aria-pressed={layout === 'grid'}
              onClick={() => setLayout('grid')}
              className={`inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-[11px] transition-colors ${
                layout === 'grid' ? 'bg-panel2 text-ink' : 'text-faint hover:text-muted'
              }`}
              title="卡片视图"
            >
              <LayoutGrid size={13} /> 卡片
            </button>
            <button
              type="button"
              aria-pressed={layout === 'list'}
              onClick={() => setLayout('list')}
              className={`inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-[11px] transition-colors ${
                layout === 'list' ? 'bg-panel2 text-ink' : 'text-faint hover:text-muted'
              }`}
              title="列表视图"
            >
              <List size={13} /> 列表
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button aria-pressed={layout === 'artists'} onClick={() => { setArtist('all'); setLayout('artists') }} className="min-h-11 rounded-xl border border-line bg-panel px-3 text-sm">按歌手分类</button>
            <button disabled={artistBusy} onClick={async () => {
              setArtistBusy(true)
              try { const result = await api.syncArtists(); setArtistMessage(`已同步 ${result.artists} 个歌手分类，请在客户端刷新播放列表和歌单内容。`) }
              catch (error) { setArtistMessage(error instanceof Error ? error.message : String(error)) }
              finally { setArtistBusy(false) }
            }} className="min-h-11 rounded-xl border border-line bg-panel px-3 text-xs text-muted disabled:opacity-50">{artistBusy ? '同步中…' : '同步歌手分类到客户端'}</button>
            <select
              value={selectedCollection}
              onChange={(e) => setSelectedCollection(e.target.value === 'all' ? 'all' : Number(e.target.value))}
              aria-label="按收藏夹筛选"
              className="min-w-0 max-w-44 rounded-xl border border-line bg-panel px-3 py-2 text-sm focus:border-accent/50 focus:outline-none"
            >
              <option value="all">全部收藏夹</option>
              {collections.map((collection) => (
                <option key={collection.id} value={collection.id}>
                  {collection.title} · {collection.song_count}
                </option>
              ))}
            </select>
            <select
              value={selectedPlaylist}
              onChange={(e) => setSelectedPlaylist(e.target.value === 'all' ? 'all' : Number(e.target.value))}
              aria-label="选择歌单"
              className="min-w-0 max-w-40 rounded-xl border border-line bg-panel px-3 py-2 text-sm focus:border-accent/50 focus:outline-none"
            >
              <option value="all">全部歌曲</option>
              {playlists.map((playlist) => <option key={playlist.id} value={playlist.id}>{playlist.name} · {playlist.song_count}</option>)}
            </select>
            <select
              value={artist}
              onChange={(e) => setArtist(e.target.value)}
              aria-label="按歌手筛选"
              className="min-w-0 max-w-36 rounded-xl border border-line bg-panel px-3 py-2 text-sm focus:border-accent/50 focus:outline-none"
            >
              <option value="all">全部歌手</option>
              {artists.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            <select
              aria-label="歌曲排序"
              value={sort}
              onChange={(e) => {
                const value = e.target.value
                if (!isLibrarySort(value)) return
                setSort(value)
                localStorage.setItem(LS_LIBRARY_SORT, value)
              }}
              className="min-w-0 max-w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm focus:border-accent/50 focus:outline-none"
            >
              {Object.entries(SORT_OPTIONS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
            <div className="relative min-w-40 flex-1 sm:flex-none">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="搜索歌曲或歌手"
                placeholder="搜索歌曲、歌手…"
                className="w-full sm:w-56 bg-panel border border-line rounded-xl pl-9 pr-3 py-2 text-sm placeholder:text-faint focus:outline-none focus:border-accent/50 transition-colors"
              />
            </div>
            <button
              onClick={() => setManagingPlaylists(true)}
              title="管理歌单"
              className="p-2.5 rounded-xl bg-panel border border-line text-muted hover:text-accent transition-colors"
            >
              <FolderPlus size={15} />
            </button>
            <button
              onClick={() => void refreshLibrary()}
              disabled={refreshing}
              aria-label="刷新曲库"
              title="刷新曲库"
              className="p-2.5 rounded-xl bg-panel border border-line text-muted hover:text-ink transition-colors"
            >
              <RefreshCw size={15} className={refreshing ? 'animate-spin' : undefined} />
            </button>
            <button
              onClick={() => { setPlaybackMode('repeat'); playQueue(filtered) }}
              disabled={!filtered.length}
              className="flex shrink-0 items-center gap-2 px-4 py-2.5 rounded-xl bg-primary hover:bg-primary/85 text-white text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Play size={15} fill="currentColor" /> 播放全部
            </button>
            <button onClick={() => { setPlaybackMode('shuffle'); playQueue(filtered) }} disabled={!filtered.length}
              className="flex min-h-11 shrink-0 items-center gap-2 rounded-xl border border-line bg-panel px-4 text-sm text-muted hover:text-ink disabled:cursor-not-allowed disabled:opacity-40">
              <Shuffle size={15} /> 随机播放
            </button>
          </div>
        </div>
      </div>

      {refreshError && <p role="alert" className="mb-4 text-sm text-danger">曲库刷新失败：{refreshError}</p>}
      {serverOk === false && (
        <div className="flex items-center gap-3 bg-danger/10 border border-danger/30 rounded-2xl px-5 py-4 mb-6 text-sm">
          <AlertTriangle size={17} className="text-danger shrink-0" />
          无法连接服务器。请到「设置」检查服务器地址，或确认服务器已部署。
        </div>
      )}

      {ready.length === 0 && others.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-28 text-center">
          <div className="w-20 h-20 rounded-3xl bg-accent-dim flex items-center justify-center mb-6">
            <Disc3 size={36} className="text-accent" />
          </div>
          <h2 className="text-xl font-medium mb-2">曲库还是空的</h2>
          <p className="text-sm text-muted mb-6 max-w-sm">
            粘贴一个 B 站收藏夹链接，自动解析歌曲名、歌手、歌词并下载音频
          </p>
          <button
            onClick={onGoImport}
            className="flex items-center gap-2 px-5 py-3 rounded-xl bg-primary hover:bg-primary/85 text-white text-sm font-medium transition-colors"
          >
            <Download size={16} /> 导入第一个收藏夹
          </button>
        </div>
      ) : (
        <>
          {artistMessage && <p role="status" className="mb-4 text-xs text-accent">{artistMessage}</p>}
          {layout === 'artists' && artist === 'all' && <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {artists.filter(name => filtered.some(song => song.artist === name)).map(name => {
              const tracks = filtered.filter(song => song.artist === name)
              return <button key={name} onClick={() => setArtist(name)} className="min-w-0 rounded-2xl border border-line bg-panel p-4 text-left hover:border-accent/50">
                {showCovers && <div className="mb-3 h-20 w-20 overflow-hidden rounded-xl"><Cover song={tracks[0]} className="h-full w-full" /></div>}
                <div className="truncate text-sm font-medium">{name}</div><div className="mt-1 text-xs text-muted">{tracks.length} 首</div>
              </button>
            })}
          </div>}
          {layout === 'artists' && artist !== 'all' && <button className="mb-4 min-h-11 text-sm text-accent" onClick={() => setArtist('all')}>← 全部歌手 · {artist}</button>}
          {layout === 'artists' && artist === 'all' ? null : filtered.length > 0 ? (
            <div
              className={
                layout === 'grid'
                  ? 'grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 sm:gap-5'
                  : 'space-y-1.5'
              }
            >
              {filtered.map((song, i) => {
                const isCurrent = current?.id === song.id
                if (layout === 'list') {
                  return (
                    <div
                      key={song.id}
                      className={`library-song-list group relative flex min-h-16 items-center gap-3 rounded-xl border px-3 py-2 transition-colors fade-in-up ${
                        isCurrent ? 'border-accent/50 bg-accent-dim' : 'border-line bg-panel hover:border-line/80 hover:bg-panel2/60'
                      }`}
                      style={{ animationDelay: `${Math.min(i * 16, 220)}ms` }}
                    >
                      <button
                        type="button"
                        onClick={() => play(song)}
                        className="absolute inset-0 z-0 cursor-pointer rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/70"
                        aria-label={`播放 ${song.title}，${song.artist}`}
                      />
                      {showCovers && (
                        <button
                          type="button"
                          onClick={(event) => openArtworkAndLyrics(event, song)}
                          className="relative z-10 h-11 w-11 shrink-0 overflow-hidden rounded-lg bg-panel2 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/70"
                          title="打开封面和歌词"
                          aria-label={`打开 ${song.title} 的封面和歌词`}
                        >
                          <Cover song={song} className="h-full w-full transition-transform duration-200 group-hover:scale-105" />
                          <span className="absolute inset-0 grid place-items-center bg-black/45 opacity-0 transition-opacity hover:opacity-100 focus-visible:opacity-100">
                            <FileText size={15} className="text-white" />
                          </span>
                        </button>
                      )}
                      <div className="pointer-events-none relative z-10 min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium" title={song.title}>{song.title}</span>
                          {isCurrent && (
                            <span className="shrink-0 rounded-full bg-primary px-1.5 py-0.5 text-[9px] font-medium text-white">
                              {playing ? '播放中' : '已暂停'}
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 truncate text-[11px] text-muted" title={song.artist}>
                          {song.artist}
                        </div>
                      </div>
                      {song.album && (
                        <div className="pointer-events-none relative z-10 hidden w-40 truncate text-xs text-faint md:block" title={song.album}>
                          {song.album}
                        </div>
                      )}
                      <div className="pointer-events-none relative z-10 w-10 shrink-0 text-right text-[11px] tabular-nums text-faint">
                        {song.duration ? fmtTime(song.duration) : '—'}
                      </div>
                      <SongActions song={song} onEdit={setEditing} onDelete={setDeleting} onPlaylist={setPlaylistTarget} />
                    </div>
                  )
                }
                return (
                  <div
                    key={song.id}
                    className={`library-song-card group relative hover-lift bg-panel border rounded-2xl overflow-hidden fade-in-up ${
                      isCurrent ? 'border-accent/50' : 'border-line'
                    }`}
                    style={{ animationDelay: `${Math.min(i * 25, 300)}ms` }}
                  >
                    <button
                      type="button"
                      onClick={() => play(song)}
                      className="absolute inset-0 z-0 cursor-pointer rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/70"
                      aria-label={`播放 ${song.title}，${song.artist}`}
                    />
                    {showCovers && (
                      <button
                        type="button"
                        onClick={(event) => openArtworkAndLyrics(event, song)}
                        className="relative z-10 block aspect-square w-full overflow-hidden bg-panel2 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/70"
                        title="打开封面和歌词"
                        aria-label={`打开 ${song.title} 的封面和歌词`}
                      >
                        <Cover song={song} className="h-full w-full transition-transform duration-300 group-hover:scale-105" />
                        <span className="absolute inset-0 grid place-items-center bg-black/0 transition-colors group-hover:bg-black/30">
                          <span className="grid h-12 w-12 translate-y-2 place-items-center rounded-full bg-white/95 text-bg opacity-0 transition-all group-hover:translate-y-0 group-hover:opacity-100 focus-visible:translate-y-0 focus-visible:opacity-100">
                            <FileText size={18} />
                          </span>
                        </span>
                        {isCurrent && (
                          <span className="absolute right-2 top-2 rounded-full bg-primary px-2 py-0.5 text-[10px] font-medium text-white">
                            {playing ? '播放中' : '已暂停'}
                          </span>
                        )}
                      </button>
                    )}
                    <div className={`pointer-events-none relative z-10 flex flex-col p-3.5 ${showCovers ? '' : 'min-h-28 justify-between'}`}>
                      <div>
                        <div className="flex items-center gap-2">
                          <div className="min-w-0 flex-1 truncate text-sm font-medium" title={song.title}>
                            {song.title}
                          </div>
                          {!showCovers && isCurrent && (
                            <span className="shrink-0 rounded-full bg-primary px-1.5 py-0.5 text-[9px] font-medium text-white">
                              {playing ? '播放中' : '已暂停'}
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 truncate text-xs text-muted" title={song.artist}>
                          {song.artist}
                          {song.album ? ` · ${song.album}` : ''}
                          {song.duration ? ` · ${fmtTime(song.duration)}` : ''}
                        </div>
                      </div>
                      <div className="mt-1.5 flex justify-end">
                        <SongActions song={song} onEdit={setEditing} onDelete={setDeleting} onPlaylist={setPlaylistTarget} />
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-sm text-muted py-12 text-center">没有匹配的歌曲</p>
          )}

          {others.length > 0 && (
            <div className="mt-10">
              <h2 className="text-sm font-medium text-muted mb-3">处理中 / 失败的条目</h2>
              <div className="space-y-2">
                {others.map((song) => (
                  <div
                    key={song.id}
                    className="flex items-center gap-3 bg-panel border border-line rounded-xl px-4 py-3"
                  >
                    {showCovers && (
                      <div className="w-10 h-10 rounded-lg overflow-hidden bg-panel2 shrink-0">
                        <Cover song={song} className="w-full h-full" />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="text-sm truncate">{song.title} — {song.artist}</div>
                      <div className="text-[11px] text-faint truncate mt-0.5">{song.raw_title}</div>
                    </div>
                    {song.status === 'downloading' && (
                      <span className="flex items-center gap-1.5 text-xs text-accent">
                        <Loader2 size={13} className="spin" /> 下载中
                      </span>
                    )}
                    {song.status === 'pending' && (
                      <span className="text-xs text-faint">待下载</span>
                    )}
                    {song.status === 'error' && (
                      <span
                        className="text-xs text-danger max-w-[220px] truncate"
                        title={song.error || ''}
                      >
                        <AlertTriangle size={12} className="inline mr-1" />
                        {song.error || '失败'}
                      </span>
                    )}
                    <button
                      onClick={() => setDeleting(song)}
                      className="p-1.5 rounded-lg text-faint hover:text-danger hover:bg-danger/10 transition-colors shrink-0"
                      title="删除该条目"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {editing && (
        <SongEditModal
          song={editing}
          onClose={() => setEditing(null)}
          onSaved={(updated) => {
            setLocalSongs((prev) => prev.map((s) => (s.id === updated.id ? updated : s)))
            setEditing(updated)
            updateSongInfo(updated)
            onRefresh()
          }}
        />
      )}

      {deleting && (
        <DeleteModal
          song={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            setLocalSongs((prev) => prev.filter((s) => s.id !== deleting.id))
            onRefresh()
          }}
        />
      )}

      {managingPlaylists && (
        <PlaylistManager
          playlists={playlists}
          onClose={() => setManagingPlaylists(false)}
          onChanged={refreshPlaylistContext}
        />
      )}

      {playlistTarget && (
        <AddToPlaylistModal
          song={playlistTarget}
          playlists={playlists}
          activePlaylistId={selectedPlaylist === 'all' ? null : selectedPlaylist}
          onClose={() => setPlaylistTarget(null)}
          onChanged={refreshPlaylistContext}
        />
      )}
    </div>
  )
}
