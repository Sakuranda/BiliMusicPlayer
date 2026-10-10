import { createContext, useContext } from 'react'
import type { Song } from '../types'

export type PlaybackMode = 'repeat' | 'shuffle' | 'one'

export interface PlayerState {
  updateSongInfo: (song: Song) => void
  current: Song | null
  queue: Song[]
  playing: boolean
  buffering: boolean
  playbackError: string | null
  retry: () => void
  restoreSystemControls: () => void
  time: number
  duration: number
  volume: number
  expanded: boolean
  lyricsVisible: boolean
  setLyricsVisible: (visible: boolean) => void
  playbackMode: PlaybackMode
  playSong: (song: Song, queue?: Song[]) => void
  toggle: () => void
  next: () => void
  prev: () => void
  seek: (time: number) => void
  setVolume: (volume: number) => void
  setExpanded: (expanded: boolean) => void
  setPlaybackMode: (mode: PlaybackMode) => void
  playQueue: (songs: Song[], index?: number) => void
}

export const PlayerContext = createContext<PlayerState | null>(null)

export function usePlayer(): PlayerState {
  const context = useContext(PlayerContext)
  if (!context) throw new Error('usePlayer must be used within PlayerProvider')
  return context
}
