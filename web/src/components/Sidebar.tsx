import { Download, Library, Settings } from 'lucide-react'
import Brand from './Brand'
import type { View } from '../App'

interface Props {
  view: View
  setView: (v: View) => void
  serverOk: boolean | null
}

const items: { key: View; label: string; icon: typeof Library }[] = [
  { key: 'library', label: '曲库', icon: Library },
  { key: 'import', label: '导入收藏夹', icon: Download },
  { key: 'settings', label: '设置', icon: Settings },
]

export default function Sidebar({ view, setView, serverOk }: Props) {
  return (
    <aside className="app-navigation fixed inset-x-0 top-0 z-40 h-16 shrink-0 border-b border-line bg-bg2/95 backdrop-blur-xl flex items-center md:static md:w-56 md:h-auto md:border-b-0 md:border-r md:bg-bg2 md:flex-col md:items-stretch">
      <div className="flex items-center gap-2.5 px-3 md:px-5 md:pt-6 md:pb-8">
        <Brand />
        <div className="hidden sm:block">
          <div className="brand-name font-semibold text-[17px] leading-tight tracking-tight">MusicPlayer</div>
          <div className="text-[11px] text-faint">你的私人音乐空间</div>
        </div>
      </div>

      <nav className="ml-auto flex items-center gap-1 px-2 md:ml-0 md:flex-col md:items-stretch md:px-3">
        {items.map(({ key, label, icon: Icon }) => {
          const active = view === key
          return (
            <button
              key={key}
              onClick={() => setView(key)}
              aria-current={active ? 'page' : undefined}
              className={[
                'flex items-center gap-1.5 px-2.5 py-2.5 rounded-xl text-xs transition-colors text-left sm:text-sm md:gap-2.5 md:px-3',
                active
                  ? 'bg-accent-dim text-accent font-medium'
                  : 'text-muted hover:text-ink hover:bg-panel/60',
              ].join(' ')}
            >
              <Icon size={17} className={active ? 'text-accent' : ''} />
              {label}
            </button>
          )
        })}
      </nav>

      <div className="mt-auto px-5 pb-5 hidden md:block">
        <div className="flex items-center gap-2 text-[11px] text-faint">
          <span
            className={[
              'w-2 h-2 rounded-full',
              serverOk === null ? 'bg-faint' : serverOk ? 'bg-emerald-500' : 'bg-danger',
            ].join(' ')}
          />
          {serverOk === null ? '连接中…' : serverOk ? '服务器已连接' : '服务器未连接'}
        </div>
      </div>
    </aside>
  )
}
