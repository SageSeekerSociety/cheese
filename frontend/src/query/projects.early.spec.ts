// 冷打开后的第一次读项目清单：启动脚本（index.html）已经替应用问过一次。守的规矩：那次的
// 答复只在它是用现在这个登录问到的时候才算数，只算一次；拿不到就照常自己问。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjects = vi.fn()
vi.mock('@/api', () => ({ listProjects: (...args: unknown[]) => listProjects(...args) }))

const P1 = { id: 'p1', slug: 'one' }
const ASKED = { id: 'asked', slug: 'asked' }

async function firstRead() {
  vi.resetModules()
  const { queryClient } = await import('@/query/client')
  const { projectsQuery } = await import('./projects')
  queryClient.clear()
  return (await queryClient.fetchQuery(projectsQuery())).map((p) => p.id)
}

function earlyAnswer(token: string, body: unknown) {
  window.__cxEarlyProjects = Promise.resolve({ token, body })
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('accessToken', 'live')
  listProjects.mockReset()
  listProjects.mockResolvedValue({ data: [ASKED] })
})

afterEach(() => {
  delete window.__cxEarlyProjects
})

describe('the first read of the project list after a cold open', () => {
  it('takes what the boot script was told instead of asking again', async () => {
    earlyAnswer('live', { code: 200, data: { data: [P1] } })
    expect(await firstRead()).toEqual(['p1'])
    expect(listProjects).not.toHaveBeenCalled()
  })

  it('takes it only once: the next read asks the server', async () => {
    earlyAnswer('live', { code: 200, data: { data: [P1] } })
    await firstRead()
    expect(await firstRead()).toEqual(['asked'])
  })

  it('asks itself when the answer was for a sign-in that is no longer this one', async () => {
    earlyAnswer('someone-else', { code: 200, data: { data: [P1] } })
    expect(await firstRead()).toEqual(['asked'])
  })

  it('asks itself when the boot script got no list', async () => {
    window.__cxEarlyProjects = Promise.resolve(null)
    expect(await firstRead()).toEqual(['asked'])
    earlyAnswer('live', { code: 500, message: 'boom' })
    expect(await firstRead()).toEqual(['asked'])
  })

  it('asks itself when the boot script asked nothing', async () => {
    expect(await firstRead()).toEqual(['asked'])
  })
})
