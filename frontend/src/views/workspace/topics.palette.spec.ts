// 命令面板的「等你处理」组只收真在等这个人动手的话题。
//
// 后端的 `awaits_me` 数的是「还挂着没结的卡、没答的决策请求」，不看归档状态：一个
// 已经归档的房间，里面那张卡也没人再去结，于是 `awaits_me` 仍然为真。可房间收了尾，
// 面板不该再把它列进「等你处理」，行尾也不该说「等你处理」——那张卡不是要人现在去
// 点它。这里从面板的数据源一头守住这条：归档的话题不认 `awaits_me`。
import type { Router } from 'vue-router'
import type { SourceContext } from '@/commands/palette/sources'
import type { Topic } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'

import source from './topics.palette'

import { buildResults } from '@/commands/palette/results'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

function topic(id: string, title: string, extra: Partial<Topic> = {}): Topic {
  return {
    id,
    project_id: 'p1',
    title,
    kind: 'topic',
    status: 'active',
    created_at: '',
    ...extra,
  } as Topic
}

// 本地数据源只从 store 里读当前项目那张表；路由这里用不到（只取条目，不点它）。
const ctx: SourceContext = { projectId: 'p1', router: {} as unknown as Router }

function mountStore(topics: Topic[]) {
  setActivePinia(createPinia())
  const store = useWorkspaceStore()
  store.projectId = 'p1'
  store.topics = topics
  return store
}

describe('命令面板：等你处理的话题', () => {
  it('归档的话题不进「等你处理」，即使后端仍说它 awaits_me', () => {
    mountStore([
      topic('t1', '登录页改成深色', { awaits_me: true }),
      topic('t2', '平台部分问题记录', { awaits_me: true, status: 'archived' }),
    ])

    const items = source.items!(ctx)
    expect(items.find((item) => item.id === 'topic:t2')?.awaiting).toBeFalsy()
    expect(items.find((item) => item.id === 'topic:t1')?.awaiting).toBe(true)

    // 不打字时面板把 awaiting 的排成最上面那一组：只有非归档的那一条。
    const awaiting = buildResults('', [source], ctx, []).find((group) => group.key === 'awaiting')
    expect(awaiting?.rows.map((row) => row.id)).toEqual(['topic:t1'])
  })

  it('归档的话题行尾只说「已归档」，不说「等你处理」', () => {
    mountStore([topic('t2', '平台部分问题记录', { awaits_me: true, status: 'archived' })])

    const item = source.items!(ctx)[0]
    expect(item.badge?.text).toBe(t('navigation.palette.archived'))
    expect(item.badge?.text).not.toBe(t('navigation.palette.awaiting'))
    // 已归档不是「要你动手」，不着暖色。
    expect(item.badge?.tone).toBeUndefined()
  })

  it('归档的话题名字仍搜得到——只是不排进「等你处理」，不是从面板里消失', () => {
    mountStore([topic('t2', '平台部分问题记录', { awaits_me: true, status: 'archived' })])

    const rows = buildResults('平台', [source], ctx, []).flatMap((group) => group.rows)
    expect(rows.find((row) => row.id === 'topic:t2')).toBeTruthy()
  })
})
