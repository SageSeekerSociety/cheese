import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import PendingView from './PendingView.vue'

import i18n, { setLocale, t } from '@/i18n'
import { PENDING_INVITATIONS, PENDING_REQUESTS, PROJECT_INVITATIONS } from '@/views/demo/catalogScenesMisc3Fixtures'

type Props = InstanceType<typeof PendingView>['$props']
const base: Props = {
  myRequests: PENDING_REQUESTS,
  myInvitations: PENDING_INVITATIONS,
  projectInvitations: PROJECT_INVITATIONS,
  loadingMyRequests: false,
  loadingMyInvitations: false,
  loadingProjectInvitations: false,
  failedMyRequests: false,
  failedMyInvitations: false,
  failedProjectInvitations: false,
  myRequestsError: null,
  myInvitationsError: null,
  projectInvitationsError: null,
  answering: null,
}
function mount(props: Partial<Props> = {}) {
  return render(PendingView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('申请与邀请的独立画面', () => {
  it.each([
    ['myRequests', 'loadingMyRequests', 'failedMyRequests', 'myRequestsError', 'noRequests', 'retryRequests'],
    [
      'myInvitations',
      'loadingMyInvitations',
      'failedMyInvitations',
      'myInvitationsError',
      'noInvitations',
      'retryInvitations',
    ],
    [
      'projectInvitations',
      'loadingProjectInvitations',
      'failedProjectInvitations',
      'projectInvitationsError',
      'noProjectInvitations',
      'retryProjectInvitations',
    ],
  ])('%s 独立显示加载、失败与空态，重试只通知对应段', async (rows, loading, failed, error, empty, event) => {
    const view = mount({ [rows]: [], [loading]: true })
    expect(view.container.querySelector('.v-progress-circular')).not.toBeNull()
    expect(view.queryByText(t(`teams.pending.${empty}`))).toBeNull()
    await view.rerender({ [loading]: false, [failed]: true, [error]: 'timeout' })
    expect(view.queryByText('timeout')).not.toBeNull()
    expect(view.queryByText(t(`teams.pending.${empty}`))).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: t('global.loadError.retry') }))
    expect(view.emitted()[event]).toEqual([[]])
    await view.rerender({ [failed]: false })
    expect(view.queryByText(t(`teams.pending.${empty}`))).not.toBeNull()
  })

  it('申请取消与团队邀请接受、拒绝分别带回正确的编号', async () => {
    const view = mount({ projectInvitations: [] })
    await fireEvent.click(view.getByRole('button', { name: t('global.cancel') }))
    await fireEvent.click(view.getByTitle(t('teams.pending.accept')))
    await fireEvent.click(view.getByTitle(t('teams.pending.decline')))
    expect(view.emitted().cancelRequest).toEqual([[PENDING_REQUESTS[0].id]])
    expect(view.emitted().acceptInvitation).toEqual([[PENDING_INVITATIONS[0].id]])
    expect(view.emitted().declineInvitation).toEqual([[PENDING_INVITATIONS[0].id]])
  })

  it('已处理的申请与团队邀请不再提供操作', () => {
    const view = mount({
      myRequests: [{ ...PENDING_REQUESTS[0], status: 'CANCELED' }],
      myInvitations: [{ ...PENDING_INVITATIONS[0], status: 'ACCEPTED' }],
      projectInvitations: [],
    })
    expect(view.queryAllByRole('button')).toHaveLength(0)
  })

  it('答复中只禁用对应项目的两颗按钮，其他项目仍能接受或拒绝', async () => {
    const view = mount({ myRequests: [], myInvitations: [], answering: PROJECT_INVITATIONS[0].id })
    const accept = view.getAllByTitle(t('teams.pending.accept')) as HTMLButtonElement[]
    const decline = view.getAllByTitle(t('teams.pending.decline')) as HTMLButtonElement[]
    expect(accept[0].disabled).toBe(true)
    expect(decline[0].disabled).toBe(true)
    expect(accept[1].disabled).toBe(false)
    await fireEvent.click(accept[1])
    await fireEvent.click(decline[1])
    expect(view.emitted().answerProjectInvitation).toEqual([
      [PROJECT_INVITATIONS[1], true],
      [PROJECT_INVITATIONS[1], false],
    ])
  })

  it('刷新或失败时原有行仍可见，不以空态替换已有内容', async () => {
    const view = mount({ loadingMyRequests: true, loadingMyInvitations: true, loadingProjectInvitations: true })
    expect(view.queryByText(PENDING_REQUESTS[0].team.name)).not.toBeNull()
    expect(view.queryByText(PENDING_INVITATIONS[0].team.name)).not.toBeNull()
    expect(view.queryByText('光学实验')).not.toBeNull()
    await view.rerender({ loadingMyRequests: false, failedMyRequests: true, myRequestsError: 'timeout' })
    expect(view.queryByText('timeout')).not.toBeNull()
    expect(view.queryByText(PENDING_REQUESTS[0].team.name)).not.toBeNull()
  })
})
