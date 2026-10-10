// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PartSelectionPanel from '../src/components/PartSelectionPanel'
import SongEditModal from '../src/components/SongEditModal'
import { api } from '../src/lib/api'
import type { SavedCollection, Song } from '../src/types'

vi.mock('../src/lib/api', () => ({ api: {
  selectCollectionParts: vi.fn(), refreshCover: vi.fn(), updateSong: vi.fn(), coverUrl: vi.fn(() => ''), videoUrl: vi.fn(() => ''),
} }))
const collection = { id: 9, title: '旧收藏夹', multipart_policy: 'ask' } as SavedCollection
const tracks = [1, 2, 3].map(page => ({ id: page, bvid: 'BVOLD', title: `歌曲${page}`, artist: '演唱者', album: '收藏夹',
  part_index: page, part_title: `分P标题${page}`, duration: 180, raw_title: '三个分P的视频', download_requested: null,
  file_path: page === 2 ? 'old.m4a' : null, parts: [{ cid: 1 }, { cid: 2 }, { cid: 3 }],
}) as Song)
beforeEach(() => { vi.mocked(api.selectCollectionParts).mockResolvedValue({ queued: 1 } as never) })
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('lists every legacy P and submits exact IDs for a partial selection', async () => {
  render(<PartSelectionPanel collection={collection} songs={[...tracks, { ...tracks[0], id: 4, bvid: 'BVSINGLE', parts: null }]} onSaved={vi.fn()} />)
  for (const song of tracks) expect(screen.getByRole('checkbox', { name: `选择 P${song.part_index} ${song.part_title}` })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '下载全部' }))
  fireEvent.click(screen.getByRole('checkbox', { name: '选择 P1 分P标题1' }))
  fireEvent.click(screen.getByRole('checkbox', { name: '选择 P3 分P标题3' }))
  fireEvent.click(screen.getByRole('button', { name: '保存选择并下载 1 首' }))
  await waitFor(() => expect(api.selectCollectionParts).toHaveBeenCalledWith(9, [2], ['BVOLD']))
})
it('supports first P and explicitly skipping all P', async () => {
  render(<PartSelectionPanel collection={collection} songs={[...tracks, { ...tracks[0], id: 4, bvid: 'BVSINGLE', parts: null }]} onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '下载全部' }))
  fireEvent.click(screen.getByRole('button', { name: '只下载首P' }))
  expect(screen.getByRole('button', { name: '保存选择并下载 1 首' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '取消全选' }))
  fireEvent.click(screen.getByRole('button', { name: '保存选择，暂不下载' }))
  await waitFor(() => expect(api.selectCollectionParts).toHaveBeenCalledWith(9, [], ['BVOLD']))
})
it('keeps every video expanded and applies shortcuts to only that video', async () => {
  const second = tracks.slice(0, 2).map(song => ({ ...song, id: song.id + 10, bvid: 'BVSECOND', raw_title: '第二个多P视频', part_title: `第二组${song.part_index}` }))
  render(<PartSelectionPanel collection={collection} songs={[...tracks, ...second]} onSaved={vi.fn()} />)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getAllByRole('checkbox')).toHaveLength(5)
  fireEvent.click(screen.getByRole('button', { name: '取消全选' }))
  fireEvent.click(screen.getByRole('button', { name: '此视频下载全部：三个分P的视频' }))
  fireEvent.click(screen.getByRole('button', { name: '此视频只下载首P：第二个多P视频' }))
  expect(screen.getByRole('button', { name: '保存选择并下载 4 首' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '此视频取消全选：三个分P的视频' }))
  fireEvent.click(screen.getByRole('button', { name: '保存选择并下载 1 首' }))
  await waitFor(() => expect(api.selectCollectionParts).toHaveBeenCalledWith(9, [11], ['BVOLD', 'BVSECOND']))
})
it('refreshing a cover retains unsaved names and then saves metadata', async () => {
  vi.mocked(api.refreshCover).mockResolvedValue({ ...tracks[0], cover_url: 'new.jpg' })
  vi.mocked(api.updateSong).mockResolvedValue({ ...tracks[0], title: '新歌名', artist: '新歌手' })
  render(<SongEditModal song={tracks[0]} onClose={vi.fn()} onSaved={vi.fn()} />)
  fireEvent.change(screen.getByDisplayValue('歌曲1'), { target: { value: '新歌名' } })
  fireEvent.change(screen.getByDisplayValue('演唱者'), { target: { value: '新歌手' } })
  fireEvent.click(screen.getByRole('button', { name: '重新下载歌曲封面' }))
  await screen.findByText('封面已更新，客户端扫描并刷新后可见')
  expect(screen.getByDisplayValue('新歌名')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(api.updateSong).toHaveBeenCalledWith(1, { title: '新歌名', artist: '新歌手', album: '收藏夹' }))
})
