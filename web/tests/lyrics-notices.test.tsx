// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import LyricsFailures from '../src/components/LyricsFailures'
import { api } from '../src/lib/api'
import type { LyricsFailure, Song } from '../src/types'
vi.mock('../src/lib/api', () => ({ api: { lyricsFailures: vi.fn(), retryLyrics: vi.fn(), dismissLyricsFailure: vi.fn(), uploadLyrics: vi.fn() } }))
const failure: LyricsFailure = { song_id: 5, title: '缺词歌曲', artist: '歌手', error: '暂未匹配到歌词', first_failed_at: '2026-10-11T02:00:00Z', last_attempt_at: '2026-10-11T02:00:00Z', next_retry_at: '2026-10-11T02:05:00Z', status: 'error', dismissed: false, attempts: 1 }
beforeEach(() => {
 vi.mocked(api.lyricsFailures).mockResolvedValue([failure])
 vi.mocked(api.retryLyrics).mockResolvedValue({started:true,queued:1,message:'已入队'})
 vi.mocked(api.dismissLyricsFailure).mockResolvedValue({ok:true})
})
afterEach(() => { cleanup();vi.resetAllMocks();vi.useRealTimers() })
it('retains successful retry notice until explicitly skipped', async () => {
 vi.useFakeTimers();render(<LyricsFailures onChanged={vi.fn()} />);await act(async()=>{})
 expect(screen.getByText('暂未匹配到歌词')).toBeTruthy()
 expect(screen.getByRole('button',{name:'上传 缺词歌曲 的歌词'})).toBeTruthy()
 vi.mocked(api.lyricsFailures).mockResolvedValue([{...failure,status:'ready'}])
 await act(async()=>{await vi.advanceTimersByTimeAsync(75_000)})
 expect(screen.getByText('缺词歌曲 · 重试成功')).toBeTruthy()
 vi.mocked(api.lyricsFailures).mockResolvedValue([])
 await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'关闭 缺词歌曲 的歌词通知'}))})
 expect(api.dismissLyricsFailure).toHaveBeenCalledWith(5)
 expect(screen.queryByLabelText('歌词匹配通知')).toBeNull()
})
it('uploads chosen lyrics and refreshes the song without redownloading audio', async () => {
 const changed=vi.fn(); const {container}=render(<LyricsFailures onChanged={changed} />)
 fireEvent.click(await screen.findByRole('button',{name:'上传 缺词歌曲 的歌词'}))
 const file=new File(['[00:01]示例歌词'],'song.lrc',{type:'text/plain'})
 vi.mocked(api.uploadLyrics).mockResolvedValue({id:5,lyrics_source:'upload'} as Song)
 vi.mocked(api.lyricsFailures).mockResolvedValue([{...failure,status:'ready'}])
 await act(async()=>{fireEvent.change(container.querySelector('input[type=file]')!,{target:{files:[file]}})})
 expect(api.uploadLyrics).toHaveBeenCalledWith(5,file)
 expect(changed).toHaveBeenCalled()
 expect(screen.getByText('缺词歌曲 · 重试成功')).toBeTruthy()
})
