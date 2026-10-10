// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import LibraryView from '../src/components/LibraryView'
import { PlayerContext, type PlayerState } from '../src/hooks/playerContext'
import { api } from '../src/lib/api'
import type { Song } from '../src/types'

vi.mock('../src/lib/api', () => ({ api: {
  playlists: vi.fn(), collections: vi.fn(), collectionSongs: vi.fn(), playlistSongs: vi.fn(), coverUrl: vi.fn(() => ''),
} }))
const tracks = [
  { id: 1, title: '晨歌', artist: '甲', created_at: '2026-10-01', status: 'ready' },
  { id: 2, title: '晚歌', artist: '甲', created_at: '2026-10-03', status: 'ready' },
  { id: 3, title: '其他', artist: '乙', created_at: '2026-10-02', status: 'ready' },
] as Song[]
let player: PlayerState
beforeEach(() => {
  localStorage.clear()
  vi.mocked(api.playlists).mockResolvedValue([])
  vi.mocked(api.collections).mockResolvedValue([])
  player = { current: null, playing: false, lyricsVisible: true,
    playQueue: vi.fn(), playSong: vi.fn(), setPlaybackMode: vi.fn(), setLyricsVisible: vi.fn(),
  } as unknown as PlayerState
})
afterEach(() => { cleanup(); vi.resetAllMocks() })
const show = (refresh = vi.fn()) => render(<PlayerContext.Provider value={player}>
  <LibraryView songs={tracks} serverOk onRefresh={refresh} onGoImport={vi.fn()} />
</PlayerContext.Provider>)

it('uses the same filtered, time-sorted queue for ordinary and random playback', async () => {
  show()
  await waitFor(() => expect(api.collections).toHaveBeenCalled())
  fireEvent.change(screen.getByRole('textbox', { name: '搜索歌曲或歌手' }), { target: { value: '甲' } })
  fireEvent.click(screen.getByRole('button', { name: '播放全部' }))
  expect(player.setPlaybackMode).toHaveBeenLastCalledWith('repeat')
  expect(player.playQueue).toHaveBeenLastCalledWith([tracks[1], tracks[0]])
  fireEvent.change(screen.getByRole('combobox', { name: '歌曲排序' }), { target: { value: 'added-asc' } })
  fireEvent.click(screen.getByRole('button', { name: '随机播放' }))
  expect(player.setPlaybackMode).toHaveBeenLastCalledWith('shuffle')
  expect(player.playQueue).toHaveBeenLastCalledWith([tracks[0], tracks[1]])
  expect(localStorage.getItem('mp_library_sort')).toBe('added-asc')
  fireEvent.click(screen.getByRole('button', { name: '播放 晨歌，甲' }))
  expect(player.playSong).toHaveBeenLastCalledWith(tracks[0], [tracks[0], tracks[1]])
})

it('persists view and cover preferences and updates shared lyric visibility', async () => {
  const view = show()
  await waitFor(() => expect(api.collections).toHaveBeenCalled())
  fireEvent.click(screen.getByRole('button', { name: '列表' }))
  fireEvent.click(screen.getByRole('switch', { name: '显示歌曲封面' }))
  fireEvent.click(screen.getByRole('switch', { name: '显示歌词' }))
  expect(player.setLyricsVisible).toHaveBeenCalledWith(false)
  expect(localStorage.getItem('mp_library_layout')).toBe('list')
  expect(localStorage.getItem('mp_library_covers')).toBe('false')
  view.unmount(); show()
  expect(screen.getByRole('button', { name: '列表' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByRole('switch', { name: '显示歌曲封面' }).getAttribute('aria-checked')).toBe('false')
})

it('refreshes library and relationships and clears a removed collection filter', async () => {
  vi.mocked(api.collections).mockResolvedValueOnce([{ id: 8, title: '收藏', song_count: 1 }] as never)
  vi.mocked(api.collectionSongs).mockResolvedValue([tracks[0]])
  const refresh = vi.fn()
  show(refresh)
  await screen.findByRole('option', { name: '收藏 · 1' })
  fireEvent.change(screen.getByRole('combobox', { name: '按收藏夹筛选' }), { target: { value: '8' } })
  await waitFor(() => expect(api.collectionSongs).toHaveBeenCalledWith(8))
  fireEvent.click(screen.getByRole('button', { name: '刷新曲库' }))
  await waitFor(() => expect(screen.getByRole('combobox', { name: '按收藏夹筛选' })).toHaveProperty('value', 'all'))
  expect(refresh).toHaveBeenCalledOnce()
  expect(api.collections).toHaveBeenCalledTimes(2)
})

it('reports a failed refresh and allows another attempt without changing the queue', async () => {
  const refresh = vi.fn().mockRejectedValueOnce(new Error('暂时离线')).mockResolvedValueOnce(undefined)
  show(refresh)
  await waitFor(() => expect(api.collections).toHaveBeenCalled())
  fireEvent.click(screen.getByRole('button', { name: '刷新曲库' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toContain('暂时离线')
  fireEvent.click(screen.getByRole('button', { name: '刷新曲库' }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  expect(refresh).toHaveBeenCalledTimes(2)
  expect(player.playQueue).not.toHaveBeenCalled()
})
