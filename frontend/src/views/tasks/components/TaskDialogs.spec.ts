// 「参与确认」说的要和这道题怎么开始对得上：直接开始的题没有申请，不问申请理由；
// 队伍在开始做的那一刻锁定，不说「申请被审核通过后」。申请制的题照旧。
import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('../composables/useTaskInheritance', () => ({
  useTaskInheritance: () => ({ inheritance: ref(null), loading: ref(false) }),
}))

import TaskDialogs from './TaskDialogs.vue'

import i18n, { setLocale } from '@/i18n'
import { taskBus } from '@/views/tasks/events'

const t = (key: string) => i18n.global.t(key)

async function open(task: Record<string, unknown>) {
  render(TaskDialogs, {
    props: {
      taskData: { id: 1, name: '题', requireRealName: false, mentors: [], ...task } as never,
      availableTeams: [],
      loadingTeams: false,
      joinedTeams: [],
      selectedLeaveTeamId: null,
      participationInfo: { identities: [] },
    },
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      // 对话框原地摊开：jsdom 里浮层挂不上，开着的时候里面的东西照常画。
      stubs: {
        AdaptiveDialog: {
          props: ['modelValue'],
          template: '<div v-if="modelValue"><slot /></div>',
        },
      },
    },
  })
  taskBus.emit('verify-dialog-open', true)
  // The form is loaded on demand; its own last line says it is there.
  await waitFor(() => expect(document.body.textContent).toContain(t('tasks.verifyForm.saveLocal')))
}

function lockBox(): string {
  // The lock card and its tooltip are drawn into the page with everything else.
  return document.body.textContent ?? ''
}

describe('参与确认', () => {
  beforeAll(() => setLocale('zh-CN'))
  afterEach(() => cleanup())

  it.each([
    ['直接开始', 'DIRECT', false],
    ['申请制', 'APPLY', true],
  ])('%s的题：问不问申请理由', async (_, startMode, asked) => {
    await open({ startMode, submitterType: 'USER' })

    expect(document.body.textContent?.includes(t('tasks.verifyForm.applyReason'))).toBe(asked)
  })

  it.each([
    ['直接开始', 'DIRECT', 'tasks.verifyDialog.lockNoticeStart', 'tasks.verifyDialog.lockNotice'],
    ['申请制', 'APPLY', 'tasks.verifyDialog.lockNotice', 'tasks.verifyDialog.lockNoticeStart'],
  ])('%s的团队题设了锁定：说队伍从什么时候起不能变', async (_, startMode, said, notSaid) => {
    await open({ startMode, submitterType: 'TEAM', teamLockingPolicy: 'LOCK_ON_APPROVAL' })

    const text = document.body.textContent ?? ''
    const policy = t('tasks.form.teamLockingPolicyLockOnApproval')
    const sentence = (key: string) => i18n.global.t(key, { policy }).split(policy).pop()!
    expect(text).toContain(sentence(said))
    expect(text).not.toContain(sentence(notSaid))
  })

  it.each([
    ['直接开始', 'DIRECT', '开始做这道题之后，蔡老师会作为导师加入你的项目'],
    ['申请制', 'APPLY', '申请通过之后，蔡老师会作为导师加入你的项目'],
  ])('%s的题：导师从什么时候起加入，说的是那一刻', async (_, startMode, said) => {
    await open({ startMode, submitterType: 'USER', mentors: [{ handle: 'cai', nickname: '蔡老师' }] })

    expect(document.body.textContent).toContain(said)
  })

  it('申请制的团队题设了锁定：锁定的说明从头到尾都说「申请通过」', async () => {
    await open({ startMode: 'APPLY', submitterType: 'TEAM', teamLockingPolicy: 'LOCK_ON_APPROVAL' })

    const text = lockBox()
    expect(text).toContain('申请通过后')
    expect(text).not.toContain('审核通过')
    expect(text).not.toContain('报名')
  })
})
