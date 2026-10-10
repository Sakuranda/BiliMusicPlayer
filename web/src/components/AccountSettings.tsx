import { useEffect, useState } from 'react'
import { api } from '../lib/api'

export default function AccountSettings() {
  const [username, setUsername] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [password, setPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [code, setCode] = useState('')
  const [setup, setSetup] = useState<{ secret: string; qr_image: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    api.accountSettings().then(value => { setUsername(value.username); setEnabled(value.two_factor_enabled) })
      .catch(e => setError(String(e.message || e)))
  }, [])
  const act = async (action: 'account' | 'setup' | 'enable' | 'disable') => {
    setError(''); setMessage('')
    if (action === 'account' && newPassword !== confirm) { setError('两次新密码不一致'); return }
    setBusy(true)
    try {
      const result = await api.changeSecurity(action, { current_password: password, code,
        ...(action === 'account' ? { username, new_password: newPassword || undefined } : {}) })
      setUsername(result.username); setEnabled(result.two_factor_enabled); setCode('')
      if (action === 'setup' && result.secret && result.qr_image) {
        setSetup({ secret: result.secret, qr_image: result.qr_image })
        setMessage('请用验证器扫描二维码，再输入动态验证码确认启用。')
      } else {
        setSetup(null); setPassword(''); setNewPassword(''); setConfirm('')
        setMessage(action === 'enable' ? '2FA 已开启，其他浏览器需要重新登录。' : action === 'disable' ? '2FA 已关闭，之后使用用户名和密码登录。' : '账户已更新，其他浏览器需要重新登录。')
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const input = 'mb-4 w-full rounded-xl border border-line bg-bg2 px-3 py-3 text-sm focus:outline-none focus:border-accent/60'
  const button = 'rounded-xl border border-line px-4 py-3 text-sm disabled:opacity-40'
  return <section className="mb-6 rounded-2xl border border-line bg-panel p-5 sm:p-6">
    <h2 className="mb-3 text-base font-medium">管理员账户与 2FA</h2>
    <p className="mb-4 text-xs leading-relaxed text-muted">2FA 为可选项，目前{enabled ? '已开启' : '未开启'}。开启后，每次登录还需验证器的 6 位动态验证码；这些设置只影响网页管理员账户。</p>
    <label className="mb-1.5 block text-xs text-muted" htmlFor="admin-name">管理员用户名</label>
    <input id="admin-name" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} className={input} maxLength={80} />
    <label className="mb-1.5 block text-xs text-muted" htmlFor="admin-current">当前密码（确认身份）</label>
    <input id="admin-current" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} className={input} />
    <label className="mb-1.5 block text-xs text-muted" htmlFor="admin-new">新密码（留空保留，至少 12 字）</label>
    <input id="admin-new" type="password" autoComplete="new-password" value={newPassword} onChange={e => setNewPassword(e.target.value)} className={input} />
    <label className="mb-1.5 block text-xs text-muted" htmlFor="admin-confirm">再次输入新密码</label>
    <input id="admin-confirm" type="password" autoComplete="new-password" value={confirm} onChange={e => setConfirm(e.target.value)} className={input} />
    {setup && <div className="mb-4 rounded-xl bg-bg2 p-4">
      <p className="mb-3 text-sm text-muted">用验证器添加账户（绑定二维码 10 分钟内有效）</p>
      <img src={setup.qr_image} alt="2FA 绑定二维码" className="mb-3 h-48 w-48 rounded-lg" />
      <p className="text-xs text-muted">也可以手动输入密钥：</p><code className="block select-all break-all py-2 text-xs">{setup.secret}</code>
      <p className="text-xs text-muted">验证器类型选「基于时间」。密钥只在这次绑定时显示。</p>
    </div>}
    {(enabled || setup) && <>
      <label className="mb-1.5 block text-xs text-muted" htmlFor="admin-totp">验证器动态验证码</label>
      <input id="admin-totp" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={e => setCode(e.target.value.replace(/\D/g, ''))} className={input} />
    </>}
    <div className="flex flex-wrap gap-3">
      <button disabled={busy || !password || !username.trim() || (!!newPassword && newPassword.length < 12)} className={button} onClick={() => void act('account')}>保存账户</button>
      {!enabled && !setup && <button disabled={busy || !password} className={button} onClick={() => void act('setup')}>设置 2FA</button>}
      {setup && <>
        <button disabled={busy || !password || code.length !== 6} className={button} onClick={() => void act('enable')}>验证并开启 2FA</button>
        <button disabled={busy} className={button} onClick={() => { setSetup(null); setCode(''); setMessage('已取消绑定，2FA 保持关闭。') }}>取消绑定</button>
      </>}
      {enabled && <button disabled={busy || !password || code.length !== 6} className={button} onClick={() => void act('disable')}>关闭 2FA</button>}
    </div>
    {message && <p role="status" className="mt-3 text-xs text-muted">{message}</p>}
    {error && <p role="alert" className="mt-3 text-xs text-danger">{error}</p>}
  </section>
}
