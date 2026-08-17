import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useSignupStore } from '../signup'

import { UserApi } from '@/network/api/users'

vi.mock('@/network/api/users', () => ({
  UserApi: {
    register: vi.fn(),
    sendEmailCode: vi.fn(),
  },
}))

describe('signup store invite-code flow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(UserApi.sendEmailCode).mockResolvedValue({} as never)
    vi.mocked(UserApi.register).mockResolvedValue({ data: { user: {} } } as never)
  })

  it('validates the invite before email and carries it into registration', async () => {
    const store = useSignupStore()

    await store.startSignup({
      username: 'cheese-user',
      nickname: '芝士用户',
      email: 'user@example.com',
      inviteCode: '  invite-once  ',
      srpSalt: 'salt',
      srpVerifier: 'verifier',
    })

    expect(UserApi.sendEmailCode).toHaveBeenCalledWith('user@example.com', 'invite-once')

    await store.signup('123456')

    expect(UserApi.register).toHaveBeenCalledWith(
      expect.objectContaining({
        emailCode: '123456',
        inviteCode: 'invite-once',
      })
    )
  })
})
