import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useSignupStore } from '../signup'

import { UserAuthApi } from '@/api/userAuth'

vi.mock('@/api/userAuth', () => ({
  UserAuthApi: {
    register: vi.fn(),
    sendEmailCode: vi.fn(),
  },
}))

describe('signup store invite-code flow', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(UserAuthApi.sendEmailCode).mockResolvedValue({} as never)
    vi.mocked(UserAuthApi.register).mockResolvedValue({ user: {} } as never)
  })

  it('validates the invite before email and carries it into registration', async () => {
    const store = useSignupStore()

    await store.startSignup({
      username: 'cheese-user',
      nickname: '芝士用户',
      email: 'user@example.com',
      inviteCode: '  invite-once  ',
      password: 'Secret#123',
      consent: { documents: { terms: '1.0', privacy: '1.0' }, method: 'dialog' },
    })

    expect(UserAuthApi.sendEmailCode).toHaveBeenCalledWith('user@example.com', 'invite-once')

    await store.signup('123456')

    expect(UserAuthApi.register).toHaveBeenCalledWith(
      expect.objectContaining({
        emailCode: '123456',
        inviteCode: 'invite-once',
        password: 'Secret#123',
        consent: { documents: { terms: '1.0', privacy: '1.0' }, method: 'dialog' },
      })
    )
  })
})
