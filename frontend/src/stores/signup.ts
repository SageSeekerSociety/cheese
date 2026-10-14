import type { AcceptedDocuments, ConsentMethod } from '@/types/apiLegal'

import { defineStore } from 'pinia'

import { UserAuthApi } from '@/api/userAuth'
import { storageGetJson, storageRemove, storageSetJson } from '@/lib/browserStorage'

interface SignupState {
  username: string
  nickname: string
  email: string
  inviteCode: string
  password: string
  /** The terms the person agreed to on the form, sent with the registration. */
  consent: Consent | null
  /** When the last verification code was sent (ms since epoch), 0 if never. */
  codeSentAt: number
}

type Consent = { documents: AcceptedDocuments; method: ConsentMethod }

// What the verify-email page needs to survive a refresh. The password is not
// in it and never is: it stays in memory, and the page asks for it again when a
// refresh has dropped it. The consent is kept because it is what the person
// actually chose; one that does not read back intact is dropped, and the page
// asks again rather than assuming it.
const STORAGE_KEY = 'cheese:signup'

type Persisted = Pick<SignupState, 'username' | 'nickname' | 'email' | 'inviteCode' | 'consent' | 'codeSentAt'>

function readConsent(raw: unknown): Consent | null {
  if (!raw || typeof raw !== 'object') return null
  const { documents, method } = raw as { documents?: unknown; method?: unknown }
  if (method !== 'checkbox' && method !== 'dialog') return null
  if (!documents || typeof documents !== 'object') return null
  const entries = Object.entries(documents)
  if (entries.length === 0 || entries.some(([, v]) => typeof v !== 'string')) return null
  return { documents: documents as AcceptedDocuments, method }
}

function loadPersisted(): Partial<Persisted> {
  const raw = storageGetJson<Record<string, unknown>>(STORAGE_KEY, 'session')
  if (!raw || typeof raw !== 'object') return {}
  const text = (v: unknown) => (typeof v === 'string' ? v : '')
  return {
    username: text(raw.username),
    nickname: text(raw.nickname),
    email: text(raw.email),
    inviteCode: text(raw.inviteCode),
    consent: readConsent(raw.consent),
    codeSentAt: typeof raw.codeSentAt === 'number' ? raw.codeSentAt : 0,
  }
}

function persist(state: SignupState) {
  const { username, nickname, email, inviteCode, consent, codeSentAt } = state
  storageSetJson(STORAGE_KEY, { username, nickname, email, inviteCode, consent, codeSentAt }, 'session')
}

function forgetPersisted() {
  storageRemove(STORAGE_KEY, 'session')
}

const emailVerificationEnabled = () => import.meta.env.VITE_DISABLE_EMAIL_VERIFY !== 'true'

export const useSignupStore = defineStore('signup', {
  state: (): SignupState => ({
    username: '',
    nickname: '',
    email: '',
    inviteCode: '',
    password: '',
    consent: null,
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
      consent: Consent
    }) {
      // 保存注册信息
      this.username = data.username
      this.nickname = data.nickname
      this.email = data.email
      this.inviteCode = data.inviteCode?.trim() ?? ''
      this.password = data.password
      this.consent = data.consent

      // 发送验证邮件
      if (emailVerificationEnabled()) {
        await UserAuthApi.sendEmailCode(data.email, this.inviteCode || undefined)
      }
      this.codeSentAt = Date.now()
      persist(this.$state)
    },

    async resendCode() {
      if (emailVerificationEnabled()) {
        await UserAuthApi.sendEmailCode(this.email, this.inviteCode || undefined)
      }
      this.codeSentAt = Date.now()
      persist(this.$state)
    },

    async signup(emailCode: string) {
      const response = await UserAuthApi.register({
        username: this.username,
        nickname: this.nickname,
        password: this.password,
        email: this.email,
        emailCode,
        consent: this.consent ?? undefined,
        ...(this.inviteCode ? { inviteCode: this.inviteCode } : {}),
      })

      // 清空 store。先清存储：$reset 会重新读一遍它。
      forgetPersisted()
      this.$reset()

      return response
    },
  },
})
