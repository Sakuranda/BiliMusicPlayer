import { useCallback, useEffect, useState } from 'react'
import { Loader2, LockKeyhole, RefreshCw } from 'lucide-react'
import Brand from './Brand'
import { api, getBase, setBase, setToken } from '../lib/api'
import type { CaptchaChallenge } from '../types'

export default function LoginView({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [captcha, setCaptcha] = useState('')
  const [challenge, setChallenge] = useState<CaptchaChallenge | null>(null)
  const [base, setBaseLocal] = useState(getBase())
  const [showServer, setShowServer] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [twoFactor, setTwoFactor] = useState(false)
  const [totpCode, setTotpCode] = useState('')

  const refreshCaptcha = useCallback(async () => {
    setCaptcha('')
    try {
      const status = await api.authStatus()
      setTwoFactor(Boolean(status.two_factor_enabled))
      setChallenge(await api.captcha())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => { void refreshCaptcha() }, [refreshCaptcha])

  const login = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!challenge || !username.trim() || !password || !captcha.trim()) return
    setBusy(true)
    setError('')
    try {
      await api.login(username.trim(), password, challenge.id, captcha.trim(), totpCode)
      setToken('')
      onLoggedIn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      await refreshCaptcha()
    } finally {
      setBusy(false)
    }
  }

  const saveServer = () => {
    setBase(base.trim().replace(/\/+$/, ''))
    setError('')
    void refreshCaptcha()
  }

  return (
    <div className="min-h-full bg-bg px-4 py-10 sm:grid sm:place-items-center">
      <div className="mx-auto w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-5"><Brand size={72} /></div>
          <h1 className="text-2xl font-semibold tracking-tight">MusicPlayer</h1>
          <p className="mt-1.5 text-sm text-muted">登录后进入你的私人曲库</p>
        </div>

        <form onSubmit={login} className="rounded-2xl border border-line bg-panel p-5 shadow-2xl sm:p-6">
          <div className="mb-5 flex items-center gap-2 text-sm font-medium"><LockKeyhole size={16} className="text-accent" /> 管理员登录</div>
          <label className="mb-1.5 block text-xs text-muted">账户</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            className="mb-4 w-full rounded-xl border border-line bg-bg2 px-3 py-3 text-sm focus:border-accent/60 focus:outline-none"
          />
          <label className="mb-1.5 block text-xs text-muted">密码</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            className="mb-4 w-full rounded-xl border border-line bg-bg2 px-3 py-3 text-sm focus:border-accent/60 focus:outline-none"
          />
          <label className="mb-1.5 block text-xs text-muted">验证码</label>
          <div className="flex gap-2">
            <input
              value={captcha}
              onChange={(e) => setCaptcha(e.target.value.toUpperCase())}
              maxLength={5}
              autoComplete="off"
              className="min-w-0 flex-1 rounded-xl border border-line bg-bg2 px-3 py-3 text-sm uppercase tracking-[0.22em] focus:border-accent/60 focus:outline-none"
            />
            <button type="button" onClick={() => void refreshCaptcha()} className="relative h-[46px] w-[132px] overflow-hidden rounded-xl border border-line bg-bg2" title="刷新验证码">
              {challenge ? <img src={challenge.image} alt="图片验证码" className="h-full w-full object-cover" /> : <Loader2 size={16} className="mx-auto spin text-faint" />}
              <span className="absolute right-1 top-1 rounded bg-black/50 p-0.5 text-white opacity-0 transition-opacity hover:opacity-100"><RefreshCw size={10} /></span>
            </button>
          </div>
          {twoFactor && <div className="mt-4">
            <label htmlFor="login-totp" className="mb-1.5 block text-xs text-muted">验证器动态验证码</label>
            <input id="login-totp" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={totpCode}
              onChange={e => setTotpCode(e.target.value.replace(/\D/g, ''))}
              className="w-full rounded-xl border border-line bg-bg2 px-3 py-3 text-sm tracking-widest focus:outline-none" />
          </div>}
          {error && <p className="mt-3 text-xs leading-relaxed text-danger">{error}</p>}
          <button
            type="submit"
            disabled={busy || !challenge || !username.trim() || !password || captcha.length !== 5 || (twoFactor && totpCode.length !== 6)}
            className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-3 text-sm font-medium text-white transition-colors hover:bg-primary/85 disabled:opacity-40"
          >
            {busy && <Loader2 size={15} className="spin" />}{busy ? '验证中…' : '进入曲库'}
          </button>

          <button type="button" onClick={() => setShowServer(!showServer)} className="mt-4 w-full text-center text-[11px] text-faint hover:text-muted">
            {showServer ? '收起服务器设置' : '无法连接？设置服务器地址'}
          </button>
          {showServer && (
            <div className="mt-3 flex gap-2">
              <input value={base} onChange={(e) => setBaseLocal(e.target.value)} placeholder="留空为当前服务器" className="min-w-0 flex-1 rounded-lg border border-line bg-bg2 px-2.5 py-2 text-xs focus:outline-none" />
              <button type="button" onClick={saveServer} className="rounded-lg border border-line px-3 text-xs hover:border-accent/50">应用</button>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
