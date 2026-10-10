// @vitest-environment jsdom
import { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PlayerProvider } from '../src/hooks/usePlayer'
import { PlayerContext, usePlayer } from '../src/hooks/playerContext'
import PlayerBar from '../src/components/PlayerBar'
import { api } from '../src/lib/api'
import type { Song } from '../src/types'

class FakeAudio extends EventTarget {
  static latest: FakeAudio
  constructor() { super(); FakeAudio.latest = this }
  private source = ''
  private position = 0
  set src(value: string) { this.source = value; this.position = 0; this.ended = false; this.duration = NaN; this.error = null }
  get src() { return this.source }
  set currentTime(value: number) { this.position = value; this.ended = false }
  get currentTime() { return this.position }
  paused = true
  ended = false
  duration = NaN
  readyState = 4
  error: unknown = null
  volume = .8
  playbackRate = 1
  preload = ''
  play = vi.fn(() => { this.paused = false; this.ended = false; this.emit('play'); return Promise.resolve() })
  pause() { this.paused = true; this.emit('pause') }
  load = vi.fn(() => { this.error = null; this.ended = false })
  emit(event: string) { this.dispatchEvent(new Event(event)) }
  finish(time = 300) { this.duration = 300; this.position = time; this.ended = true; this.paused = true; this.emit('ended') }
}
const songs = Array.from({ length: 6 }, (_, id) => ({ id: id + 1, title: `Song ${id}`, artist: 'Artist', duration: 300 }) as Song)
let handlers: Record<string, MediaSessionActionHandler | null>
let session: { setActionHandler: ReturnType<typeof vi.fn>; setPositionState: ReturnType<typeof vi.fn>; metadata: unknown; playbackState: string }
beforeEach(() => {
  localStorage.clear()
  handlers = {}
  session = { setActionHandler: vi.fn((action, handler) => { handlers[action] = handler }), setPositionState: vi.fn(), metadata: null, playbackState: 'none' }
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal('MediaMetadata', class { constructor(data: object) { Object.assign(this, data) } })
  Object.defineProperty(navigator, 'mediaSession', { configurable: true, value: session })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
function setup() {
  return renderHook(usePlayer, { wrapper: ({ children }) => <StrictMode><PlayerProvider>{children}</PlayerProvider></StrictMode> })
}
function action(name: string) { handlers[name]?.({ action: name } as MediaSessionActionDetails) }

describe('background playback and media session', () => {
  it('updates current and queued metadata without restarting playback or changing position', () => {
    const { result } = setup()
    act(() => result.current.playQueue(songs))
    const audio = FakeAudio.latest
    audio.currentTime = 75
    const plays = audio.play.mock.calls.length
    const source = audio.src
    act(() => result.current.updateSongInfo({ ...songs[0], title: '新歌名', artist: '新歌手' }))
    expect(result.current.current?.title).toBe('新歌名')
    expect(session.metadata).toMatchObject({ title: '新歌名', artist: '新歌手' })
    expect(audio.currentTime).toBe(75)
    expect(audio.src).toBe(source)
    expect(audio.play.mock.calls.length).toBe(plays)
    act(() => result.current.updateSongInfo({ ...songs[1], title: '队列新歌名' }))
    act(() => action('nexttrack'))
    expect(result.current.current?.title).toBe('队列新歌名')
  })

  it('advances multiple lock-screen commands before React has rendered, keeping handlers and metadata', () => {
    const { result } = setup()
    act(() => result.current.playQueue(songs))
    const originalNext = handlers.nexttrack
    const callStart = session.setActionHandler.mock.calls.length
    act(() => { action('nexttrack'); action('nexttrack'); action('nexttrack') })
    expect(result.current.current?.id).toBe(4)
    expect(session.metadata).toMatchObject({ title: songs[3].title })
    FakeAudio.latest.currentTime = 45
    act(() => action('previoustrack'))
    expect(result.current.current?.id).toBe(3)
    expect(handlers.nexttrack).toBe(originalNext)
    expect(session.setActionHandler.mock.calls.slice(callStart).filter(([action, handler]) =>
      (action === 'nexttrack' || action === 'previoustrack') && handler === null)).toHaveLength(0)
    expect(handlers.seekforward).toBeNull()
    expect(handlers.seekbackward).toBeNull()
    act(() => action('pause'))
    expect(session.playbackState).toBe('paused')
    act(() => action('play'))
    expect(session.playbackState).toBe('playing')
  })
  it('restores WebKit native command loss after source load, playback resume and pageshow', () => {
    const { result } = setup()
    act(() => result.current.playQueue(songs))
    const originalNext = handlers.nexttrack
    for (const event of ['loadedmetadata', 'playing', 'play']) {
      handlers = {}
      act(() => FakeAudio.latest.emit(event))
      expect(handlers.nexttrack).toBe(originalNext)
      expect(handlers.previoustrack).toBeTypeOf('function')
      expect(handlers.seekforward).toBeNull()
      expect(handlers.seekbackward).toBeNull()
    }
    handlers = {}
    act(() => window.dispatchEvent(new Event('pageshow')))
    expect(handlers.nexttrack).toBe(originalNext)
    act(() => action('nexttrack'))
    expect(result.current.current?.id).toBe(2)
    act(() => action('previoustrack'))
    expect(result.current.current?.id).toBe(1)
    act(() => result.current.restoreSystemControls())
    expect(handlers.nexttrack).toBe(originalNext)
  })
  it('reasserts controls without seeking or skipping when visibility changes', () => {
    const { result } = setup()
    act(() => result.current.playQueue(songs))
    FakeAudio.latest.currentTime = 60
    handlers = {}
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    expect(handlers.nexttrack).toBeTypeOf('function')
    expect(FakeAudio.latest.currentTime).toBe(60)
    expect(result.current.current?.id).toBe(1)
  })
  it('does not skip on 40-second premature ended, errors, stalls or late events', () => {
    const { result } = setup()
    act(() => result.current.playQueue(songs))
    act(() => FakeAudio.latest.finish(40))
    expect(result.current.current?.id).toBe(1)
    expect(result.current.playbackError).toContain('意外中断')
    act(() => result.current.toggle())
    expect(FakeAudio.latest.load).toHaveBeenCalledOnce()
    act(() => { FakeAudio.latest.emit('loadedmetadata'); FakeAudio.latest.emit('ended'); FakeAudio.latest.emit('stalled') })
    expect(result.current.current?.id).toBe(1)
    expect(FakeAudio.latest.currentTime).toBe(40)
    act(() => FakeAudio.latest.emit('error'))
    expect(result.current.current?.id).toBe(1)
  })
  it('finishes each shuffle cycle once and avoids repetition at the boundary', () => {
    const { result } = setup()
    act(() => { result.current.setPlaybackMode('shuffle'); result.current.playQueue(songs) })
    const visited = [result.current.current!.id]
    for (let i = 1; i < songs.length; i++) {
      act(() => FakeAudio.latest.finish())
      visited.push(result.current.current!.id)
    }
    expect(new Set(visited).size).toBe(songs.length)
    act(() => FakeAudio.latest.finish())
    expect(result.current.current!.id).not.toBe(visited.at(-1))
  })
  it('changes mode without restarting audio and supports repeat-one/manual next', () => {
    const { result } = setup()
    act(() => result.current.playQueue(songs))
    FakeAudio.latest.currentTime = 40
    act(() => result.current.setPlaybackMode('shuffle'))
    expect(FakeAudio.latest.currentTime).toBe(40)
    act(() => result.current.setPlaybackMode('one'))
    act(() => FakeAudio.latest.finish())
    expect(result.current.current?.id).toBe(1)
    expect(FakeAudio.latest.currentTime).toBe(0)
    act(() => action('nexttrack'))
    expect(result.current.current?.id).toBe(2)
  })
  it('does not let a rejected old play promise pause a newer song', async () => {
    const { result } = setup()
    let reject!: (error: Error) => void
    FakeAudio.latest.play.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
    act(() => { result.current.playQueue(songs); result.current.next() })
    await act(async () => { reject(new Error('aborted old src')); await Promise.resolve() })
    expect(result.current.current?.id).toBe(2)
    expect(result.current.playing).toBe(true)
    expect(result.current.playbackError).toBeNull()
  })
  it('does not report an intentional pause as a playback failure', async () => {
    const { result } = setup()
    let reject!: (error: Error) => void
    FakeAudio.latest.play.mockImplementationOnce(() => {
      FakeAudio.latest.paused = false
      FakeAudio.latest.emit('play')
      return new Promise((_, fail) => { reject = fail })
    })
    act(() => result.current.playQueue(songs))
    act(() => action('pause'))
    await act(async () => { reject(new DOMException('interrupted by pause', 'AbortError')) })
    expect(result.current.playing).toBe(false)
    expect(result.current.playbackError).toBeNull()
  })
  it('invalidates an old play attempt when resuming the same song', async () => {
    const { result } = setup()
    let reject!: (error: Error) => void
    FakeAudio.latest.play.mockImplementationOnce(() => {
      FakeAudio.latest.paused = false
      return new Promise((_, fail) => { reject = fail })
    })
    act(() => result.current.playQueue(songs))
    act(() => { action('pause'); action('play') })
    await act(async () => { reject(new DOMException('old attempt', 'AbortError')) })
    expect(result.current.current?.id).toBe(1)
    expect(result.current.playing).toBe(true)
    expect(result.current.playbackError).toBeNull()
  })
  it('ignores delayed pause and error events after a source change, and play after pause', () => {
    const { result } = setup()
    act(() => { result.current.playQueue(songs); result.current.next() })
    act(() => { FakeAudio.latest.emit('pause'); FakeAudio.latest.emit('error') })
    expect(result.current.current?.id).toBe(2)
    expect(result.current.playing).toBe(true)
    expect(result.current.playbackError).toBeNull()
    act(() => { result.current.toggle(); FakeAudio.latest.emit('play'); FakeAudio.latest.emit('playing') })
    expect(result.current.playing).toBe(false)
    expect(session.playbackState).toBe('paused')
  })
  it('retries a network error immediately and preserves progress across repeated reloads', () => {
    const { result } = setup()
    act(() => result.current.playQueue(songs))
    const audio = FakeAudio.latest
    audio.currentTime = 72
    audio.error = { code: 2 }
    act(() => audio.emit('error'))
    expect(result.current.playing).toBe(false)
    expect(result.current.playbackError).toContain('音频加载失败')
    audio.load.mockImplementation(() => { audio.currentTime = 0; audio.duration = NaN; audio.error = null })
    const previousCalls = audio.play.mock.calls.length
    act(() => { result.current.retry(); result.current.retry() })
    expect(audio.play.mock.calls.length).toBe(previousCalls + 2)
    audio.duration = 300
    act(() => audio.emit('loadedmetadata'))
    expect(audio.currentTime).toBe(72)
    expect(result.current.time).toBe(72)
    expect(result.current.playbackError).toBeNull()
    act(() => { result.current.next(); audio.duration = 300; audio.emit('loadedmetadata') })
    expect(audio.currentTime).toBe(0)
  })
  it('shows actual buffering, clears it on recovery/pause, and does not skip on a stall', () => {
    const { result } = setup()
    act(() => result.current.playQueue(songs))
    const audio = FakeAudio.latest
    act(() => audio.emit('stalled'))
    expect(result.current.buffering).toBe(false)
    audio.readyState = 2
    act(() => audio.emit('waiting'))
    expect(result.current.buffering).toBe(true)
    act(() => audio.emit('playing'))
    expect(result.current.buffering).toBe(true)
    expect(result.current.current?.id).toBe(1)
    audio.readyState = 4
    act(() => audio.emit('playing'))
    expect(result.current.buffering).toBe(false)
    audio.readyState = 2
    act(() => { audio.emit('stalled'); result.current.toggle() })
    expect(result.current.buffering).toBe(false)
    expect(result.current.playing).toBe(false)
  })
  it('plays when an optional Media Session metadata implementation throws', () => {
    const { result } = setup()
    vi.stubGlobal('MediaMetadata', class { constructor() { throw new TypeError('unsupported artwork') } })
    act(() => result.current.playQueue(songs))
    expect(result.current.playing).toBe(true)
    expect(result.current.current?.id).toBe(1)
  })
  it('uses the explicit retry action from the error banner and restores page scroll on close', async () => {
    vi.spyOn(api, 'song').mockResolvedValue(songs[0])
    const { result } = setup()
    const retry = vi.fn()
    const toggle = vi.fn()
    const setExpanded = vi.fn()
    const view = render(<PlayerContext.Provider value={{ ...result.current, current: songs[0],
      playbackError: '音频加载失败', expanded: true, retry, toggle, setExpanded }}>
      <PlayerBar />
    </PlayerContext.Provider>)
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(retry).toHaveBeenCalledOnce()
    expect(toggle).not.toHaveBeenCalled()
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(setExpanded).toHaveBeenCalledWith(false)
    await act(async () => { await Promise.resolve() })
    view.unmount()
    expect(document.body.style.overflow).toBe('')
  })
  it('clamps queue indices and seek values and includes explicitly selected songs', () => {
    const { result } = setup()
    act(() => result.current.playQueue(songs, 999))
    expect(result.current.current?.id).toBe(6)
    FakeAudio.latest.duration = 300
    act(() => { result.current.seek(Infinity); result.current.seek(-5) })
    expect(FakeAudio.latest.currentTime).toBe(0)
    act(() => result.current.seek(500))
    expect(FakeAudio.latest.currentTime).toBe(300)
    const other = { ...songs[0], id: 20 }
    act(() => result.current.playSong(other))
    expect(result.current.queue.map((song) => song.id)).toContain(20)
  })
})
