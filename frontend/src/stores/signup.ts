import type { AcceptedDocuments, ConsentMethod } from '@/network/api/legal/types'

import { defineStore } from 'pinia'

import { UserApi } from '@/network/api/users'

interface SignupState {
  username: string
  nickname: string
  email: string
  inviteCode: string
  password: string
  consent: { documents: AcceptedDocuments; method: ConsentMethod } | null
}

export const useSignupStore = defineStore('signup', {
  state: (): SignupState => ({
    username: '',
    nickname: '',
    email: '',
    inviteCode: '',
    password: '',
    consent: null,
  }),

  actions: {
    async startSignup(data: {
      username: string
      nickname: string
      email: string
      inviteCode?: string
      password: string
      consent: { documents: AcceptedDocuments; method: ConsentMethod }
    }) {
      // 保存注册信息
      this.username = data.username
      this.nickname = data.nickname
      this.email = data.email
      this.inviteCode = data.inviteCode?.trim() ?? ''
      this.password = data.password
      this.consent = data.consent

      // 发送验证邮件
      if (import.meta.env.VITE_DISABLE_EMAIL_VERIFY !== 'true') {
        await UserApi.sendEmailCode(data.email, this.inviteCode || undefined)
      }
    },

    async signup(emailCode: string) {
      const response = await UserApi.register({
        username: this.username,
        nickname: this.nickname,
        password: this.password,
        email: this.email,
        emailCode,
        consent: this.consent ?? undefined,
        ...(this.inviteCode ? { inviteCode: this.inviteCode } : {}),
      })

      // 清空 store
      this.$reset()

      return response
    },
  },
})
