import { useEffect, useState } from 'react'
import { Check, Download, ExternalLink, Globe2, Loader2, LogOut, Smartphone } from 'lucide-react'
import { api, getBase, setBase } from '../lib/api'
import type { AccessAudit } from '../types'
import { usePlayer } from '../hooks/playerContext'
import { downloadPlayerDiagnostics } from '../lib/playerDiagnostics'
import AccountSettings from './AccountSettings'

interface Props {
  onSaved: () => void
  username?: string | null
  authEnabled: boolean
  onLogout: () => void
}

export default function SettingsView({ onSaved, username, authEnabled, onLogout }: Props) {
  const { current, restoreSystemControls } = usePlayer()
  const [controlsRequested, setControlsRequested] = useState(false)
  const [base, setBaseLocal] = useState(getBase())
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<'ok' | 'fail' | null>(null)
  const [audit, setAudit] = useState<AccessAudit | null>(null)
  const [auditError, setAuditError] = useState('')
  const [navidromeUrl, setNavidromeUrl] = useState<string | null>(null)

  useEffect(() => {
    api.accessAudit().then(setAudit).catch((e) => setAuditError(e instanceof Error ? e.message : String(e)))
    api.authStatus().then(status => setNavidromeUrl(status.navidrome_url || null)).catch(() => {})
  }, [])

  const save = () => {
    setBase(base.trim().replace(/\/+$/, ''))
    onSaved()
  }

  const test = async () => {
    setTesting(true)
    setTestResult(null)
    setBase(base.trim().replace(/\/+$/, ''))
    try {
      const status = await api.authStatus()
      if (!status.authenticated) throw new Error('会话无效')
      setTestResult('ok')
    } catch {
      setTestResult('fail')
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 pt-6 sm:px-6 md:px-8 md:pt-10">
      <h1 className="text-3xl font-semibold tracking-tight mb-8">设置</h1>

      {/* 服务器连接 */}
      <div className="bg-panel border border-line rounded-2xl p-6 mb-6">
        <h2 className="text-base font-medium mb-4">服务器连接</h2>

        <label className="block text-sm text-muted mb-1.5">API 地址</label>
        <input
          value={base}
          onChange={(e) => setBaseLocal(e.target.value)}
          placeholder="留空使用当前网站；跨域部署时填自己的 API 地址"
          className="w-full bg-bg2 border border-line rounded-xl px-3 py-3 text-sm placeholder:text-faint focus:outline-none focus:border-accent/60 transition-colors"
        />
        <p className="text-[11px] text-faint mt-1.5">
          一键部署和本地开发留空即可；跨域连接时填写自己的服务器地址
        </p>

        <div className="flex items-center gap-3 mt-5">
          <button
            onClick={test}
            disabled={testing}
            className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-panel2 border border-line text-sm hover:border-accent/50 transition-colors disabled:opacity-50"
          >
            {testing ? <Loader2 size={15} className="spin" /> : <Check size={15} />} 测试连接
          </button>
          {testResult === 'ok' && <span className="text-sm text-emerald-500">✓ 连接成功</span>}
          {testResult === 'fail' && <span className="text-sm text-danger">✗ 连接失败或登录已失效</span>}
        </div>
      </div>

      {/* 登录与访问记录 */}
      {authEnabled && <AccountSettings />}
      <div className="mb-6 rounded-2xl border border-line bg-panel p-5 sm:p-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Globe2 size={17} className="text-accent" />
            <div>
              <h2 className="text-base font-medium">登录与访问记录</h2>
              <p className="mt-0.5 text-[11px] text-faint">只记录成功登录后的 IP、属地和访问时间</p>
            </div>
          </div>
          {authEnabled && (
            <button
              onClick={async () => { await api.logout(); onLogout() }}
              className="inline-flex items-center gap-1.5 rounded-xl border border-line bg-panel2 px-3 py-2 text-xs text-muted hover:text-danger"
            ><LogOut size={14} /> 退出{username ? ` ${username}` : ''}</button>
          )}
        </div>
        {audit ? (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-bg2 p-3"><div className="text-2xl font-semibold text-ink">{audit.unique_ip_count}</div><div className="mt-1 text-[11px] text-faint">成功访问 IP</div></div>
              <div className="rounded-xl bg-bg2 p-3"><div className="text-2xl font-semibold text-ink">{audit.successful_sessions}</div><div className="mt-1 text-[11px] text-faint">成功登录次数</div></div>
            </div>
            <div className="max-h-80 space-y-2 overflow-y-auto">
              {audit.entries.map((entry) => {
                const location = [entry.country, entry.region, entry.city].filter(Boolean).join(' · ') || '属地暂不可用'
                return (
                  <div key={entry.id} className="rounded-xl border border-line bg-bg2 px-3 py-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-1.5">
                      <span className="font-mono text-xs text-ink">{entry.ip}</span>
                      <span className="text-[10px] text-faint">{new Date(entry.login_at).toLocaleString('zh-CN')}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap justify-between gap-1 text-[11px] text-muted">
                      <span>{location}</span><span>最近 {new Date(entry.last_seen).toLocaleString('zh-CN')} · {entry.request_count} 次活跃记录</span>
                    </div>
                  </div>
                )
              })}
              {!audit.entries.length && <p className="py-6 text-center text-xs text-faint">还没有成功登录记录</p>}
            </div>
          </>
        ) : auditError ? <p className="text-xs text-danger">{auditError}</p> : <div className="flex items-center gap-2 text-xs text-faint"><Loader2 size={14} className="spin" /> 加载访问记录…</div>}
      </div>

      <div className="bg-panel border border-line rounded-2xl p-6 mb-6">
        <h2 className="text-base font-medium mb-3">锁屏与系统切歌</h2>
        <p className="text-sm text-muted leading-relaxed mb-4">
          iPhone 或 Mac 的系统按钮偶尔变成前后 15 秒时，可以重新声明切歌控制，当前播放进度会保留。
        </p>
        <div className="flex flex-wrap gap-3">
          <button disabled={!current || !('mediaSession' in navigator)}
            onClick={() => { restoreSystemControls(); setControlsRequested(true) }}
            className="rounded-xl border border-line bg-panel2 px-4 py-2.5 text-sm disabled:opacity-50">
            恢复系统切歌按钮
          </button>
          <button onClick={downloadPlayerDiagnostics}
            className="rounded-xl border border-line bg-panel2 px-4 py-2.5 text-sm">
            导出播放诊断
          </button>
        </div>
        {controlsRequested && <p role="status" className="text-xs text-muted mt-3">已重新声明切歌控制，请查看系统音乐面板。</p>}
        <p className="text-[11px] text-faint mt-3">诊断仅保留本页最近 100 条播放事件，刷新页面后清空，点击导出才会保存到本机。</p>
      </div>

      {/* iOS 播放 */}
      <div className="bg-panel border border-line rounded-2xl p-6 mb-6">
        <div className="flex items-center gap-2 mb-4">
          <Smartphone size={17} className="text-accent" />
          <h2 className="text-base font-medium">iOS 在线播放</h2>
        </div>
        <p className="text-sm text-muted mb-4 leading-relaxed">
          服务器上同时运行 <span className="text-ink">Navidrome</span> 曲库服务（Subsonic 协议），
          iPhone 用免费开源的 <span className="text-ink">Amperfy</span> 即可在线听，支持歌词、离线缓存和 CarPlay。
        </p>
        <ol className="text-sm text-muted space-y-2.5 list-decimal list-inside leading-relaxed">
          <li>App Store 搜索下载 <span className="text-ink">Amperfy</span>（国区可下载，免费）</li>
          <li>
            打开 App → 添加服务器 → 协议选 <span className="text-ink">Subsonic</span>，地址填{' '}
            <code className="bg-bg2 px-1.5 py-0.5 rounded text-accent break-all">{navidromeUrl || '你的 Navidrome 地址（见服务器配置）'}</code>
          </li>
          <li>用户名/密码 = Navidrome 账号（首次部署后去 Navidrome 网页注册）</li>
          <li>保持在线模式，在「播放列表」下拉刷新，打开「最近添加 · 新歌在前」或「最早添加 · 老歌在前」，再下拉刷新歌单内容</li>
          <li>Amperfy 2.0 及以上也可以在「歌曲 → 排序 → Date Added」按添加时间查看新歌</li>
        </ol>
        <div className="mt-4 space-y-3 rounded-xl bg-bg2 p-4 text-sm text-muted leading-relaxed">
          <p>新歌下载完成后，服务器扫描入库，App 联网刷新后才会显示。网页界面更新不会升级 Amperfy，App 本体由 App Store 更新。</p>
          <p>建议在 Amperfy 的音频格式设置中，分别将 Wi-Fi、蜂窝网络及下载缓存选为 Raw（原始格式），直接播放服务器上的 AAC/M4A。已有离线缓存需要重新下载才会替换音频。</p>
          <p>歌名、歌词、封面修改也需要服务器扫描和 App 刷新。网页歌单、播放队列与 App 各自独立。</p>
        </div>
        <a
          href="https://github.com/BLeeEZ/amperfy"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-xs text-accent hover:text-accent-soft mt-4 transition-colors"
        >
          了解 Amperfy <ExternalLink size={12} />
        </a>
      </div>

      {/* 数据备份 */}
      <div className="bg-panel border border-line rounded-2xl p-6 mb-6">
        <div className="flex items-center gap-2 mb-3">
          <Download size={17} className="text-accent" />
          <h2 className="text-base font-medium">曲库备份</h2>
        </div>
        <p className="text-sm text-muted mb-4 leading-relaxed">
          导出全曲库清单（CSV，含每首歌对应的 B 站视频链接与分P），可用 Excel 打开，重复导入时会自动跳过已下载的歌曲。
        </p>
        <a
          href={api.exportUrl()}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-panel2 border border-line text-sm hover:border-accent/50 transition-colors"
        >
          <Download size={15} /> 导出歌曲清单 CSV
        </a>
      </div>

      <button
        onClick={save}
        className="mt-6 px-5 py-3 rounded-xl bg-primary hover:bg-primary/85 text-white text-sm font-medium transition-colors"
      >
        保存设置
      </button>
    </div>
  )
}
