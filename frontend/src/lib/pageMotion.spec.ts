// 手机上换页的方向：往里走一层新页从右边来，退回一层从左边来，平级只淡入。
// 「一层」是路由声明的上一层，不是浏览器历史——点 ← 回去在历史里是前进。
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { pageMotion } from './pageMotion'

const blank = { template: '<div />' }

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/work', name: 'work', component: blank },
      { path: '/inbox', name: 'inbox', component: blank },
      { path: '/projects/:projectId', name: 'topics', component: blank },
      { path: '/projects/:projectId/topics/:topicId', name: 'topic', component: blank, meta: { backTo: 'topics' } },
      { path: '/projects/:projectId/members', name: 'members', component: blank, meta: { backTo: 'topics' } },
      { path: '/projects/:projectId/dm/:peer', name: 'dm', component: blank, meta: { backTo: 'members' } },
    ],
  })
}

async function motion(fromPath: string | null, toPath: string) {
  const router = makeRouter()
  if (fromPath) await router.push(fromPath)
  const from = router.currentRoute.value
  const to = router.resolve(toPath)
  return pageMotion(to, from, router)
}

describe('pageMotion', () => {
  it('第一次打开不动', async () => {
    expect(await motion(null, '/work')).toBeNull()
  })

  it('话题列表进话题是往里走，退回来是往外走', async () => {
    expect(await motion('/projects/p/', '/projects/p/topics/t')).toBe('forward')
    expect(await motion('/projects/p/topics/t', '/projects/p')).toBe('back')
  })

  it('深几层都按声明的上一层数', async () => {
    expect(await motion('/projects/p/topics/t', '/projects/p/dm/bob')).toBe('forward')
    expect(await motion('/projects/p/dm/bob', '/projects/p/members')).toBe('back')
  })

  it('底栏换一格、同一层换一页是平级', async () => {
    expect(await motion('/work', '/inbox')).toBe('fade')
    expect(await motion('/projects/p/topics/t', '/projects/p/topics/u')).toBe('fade')
  })

  it('只改 query 或 hash 不算换页', async () => {
    expect(await motion('/projects/p/topics/t', '/projects/p/topics/t?tab=changes')).toBeNull()
    expect(await motion('/work', '/work#top')).toBeNull()
  })
})
