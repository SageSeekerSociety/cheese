// 刷新页面时，存着的那份项目清单在第一次导航之前就要把「短名 → 项目」记下，
// 地址守卫才不必为了换一个短名先等一次 /addresses 请求。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const me = vi.hoisted(() => ({ handle: 'alice' }))
vi.mock('@/me', () => ({ myHandle: () => me.handle, myId: () => '7' }))

import { projectUuid } from '@/lib/addresses'
import { persistProjectList } from '@/query/persist'

const KEY = 'cheesex.queries.v1'

function store(buster: string, timestamp: number, rows: unknown): void {
  sessionStorage.setItem(
    KEY,
    JSON.stringify({
      buster,
      timestamp,
      clientState: {
        mutations: [],
        queries: [
          {
            queryKey: ['projects'],
            queryHash: '["projects"]',
            state: { data: rows, dataUpdateCount: 1, dataUpdatedAt: timestamp, status: 'success' },
          },
        ],
      },
    })
  )
}

describe('persisted project list', () => {
  beforeEach(() => {
    sessionStorage.clear()
    me.handle = 'alice'
  })

  it('记下存着的那份里每个项目的短名', () => {
    store('alice', Date.now(), [
      { id: '11111111-1111-4111-8111-111111111111', slug: 'cheese-dev' },
      { id: '22222222-2222-4222-8222-222222222222', slug: 'docs' },
    ])

    persistProjectList()

    expect(projectUuid('cheese-dev')).toBe('11111111-1111-4111-8111-111111111111')
    expect(projectUuid('docs')).toBe('22222222-2222-4222-8222-222222222222')
  })

  it('换了人的那份不看', () => {
    store('bob', Date.now(), [{ id: '33333333-3333-4333-8333-333333333333', slug: 'bob-project' }])

    persistProjectList()

    expect(projectUuid('bob-project')).toBe('bob-project')
  })

  it('存得太久的那份不看', () => {
    store('alice', Date.now() - 25 * 60 * 60 * 1000, [
      { id: '44444444-4444-4444-8444-444444444444', slug: 'yesterday' },
    ])

    persistProjectList()

    expect(projectUuid('yesterday')).toBe('yesterday')
  })
})
