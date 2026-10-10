// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AccountSettings from '../src/components/AccountSettings'
import LoginView from '../src/components/LoginView'
import { api } from '../src/lib/api'
vi.mock('../src/lib/api', () => ({ api: {
  accountSettings: vi.fn(), changeSecurity: vi.fn(), authStatus: vi.fn(), captcha: vi.fn(), login: vi.fn(),
}, getBase: () => '', setBase: vi.fn(), setToken: vi.fn() }))
beforeEach(() => {
  vi.mocked(api.accountSettings).mockResolvedValue({ username: 'admin', two_factor_enabled: false })
  vi.mocked(api.authStatus).mockResolvedValue({ enabled: true, authenticated: false, username: null, two_factor_enabled: false })
  vi.mocked(api.captcha).mockResolvedValue({ id: 'captcha1', image: 'data:image/png;base64,test', expires_in: 300 })
})
afterEach(() => { cleanup(); vi.resetAllMocks() })
it('shows optional 2FA, verifies the binding before enable, and keeps username', async () => {
  vi.mocked(api.changeSecurity).mockResolvedValueOnce({ username: 'admin', two_factor_enabled: false, secret: 'NEWKEY', qr_image: 'data:image/png;base64,qr' })
    .mockResolvedValueOnce({ username: 'admin', two_factor_enabled: true })
  render(<AccountSettings />)
  await waitFor(() => expect((screen.getByLabelText('管理员用户名') as HTMLInputElement).value).toBe('admin'))
  expect(screen.queryByLabelText('验证器动态验证码')).toBeNull()
  fireEvent.change(screen.getByLabelText('当前密码（确认身份）'), { target: { value: 'initial-password' } })
  fireEvent.click(screen.getByRole('button', { name: '设置 2FA' }))
  await screen.findByAltText('2FA 绑定二维码')
  expect(screen.getByText('NEWKEY')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('验证器动态验证码'), { target: { value: '123456' } })
  fireEvent.click(screen.getByRole('button', { name: '验证并开启 2FA' }))
  await screen.findByRole('button', { name: '关闭 2FA' })
  expect(api.changeSecurity).toHaveBeenLastCalledWith('enable', { current_password: 'initial-password', code: '123456' })
})
it('rejects unmatched new passwords before sending and saves changed username/password', async () => {
  vi.mocked(api.changeSecurity).mockResolvedValue({ username: 'owner', two_factor_enabled: false })
  render(<AccountSettings />)
  await waitFor(() => expect((screen.getByLabelText('管理员用户名') as HTMLInputElement).value).toBe('admin'))
  fireEvent.change(screen.getByLabelText('管理员用户名'), { target: { value: 'owner' } })
  fireEvent.change(screen.getByLabelText('当前密码（确认身份）'), { target: { value: 'initial-password' } })
  fireEvent.change(screen.getByLabelText('新密码（留空保留，至少 12 字）'), { target: { value: 'new-password-123' } })
  fireEvent.click(screen.getByRole('button', { name: '保存账户' }))
  expect(api.changeSecurity).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('再次输入新密码'), { target: { value: 'new-password-123' } })
  fireEvent.click(screen.getByRole('button', { name: '保存账户' }))
  await waitFor(() => expect(api.changeSecurity).toHaveBeenCalledWith('account', { current_password: 'initial-password', code: '', username: 'owner', new_password: 'new-password-123' }))
})
it('only asks for an authenticator code when 2FA is enabled', async () => {
  render(<LoginView onLoggedIn={vi.fn()} />)
  await screen.findByAltText('图片验证码')
  expect(screen.queryByLabelText('验证器动态验证码')).toBeNull()
  cleanup()
  vi.mocked(api.authStatus).mockResolvedValue({ enabled: true, authenticated: false, username: null, two_factor_enabled: true })
  render(<LoginView onLoggedIn={vi.fn()} />)
  await screen.findByLabelText('验证器动态验证码')
})
