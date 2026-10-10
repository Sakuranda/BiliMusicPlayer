// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ImportView from '../src/components/ImportView'
import { api } from '../src/lib/api'

vi.mock('../src/lib/api', () => ({ api: {
  collections: vi.fn(), collectionParts: vi.fn(), createJob: vi.fn(), deleteJob: vi.fn(), coverUrl: vi.fn(() => ''),
} }))
const example = {
  job: { id: 'preview', title: '测试收藏夹', status: 'parsed' },
  songs: [
    { id: 1, bvid: 'BV1', title: '烟花', artist: '演唱者', raw_title: '《烟花》', tags: [], metadata: {needs_review: false} },
    { id: 2, bvid: 'BV2', title: '空心', artist: '未知歌手', raw_title: '一句歌词', tags: [], metadata: {needs_review: true, reasons: ['没有足够演唱证据']} },
  ],
}
async function preview() {
  render(<ImportView onImported={vi.fn()} onViewLibrary={vi.fn()} />)
  fireEvent.change(screen.getByPlaceholderText(/space.bilibili.com/), { target: { value: 'https://space.bilibili.com/1/favlist?fid=2' } })
  fireEvent.click(screen.getByRole('button', {name: '解析收藏夹'}))
  await screen.findByRole('heading', {name: '确认歌曲信息'})
}
beforeEach(() => {
  localStorage.clear()
  vi.mocked(api.collections).mockResolvedValue([])
  vi.mocked(api.createJob).mockResolvedValue(example as never)
})
afterEach(() => { cleanup(); vi.resetAllMocks() })

describe('import review', () => {
  it('automatically expands saved multi-P videos without opening a dialog', async () => {
    vi.mocked(api.collections).mockResolvedValue([{ id: 9, title: '旧多P收藏夹', multipart_count: 1, pending_multipart_count: 1, song_count: 2 }] as never)
    vi.mocked(api.collectionParts).mockResolvedValue([1, 2].map(page => ({ id: page, bvid: 'BVOLD', part_index: page, part_title: `旧分P${page}`, raw_title: '旧视频', parts: [{ cid: 1 }, { cid: 2 }] })) as never)
    render(<ImportView onImported={vi.fn()} onViewLibrary={vi.fn()} />)
    await screen.findByRole('region', { name: '旧多P收藏夹 多P歌曲列表' })
    expect(screen.getAllByRole('checkbox', { name: /选择 P/ })).toHaveLength(2)
    expect(screen.getByRole('button', { name: '此视频只下载首P：旧视频' })).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('filters uncertain metadata without silently changing the download selection', async () => {
    await preview()
    fireEvent.click(screen.getByRole('checkbox', {name: /仅看待核对/}))
    expect(screen.queryByDisplayValue('烟花')).toBeNull()
    expect(screen.getByDisplayValue('空心')).toBeTruthy()
    expect(screen.getByRole('button', {name: '下载选中的 2 首'})).toBeTruthy()
    expect(screen.getByText('没有足够演唱证据')).toBeTruthy()
  })
  it('keeps the preview and displays an error when cancellation fails', async () => {
    vi.mocked(api.deleteJob).mockRejectedValue(new Error('任务正在下载'))
    await preview()
    fireEvent.click(screen.getByRole('button', {name: '取消导入'}))
    await waitFor(() => expect(screen.getByText('任务正在下载')).toBeTruthy())
    expect(screen.getByRole('heading', {name: '确认歌曲信息'})).toBeTruthy()
  })
})
