import { defineStore } from 'pinia'

import { UserApi } from '@/network/api/users'

interface SignupState {
  username: string
  nickname: string
  email: string
  inviteCode: string
  password: string
  /** When the last verification code was sent (ms since epoch), 0 if never. */
  codeSentAt: number
}

// What the verify-email page needs to survive a refresh. The password is not
// in it and never is: it stays in memory, and the page asks for it again when a
// refresh has dropped it.
const STORAGE_KEY = 'cheese:signup'

type Persisted = Pick<SignupState, 'username' | 'nickname' | 'email' | 'inviteCode' | 'codeSentAt'>

function loadPersisted(): Partial<Persisted> {
  try {
    const raw = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? 'null')
    if (!raw || typeof raw !== 'object') return {}
    const text = (v: unknown) => (typeof v === 'string' ? v : '')
    return {
      username: text(raw.username),
      nickname: text(raw.nickname),
      email: text(raw.email),
      inviteCode: text(raw.inviteCode),
      codeSentAt: typeof raw.codeSentAt === 'number' ? raw.codeSentAt : 0,
    }
  } catch {
    return {}
  }
}

function persist(state: SignupState) {
  const { username, nickname, email, inviteCode, codeSentAt } = state
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ username, nickname, email, inviteCode, codeSentAt }))
  } catch {
    // Storage unavailable: the flow still works until the page is refreshed.
  }
}

function forgetPersisted() {
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // Storage unavailable: nothing was kept.
  }
}

const emailVerificationEnabled = () => import.meta.env.VITE_DISABLE_EMAIL_VERIFY !== 'true'

export const useSignupStore = defineStore('signup', {
  state: (): SignupState => ({
    username: '',
    nickname: '',
    email: '',
    inviteCode: '',
    password: '',
    codeSentAt: 0,
    ...loadPersisted(),
  }),

  actions: {
    async startSignup(data: {
      username: string
      nickname: string
      email: string
      inviteCode?: string
      password: string
    }) {
      // 保存注册信息
      this.username = data.username
      this.nickname = data.nickname
      this.email = data.email
      this.inviteCode = data.inviteCode?.trim() ?? ''
      this.password = data.password

      // 发送验证邮件
      if (emailVerificationEnabled()) {
        await UserApi.sendEmailCode(data.email, this.inviteCode || undefined)
      }
      this.codeSentAt = Date.now()
      persist(this.$state)
    },

    async resendCode() {
      if (emailVerificationEnabled()) {
        await UserApi.sendEmailCode(this.email, this.inviteCode || undefined)
      }
      this.codeSentAt = Date.now()
      persist(this.$state)
    },

    async signup(emailCode: string) {
      const response = await UserApi.register({
        username: this.username,
        nickname: this.nickname,
        password: this.password,
        email: this.email,
        emailCode,
        ...(this.inviteCode ? { inviteCode: this.inviteCode } : {}),
      })

      // 清空 store。先清存储：$reset 会重新读一遍它。
      forgetPersisted()
      this.$reset()

      return response
    },
  },
})
