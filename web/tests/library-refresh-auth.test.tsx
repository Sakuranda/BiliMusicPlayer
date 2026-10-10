// @vitest-environment jsdom
import type { ReactNode } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../src/App'
import { api } from '../src/lib/api'

vi.mock('../src/lib/api', () => ({ api: { health: vi.fn(), authStatus: vi.fn(), songs: vi.fn() }, getToken: () => '', setToken: vi.fn() }))
vi.mock('../src/hooks/usePlayer', () => ({ PlayerProvider: ({ children }: { children: ReactNode }) => children }))
vi.mock('../src/components/LibraryView', () => ({ default: ({ onRefresh }: { onRefresh: () => Promise<void> }) => <button onClick={() => void onRefresh()}>刷新测试曲库</button> }))
vi.mock('../src/components/Sidebar', () => ({ default: () => null }))
vi.mock('../src/components/PlayerBar', () => ({ default: () => null }))
vi.mock('../src/components/LoginView', () => ({ default: () => <p>请登录曲库</p> }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('checks session on manual refresh and returns an expired login to the login page', async () => {
  vi.mocked(api.health).mockResolvedValue({ ok: true })
  vi.mocked(api.authStatus).mockResolvedValueOnce({ enabled: true, authenticated: true, username: 'test' })
    .mockResolvedValueOnce({ enabled: true, authenticated: false, username: null })
  vi.mocked(api.songs).mockResolvedValue([])
  render(<App />)
  await screen.findByRole('button', { name: '刷新测试曲库' })
  await waitFor(() => expect(api.songs).toHaveBeenCalledOnce())
  fireEvent.click(screen.getByRole('button', { name: '刷新测试曲库' }))
  await screen.findByText('请登录曲库')
  expect(api.songs).toHaveBeenCalledOnce()
})
