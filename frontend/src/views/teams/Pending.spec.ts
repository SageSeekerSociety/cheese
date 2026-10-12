import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listMyJoinRequests: vi.fn(), listMyInvitations: vi.fn(), listProjects: vi.fn(),
  cancelMyJoinRequest: vi.fn(), acceptInvitation: vi.fn(), declineInvitation: vi.fn(), respondToInvitation: vi.fn(),
}))
vi.mock('@/network/api/teams', () => ({ TeamsApi: api }))
vi.mock('@/api', () => ({ listMyInvitations: api.listProjects, respondToInvitation: api.respondToInvitation }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import Pending from './Pending.vue'
import PendingView from './PendingView.vue'

import { PENDING_INVITATIONS, PENDING_REQUESTS, PROJECT_INVITATIONS } from '@/views/demo/catalogScenesMisc3Fixtures'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
const mounted: { unmount: () => void }[] = []
function render() {
  const wrapper = shallowMount(Pending)
  mounted.push(wrapper)
  return wrapper.findComponent(PendingView)
}
beforeEach(() => {
  vi.resetAllMocks()
  api.listMyJoinRequests.mockResolvedValue({ data: { requests: PENDING_REQUESTS } })
  api.listMyInvitations.mockResolvedValue({ data: { invitations: PENDING_INVITATIONS } })
  api.listProjects.mockResolvedValue({ data: PROJECT_INVITATIONS })
})
afterEach(() => {
  mounted.splice(0).forEach((view) => view.unmount())
  vi.restoreAllMocks()
})

describe('申请与邀请容器', () => {
  it('初次读取按申请、团队邀请、项目邀请的顺序进行', async () => {
    const requests = deferred<{ data: { requests: typeof PENDING_REQUESTS } }>()
    const invitations = deferred<{ data: { invitations: typeof PENDING_INVITATIONS } }>()
    api.listMyJoinRequests.mockReturnValueOnce(requests.promise)
    api.listMyInvitations.mockReturnValueOnce(invitations.promise)
    const view = render()
    await flushPromises()
    expect(view.props('loadingMyRequests')).toBe(true)
    expect(api.listMyInvitations).not.toHaveBeenCalled()
    expect(api.listProjects).not.toHaveBeenCalled()
    requests.resolve({ data: { requests: PENDING_REQUESTS } })
    await flushPromises()
    expect(view.props('myRequests')).toEqual(PENDING_REQUESTS)
    expect(view.props('loadingMyInvitations')).toBe(true)
    expect(api.listProjects).not.toHaveBeenCalled()
    invitations.resolve({ data: { invitations: PENDING_INVITATIONS } })
    await flushPromises()
    expect(api.listProjects).toHaveBeenCalledOnce()
  })

  it('一段失败不阻断后续读取，各段重试仅刷新自己的数据', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    api.listMyJoinRequests.mockRejectedValueOnce(new Error('requests timeout'))
    const view = render()
    await flushPromises()
    expect(view.props('failedMyRequests')).toBe(true)
    expect(view.props('myRequestsError')).toBe('requests timeout')
    expect(view.props('projectInvitations')).toEqual(PROJECT_INVITATIONS)
    view.vm.$emit('retryRequests')
    await flushPromises()
    expect(view.props('failedMyRequests')).toBe(false)
    expect(api.listMyInvitations).toHaveBeenCalledOnce()
    view.vm.$emit('retryInvitations')
    await flushPromises()
    expect(api.listMyInvitations).toHaveBeenCalledTimes(2)
    view.vm.$emit('retryProjectInvitations')
    await flushPromises()
    expect(api.listProjects).toHaveBeenCalledTimes(2)
  })

  it('取消、接受与拒绝走各自接口，并重取对应团队列表', async () => {
    const view = render()
    await flushPromises()
    view.vm.$emit('cancelRequest', 1)
    await flushPromises()
    expect(api.cancelMyJoinRequest).toHaveBeenCalledWith(1)
    expect(api.listMyJoinRequests).toHaveBeenCalledTimes(2)
    view.vm.$emit('acceptInvitation', 2)
    await flushPromises()
    view.vm.$emit('declineInvitation', 3)
    await flushPromises()
    expect(api.acceptInvitation).toHaveBeenCalledWith(2)
    expect(api.declineInvitation).toHaveBeenCalledWith(3)
    expect(api.listMyInvitations).toHaveBeenCalledTimes(3)
  })

  it.each([true, false])('项目答复 %s 在答复和刷新期间保持 answering，并保留旧行', async (accept) => {
    const response = deferred<void>()
    const refresh = deferred<{ data: typeof PROJECT_INVITATIONS }>()
    const view = render()
    await flushPromises()
    api.respondToInvitation.mockReturnValueOnce(response.promise)
    api.listProjects.mockReturnValueOnce(refresh.promise)
    view.vm.$emit('answerProjectInvitation', PROJECT_INVITATIONS[0], accept)
    await flushPromises()
    expect(api.respondToInvitation).toHaveBeenCalledWith(PROJECT_INVITATIONS[0].id, accept)
    expect(view.props('answering')).toBe(PROJECT_INVITATIONS[0].id)
    response.resolve()
    await flushPromises()
    expect(view.props('loadingProjectInvitations')).toBe(true)
    expect(view.props('answering')).toBe(PROJECT_INVITATIONS[0].id)
    expect(view.props('projectInvitations')).toEqual(PROJECT_INVITATIONS)
    refresh.resolve({ data: [] })
    await flushPromises()
    expect(view.props('answering')).toBeNull()
    expect(view.props('projectInvitations')).toEqual([])
  })
})
