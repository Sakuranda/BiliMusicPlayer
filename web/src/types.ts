export interface MetadataSuggestion {
  title: string
  artist: string
  title_source: string
  artist_source: string
  title_confidence: number
  artist_confidence: number
  needs_review: boolean
  reasons: string[]
  warnings?: string[]
}

export interface Part {
  cid: number
  page: number
  part: string
  duration: number
}

export type MultipartPolicy = 'ask' | 'first' | 'all'

export interface Song {
  download_requested?: number | null
  id: number
  bvid: string
  job_id: string
  title: string
  artist: string
  album: string
  duration: number | null
  cid: number | null
  part_index: number | null
  part_title: string | null
  parts: Part[] | null
  source_url: string | null
  downloaded_cid: number | null
  raw_title: string
  uploader: string
  tags: string[]
  description?: string | null
  metadata?: MetadataSuggestion | null
  cover_url: string | null
  cover: string | null
  file_path: string | null
  lyrics: string | null
  lrc: string | null
  lyrics_source: string | null
  lyrics_enabled: boolean
  status: 'pending' | 'downloading' | 'ready' | 'error'
  error: string | null
  created_at: string
}

export interface Job {
  id: string
  url: string
  media_id: string
  title: string
  status: 'parsed' | 'downloading' | 'done' | 'error'
  total: number
  done: number
  failed: number
  message: string | null
  collection_id: number | null
  created_at: string
}

export interface JobDetail {
  job: Job
  songs: Song[]
}

export interface Playlist {
  id: number
  name: string
  song_count: number
  created_at: string
}

export interface SavedCollection {
  multipart_policy: MultipartPolicy
  pending_multipart_count: number
  multipart_count?: number
  id: number
  media_id: string
  url: string
  title: string
  album: string | null
  auto_update: boolean
  fetch_lyrics: boolean
  song_count: number
  downloaded_count: number
  last_checked_at: string | null
  last_updated_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface CollectionRefreshResult {
  pending_multipart_count?: number
  job_id: string
  started: boolean
  queued: number
  concurrency: number
  message: string
}

export interface AuthStatus {
  two_factor_enabled?: boolean
  navidrome_url?: string | null
  enabled: boolean
  authenticated: boolean
  username: string | null
}

export interface CaptchaChallenge {
  id: string
  image: string
  expires_in: number
}

export interface AccessEntry {
  id: string
  username: string
  ip: string
  country: string | null
  region: string | null
  city: string | null
  user_agent: string | null
  login_at: string
  last_seen: string
  request_count: number
}

export interface AccessAudit {
  successful_sessions: number
  unique_ip_count: number
  latest_access: string | null
  entries: AccessEntry[]
}

export interface LyricsFailure {
  song_id: number
  title: string
  artist: string
  error: string
  first_failed_at: string
  last_attempt_at: string
  next_retry_at: string | null
  status: 'error' | 'queued' | 'downloading' | 'ready'
  attempts: number
  dismissed: boolean
}
