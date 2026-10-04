/// <reference types="node" />
// 「邀请回答」弹窗里那份「可以邀请谁」的名单。钉两件事：
//
//   1. **没登录就不发那两趟请求**。后端这两条读侧要登录，匿名会拿到 401
//      （apps 那边的口径见 backend/app/api/routes/questions.py 的邀请读侧）。
//      游客只是**还不能邀请人**，替他打一趟必然 401 的请求、再把 401 翻成一句
//      报错弹窗，是拿他看不懂的东西拦他。
//   2. **拉失败要有落点**。原来这里是挂载时一句裸的 `await Promise.all([...])`：
//      请求一失败，组件没人接，控制台多一条 unhandled rejection，而名单空着——
//      「失败了」被画成了「没有人可以邀请」。现在落在一句 toast 上。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const invitationRecommend = vi.fn()
const getInvitaions = vi.fn()

vi.mock('@/network/api/questions', () => ({
  QuestionApi: {
    invitationRecommend: (...args: unknown[]) => invitationRecommend(...args),
    getInvitaions: (...args: unknown[]) => getInvitaions(...args),
    inviteUser: vi.fn(),
  },
}))

const toastError = vi.fn()
vi.mock('vuetify-sonner', () => ({ toast: { error: (...args: unknown[]) => toastError(...args) } }))
// 只换掉 `useI18n`，其余照旧：`@/services/account` 那条 import 链会走到
// `@/i18n/index`，那里要的是真的 `createI18n`（views/spaces/Detail.spec.ts 同款）。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import InvitationList from '../InvitationList.vue'

import AccountService from '@/services/account'

const QUESTION_ID = 42
const LOAD_FAILED = 'questions.invitationList.errors.loadFailed'

// 一条没人接的 rejection 不会当场把测试判红：它只在运行末尾冒一声「Unhandled
// Rejection」，而那声是在断言跑完之后才响的，钉不住。所以自己接一个监听器，把
// 它变成这一份可以当场断言的记录——「有没有人接住」这件事才有落点。
const unhandled: unknown[] = []
const onUnhandled = (reason: unknown) => unhandled.push(reason)

/** 等一拍 macrotask：unhandled rejection 是在微任务队列走空之后才报出来的。 */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function mountList() {
  return render(InvitationList, {
    props: { questionId: QUESTION_ID },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  invitationRecommend.mockReset().mockResolvedValue({ data: { users: [] } })
  getInvitaions.mockReset().mockResolvedValue({ data: { invitations: [] } })
  toastError.mockReset()
  process.on('unhandledRejection', onUnhandled)
})

afterEach(() => {
  process.off('unhandledRejection', onUnhandled)
  unhandled.length = 0
  AccountService.loggedIn = false
  cleanup()
})

describe('the invitation list', () => {
  it('asks nobody for anything while nobody is logged in', async () => {
    AccountService.loggedIn = false
    mountList()
    await flush()

    // 两趟都不发：一个还没登录的人在这里没有任何可邀请的对象可言。
    expect(invitationRecommend).not.toHaveBeenCalled()
    expect(getInvitaions).not.toHaveBeenCalled()
    // 游客也不该被弹一句错误：他只是还不能邀请人。
    expect(toastError).not.toHaveBeenCalled()
    expect(unhandled).toEqual([])
  })

  it('asks once there is someone to ask as', async () => {
    AccountService.loggedIn = true
    mountList()

    await waitFor(() => expect(invitationRecommend).toHaveBeenCalledWith(QUESTION_ID))
    expect(getInvitaions).toHaveBeenCalledWith(QUESTION_ID)
  })

  it('lands a failed load on a toast instead of an unhandled rejection', async () => {
    AccountService.loggedIn = true
    invitationRecommend.mockRejectedValue(new Error('Unauthorized'))
    getInvitaions.mockRejectedValue(new Error('Unauthorized'))
    mountList()
    await flush()

    // 先断言这一条：红的时候它会直接把那条没人接的 rejection 原文打出来。
    expect(unhandled, '这条 rejection 必须有人接住，不能漏给控制台').toEqual([])
    expect(toastError).toHaveBeenCalledWith(LOAD_FAILED)
  })
})
