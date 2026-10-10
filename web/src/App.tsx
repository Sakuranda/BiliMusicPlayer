import { useEffect, useState } from 'react'
import { api, getToken, setToken } from './lib/api'
import { PlayerProvider } from './hooks/usePlayer'
import Sidebar from './components/Sidebar'
import LibraryView from './components/LibraryView'
import ImportView from './components/ImportView'
import SettingsView from './components/SettingsView'
import PlayerBar from './components/PlayerBar'
import LoginView from './components/LoginView'
import type { AuthStatus, Song } from './types'

export type View = 'library' | 'import' | 'settings'

export default function App() {
  const [view, setView] = useState<View>('library')
  const [songs, setSongs] = useState<Song[]>([])
  const [serverOk, setServerOk] = useState<boolean | null>(null)
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [loadError, setLoadError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let alive = true
    Promise.all([api.health(), api.authStatus()])
      .then(([, status]) => {
        if (!alive) return
        // 旧版网页曾把 API Token 存在 localStorage。启用账户登录后主动迁移掉，
        // 避免浏览器继续走脚本兼容通道而绕过登录审计。
        if (status.enabled && getToken()) {
          setToken('')
          if (!status.username) status = { ...status, authenticated: false }
        }
        setServerOk(true)
        setAuth(status)
      })
      .catch(() => {
        if (!alive) return
        setServerOk((previous) => previous === null ? false : previous)
        setLoadError('服务器暂时不可用，可以稍后重试。')
      })
    return () => { alive = false }
  }, [refreshKey])

  useEffect(() => {
    if (!serverOk || !auth?.authenticated) return
    let alive = true
    api.songs()
      .then((s) => { if (alive) { setSongs(s); setLoadError('') } })
      .catch((error) => { if (alive) setLoadError(error instanceof Error ? error.message : '曲库刷新失败') })
    return () => { alive = false }
  }, [serverOk, auth?.authenticated, refreshKey])

  if (serverOk === null) {
    return <div className="grid h-full place-items-center bg-bg text-sm text-muted">正在连接私人曲库…</div>
  }

  if (serverOk === false || (auth?.enabled && !auth.authenticated)) {
    return <LoginView onLoggedIn={() => {
      setAuth({ enabled: true, authenticated: true, username: null })
      setServerOk(true)
      setRefreshKey((key) => key + 1)
    }} />
  }

  return (
    <PlayerProvider>
      <div className="flex h-full flex-col md:flex-row">
        <Sidebar view={view} setView={setView} serverOk={serverOk} />
        <main className="app-content flex-1 min-w-0 overflow-y-auto pt-16 pb-28 md:pt-0">
          {loadError && <div role="alert" className="mx-4 mt-4 rounded-xl border border-danger/30 bg-panel p-3 text-sm text-danger">
            {loadError}<button className="ml-3 underline" onClick={() => setRefreshKey((key) => key + 1)}>重试</button>
          </div>}
          {view === 'library' && (
            <LibraryView
              songs={songs}
              serverOk={serverOk}
              onRefresh={async () => {
                const status = await api.authStatus()
                setAuth(status)
                if (!status.authenticated) return
                const next = await api.songs()
                setSongs(next)
                setLoadError('')
              }}
              onGoImport={() => setView('import')}
            />
          )}
          {view === 'import' && (
            <ImportView
              onImported={() => {
                setRefreshKey((k) => k + 1)
                setView('library')
              }}
              onViewLibrary={() => {
                setRefreshKey((key) => key + 1)
                setView('library')
              }}
            />
          )}
          {view === 'settings' && (
            <SettingsView
              onSaved={() => setRefreshKey((k) => k + 1)}
              username={auth?.username}
              authEnabled={Boolean(auth?.enabled)}
              onLogout={() => {
                setSongs([])
                setAuth({ enabled: true, authenticated: false, username: null })
                setView('library')
              }}
            />
          )}
        </main>
        <PlayerBar onSongSaved={updated => setSongs(items => items.map(song => song.id === updated.id ? updated : song))} />
      </div>
    </PlayerProvider>
  )
}
