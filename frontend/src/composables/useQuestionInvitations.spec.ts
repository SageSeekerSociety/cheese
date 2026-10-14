/// <reference types="node" />
// 邀请弹窗里那份名单的取数接线，钉四件事：
//
//   1. **没登录就不发那两趟请求**。后端这两条读侧要登录，匿名会拿到 401
//      （apps 那边的口径见 backend/app/api/routes/questions.py 的邀请读侧）。
//      游客只是**还不能邀请人**，替他打一趟必然 401 的请求、再把 401 翻成一句
//      报错弹窗，是拿他看不懂的东西拦他。
//   2. **弹窗没开就别读**。名单住在弹窗里，弹窗是懒挂载的：没开的时候这份东西没人
//      看得见，这时候读回来是白花两趟请求（页面把 `active` 接在弹窗的开合上）。
//   3. **拉失败要有落点**。原来这里是挂载时一句裸的 `await Promise.all([...])`：
//      请求一失败，组件没人接，控制台多一条 unhandled rejection，而名单空着——
//      「失败了」被画成了「没有人可以邀请」。现在落在一句 toast 上。
//   4. **点那颗按钮真发出邀请**，成了之后那个人当场变成「已邀请」。
//
// 挂的是一个和页面同款的小壳（composable + 画面），因为这一层的行为只有和画面接上
// 才有意义——光调 `useQuestionInvitations()` 得到的是一堆 ref，看不出屏幕上有没有
// 那颗按钮变成「已邀请」。
import type { User } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const invitationRecommend = vi.fn()
const getInvitaions = vi.fn()
const inviteUser = vi.fn()

vi.mock('@/api/questions', () => ({
  QuestionApi: {
    invitationRecommend: (...args: unknown[]) => invitationRecommend(...args),
    getInvitaions: (...args: unknown[]) => getInvitaions(...args),
    inviteUser: (...args: unknown[]) => inviteUser(...args),
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

import { defineComponent, toRef } from 'vue'

import { useQuestionInvitations } from './useQuestionInvitations'

import InvitationList from '@/components/questions/InvitationList.vue'
import { t } from '@/i18n'
import AccountService from '@/services/account'

const QUESTION_ID = 42
const LOAD_FAILED = 'questions.invitationList.errors.loadFailed'
const INVITED = 'questions.invitationList.buttons.invited'

const FULU: User = {
  id: 7,
  username: 'fulu',
  nickname: '福禄',
  avatarId: 3,
  intro: '在做题',
  question_count: 0,
  answer_count: 0,
}

/** 页面那一层的样子：composable 拿名单，「已经邀请过谁」比成 id，其余交给画面。 */
const Harness = defineComponent({
  components: { InvitationList },
  props: {
    questionId: { type: Number, default: QUESTION_ID },
    active: { type: Boolean, default: true },
  },
  setup(props) {
    const { people, invited, invite } = useQuestionInvitations(() => props.questionId, {
      active: toRef(props, 'active'),
    })
    return { people, invited, invite }
  },
  template: `
    <InvitationList
      :people="people"
      :invited-ids="invited.map((user) => user.id)"
      @invite="invite"
    />
  `,
})

function mountList(props: { questionId?: number; active?: boolean } = {}) {
  return render(Harness, {
    props,
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

// 一条没人接的 rejection 不会当场把测试判红：它只在运行末尾冒一声「Unhandled
// Rejection」，而那声是在断言跑完之后才响的，钉不住。所以自己接一个监听器，把
// 它变成这一份可以当场断言的记录——「有没有人接住」这件事才有落点。
const unhandled: unknown[] = []
const onUnhandled = (reason: unknown) => unhandled.push(reason)

/** 等一拍 macrotask：unhandled rejection 是在微任务队列走空之后才报出来的。 */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  invitationRecommend.mockReset().mockResolvedValue({ users: [] })
  getInvitaions.mockReset().mockResolvedValue({ invitations: [] })
  inviteUser.mockReset().mockResolvedValue({})
  toastError.mockReset()
  process.on('unhandledRejection', onUnhandled)
})

afterEach(() => {
  process.off('unhandledRejection', onUnhandled)
  unhandled.length = 0
  AccountService.loggedIn = false
  cleanup()
})

describe('the invitation roster', () => {
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

  it('reads nothing while the dialog that shows it is closed, and reads on the way in', async () => {
    AccountService.loggedIn = true
    const { rerender } = mountList({ active: false })
    await flush()

    // 弹窗没开：名单这会儿没人看得见，那两趟请求白发。
    expect(invitationRecommend).not.toHaveBeenCalled()
    expect(getInvitaions).not.toHaveBeenCalled()

    await rerender({ active: true })

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
    // 这句话由 `useQuestionInvitations` 用全局的 `t` 说出来（组件那边被替成了 stub），
    // 所以照它那句比：同一个 key、同一份译文，不跟着跑测试时的默认语言变。
    expect(toastError).toHaveBeenCalledWith(t(LOAD_FAILED))
  })

  it('invites the person that was clicked, and shows them as invited right after', async () => {
    AccountService.loggedIn = true
    invitationRecommend.mockResolvedValue({ users: [FULU] })

    const { container } = mountList()
    await waitFor(() => expect(container.textContent).toContain('福禄'))
    expect(container.textContent).not.toContain(INVITED)

    await fireEvent.click(container.querySelector('button')!)

    // 出发的是那两样：这道题，和这个人。
    expect(inviteUser).toHaveBeenCalledWith(QUESTION_ID, FULU.id)
    // 成了之后当场变成「已邀请」——不必等下一次读名单。
    await waitFor(() => expect(container.textContent).toContain(INVITED))
  })
})
