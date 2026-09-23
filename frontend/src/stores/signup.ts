import { defineStore } from 'pinia'

import { UserApi } from '@/network/api/users'

interface SignupState {
  username: string
  nickname: string
  email: string
  inviteCode: string
  password: string
}

export const useSignupStore = defineStore('signup', {
  state: (): SignupState => ({
    username: '',
    nickname: '',
    email: '',
    inviteCode: '',
    password: '',
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
        ...(this.inviteCode ? { inviteCode: this.inviteCode } : {}),
      })

      // 清空 store
      this.$reset()

      return response
    },
  },
})
