import type {
  AccessAudit,
  AuthStatus,
  CaptchaChallenge,
  CollectionRefreshResult,
  Job,
  JobDetail,
  MetadataSuggestion,
  MultipartPolicy,
  Playlist,
  SavedCollection,
  Song,
} from '../types'

const LS_BASE = 'mp_api_base'
const LS_TOKEN = 'mp_api_token'

export function getBase(): string {
  return localStorage.getItem(LS_BASE) || ''
}

export function setBase(v: string) {
  if (v) localStorage.setItem(LS_BASE, v)
  else localStorage.removeItem(LS_BASE)
}

export function getToken(): string {
  return localStorage.getItem(LS_TOKEN) || ''
}

export function setToken(v: string) {
  if (v) localStorage.setItem(LS_TOKEN, v)
  else localStorage.removeItem(LS_TOKEN)
}

async function req<T>(path: string, opts: RequestInit = {}, retried = false): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) }
  if (token) headers['X-Api-Token'] = token
  if (typeof opts.body === 'string') headers['Content-Type'] = 'application/json'
  let res: Response
  try {
    res = await fetch(getBase() + path, { ...opts, headers, credentials: 'include' })
  } catch {
    throw new Error('无法连接服务器，请检查设置中的服务器地址')
  }
  if (!res.ok) {
    // 只自动重试只读请求。解析收藏夹等 POST 若响应在网关处丢失，盲目重试
    // 可能在服务器上重复创建任务。
    const method = (opts.method || 'GET').toUpperCase()
    const retryable = method === 'GET' || method === 'HEAD'
    if (!retried && retryable && (res.status === 502 || res.status === 503 || res.status === 504)) {
      await new Promise((r) => setTimeout(r, 1500))
      return req<T>(path, opts, true)
    }
    let msg = `请求失败 (${res.status})`
    try {
      const data = await res.json()
      if (data.detail) msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

function withToken(q: string): string {
  const token = getToken()
  return token ? q + (q.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}` : q
}

export const api = {
  health: () => req<{ ok: boolean }>('/api/health'),
  authStatus: () => req<AuthStatus>('/api/auth/status'),
  captcha: () => req<CaptchaChallenge>('/api/auth/captcha'),
  login: (username: string, password: string, captchaId: string, captcha: string, totpCode = '') =>
    req<{ authenticated: boolean; username: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, captcha_id: captchaId, captcha, totp_code: totpCode }),
    }),
  logout: () => req<{ logged_out: boolean }>('/api/auth/logout', { method: 'POST' }),
  accessAudit: () => req<AccessAudit>('/api/auth/access-audit'),
  accountSettings: () => req<{ username: string; two_factor_enabled: boolean }>('/api/auth/settings'),
  changeSecurity: (action: 'account' | 'setup' | 'enable' | 'disable', body: { current_password: string; code: string; username?: string; new_password?: string }) =>
    req<{ username: string; two_factor_enabled: boolean; secret?: string; qr_image?: string }>(action === 'account' ? '/api/auth/settings' : `/api/auth/2fa/${action}`, {
      method: action === 'account' ? 'PUT' : 'POST', body: JSON.stringify(body),
    }),

  createJob: (
    url: string,
    cookie?: string,
    album?: string,
    saveCollection = false,
    autoUpdate = false,
    multipartPolicy: MultipartPolicy = 'ask',
  ) =>
    req<JobDetail>('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({
        url,
        cookie: cookie || undefined,
        album: album || undefined,
        save_collection: saveCollection,
        auto_update: autoUpdate && saveCollection,
        multipart_policy: multipartPolicy,
      }),
    }),
  getJob: (id: string) => req<JobDetail>(`/api/jobs/${id}`),
  listJobs: () => req<Job[]>('/api/jobs'),
  deleteJob: (id: string) => req<{ deleted: boolean }>(`/api/jobs/${id}`, { method: 'DELETE' }),
  startJob: (id: string, songIds: number[], fetchLyrics = true) =>
    req<{ started: boolean; queued: number; concurrency: number; message: string }>(`/api/jobs/${id}/start`, {
      method: 'POST',
      body: JSON.stringify({ song_ids: songIds, fetch_lyrics: fetchLyrics }),
    }),

  collections: () => req<SavedCollection[]>('/api/collections'),
  collectionParts: (id: number) => req<Song[]>(`/api/collections/${id}/songs?multipart_only=true`),
  collectionSongs: (id: number) => req<Song[]>(`/api/collections/${id}/songs`),
  updateCollection: (id: number, patch: { auto_update?: boolean; fetch_lyrics?: boolean; multipart_policy?: MultipartPolicy }) =>
    req<SavedCollection>(`/api/collections/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  refreshCollection: (id: number) =>
    req<CollectionRefreshResult>(`/api/collections/${id}/refresh`, { method: 'POST' }),
  deleteCollection: (id: number) =>
    req<{ deleted: boolean; songs_preserved: boolean }>(`/api/collections/${id}`, { method: 'DELETE' }),

  selectCollectionParts: (id: number, songIds: number[], bvids: string[]) =>
    req<CollectionRefreshResult>(`/api/collections/${id}/parts`, { method: 'POST', body: JSON.stringify({ song_ids: songIds, bvids }) }),
  syncArtists: () => req<{ artists: number }>('/api/artists/sync', { method: 'POST' }),
  lyricsFailures: () => req<import('../types').LyricsFailure[]>('/api/lyrics-failures'),
  retryLyrics: (id: number) => req<{ started: boolean; queued: number; message: string }>(`/api/songs/${id}/lyrics/retry`, { method: 'POST' }),
  dismissLyricsFailure: (id: number) => req<{ ok: boolean }>(`/api/songs/${id}/lyrics/failure/dismiss`, { method: 'POST' }),
  refreshLyrics: (id: number) => req<{ matched: boolean; message: string; song: Song }>(`/api/songs/${id}/lyrics/refresh`, { method: 'POST' }),
  refreshCover: (id: number) => req<Song>(`/api/songs/${id}/cover/refresh`, { method: 'POST' }),
  songs: () => req<Song[]>('/api/songs'),
  analyzeSong: (id: number) => req<MetadataSuggestion>(`/api/songs/${id}/analyze?refresh=true`, { method: 'POST' }),
  song: (id: number) => req<Song>(`/api/songs/${id}`),
  updateSong: (
    id: number,
    patch: Partial<Pick<Song, 'title' | 'artist' | 'album' | 'cid' | 'part_index' | 'part_title' | 'duration'>>,
  ) =>
    req<Song>(`/api/songs/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteSong: (id: number) => req<{ deleted: boolean }>(`/api/songs/${id}`, { method: 'DELETE' }),
  uploadLyrics: (id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return req<Song>(`/api/songs/${id}/lyrics`, { method: 'PUT', body: form })
  },
  deleteLyrics: (id: number) =>
    req<Song>(`/api/songs/${id}/lyrics`, { method: 'DELETE' }),

  playlists: () => req<Playlist[]>('/api/playlists'),
  createPlaylist: (name: string) =>
    req<Playlist>('/api/playlists', { method: 'POST', body: JSON.stringify({ name }) }),
  renamePlaylist: (id: number, name: string) =>
    req<Playlist>(`/api/playlists/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  deletePlaylist: (id: number) =>
    req<{ deleted: boolean }>(`/api/playlists/${id}`, { method: 'DELETE' }),
  playlistSongs: (id: number) => req<Song[]>(`/api/playlists/${id}/songs`),
  addPlaylistSong: (playlistId: number, songId: number) =>
    req<{ added: boolean }>(`/api/playlists/${playlistId}/songs/${songId}`, { method: 'POST' }),
  removePlaylistSong: (playlistId: number, songId: number) =>
    req<{ removed: boolean }>(`/api/playlists/${playlistId}/songs/${songId}`, { method: 'DELETE' }),

  streamUrl: (id: number) => withToken(`${getBase()}/api/stream/${id}`),
  coverUrl: (song: Song) => (song.cover ? withToken(`${getBase()}${song.cover}`) : ''),
  exportUrl: () => withToken(`${getBase()}/api/export.csv`),
  videoUrl: (song: Song) =>
    song.source_url ||
    `https://www.bilibili.com/video/${song.bvid}${song.part_index && song.part_index > 1 ? `?p=${song.part_index}` : ''}`,
}
