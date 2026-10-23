/**
 * 拆分的机械验收：这一册出去的名字一条不多一条不少，十条在整份目录里的先后一个没变。
 *
 * 为什么值一条测试：这次改动的全部主张就是「名字、内容和顺序一个没动」。手工核一遍只能
 * 证明今天这一次；下一次有人把三份分卷合起来的先后换个个儿、或者从门面顺手多导出一个名
 * 字，别的测试都不会红 —— `catalog.spec.ts` 挂的是条目本身，它不管这些条目从哪一册来、
 * 按什么顺序排。
 */
import { describe, expect, it } from 'vitest'

import { CATALOG } from './catalog'
import { VIEW_SETTINGS_WORKSPACE_ENTRIES } from './catalogViewSettingsWorkspace'
import * as facade from './catalogViewSettingsWorkspace'

/** 这十条原来内联在 `catalogViewSettingsWorkspace.ts` 里，拆成分卷之后一条都不能少、也不能换地方。 */
const IDS = [
  'settings-general-view',
  'notifications-view',
  'profile-view',
  'real-name-view',
  'security-view',
  'team-agent-picker',
  'model-connections-view',
  'this-device-view',
  'channel-browse-view',
  'project-tasks-view',
  'project-overview-view',
]

describe('账号设置与项目工作台那一册', () => {
  it('出去的就是同样那十条，一条不多一条不少，先后也没动', () => {
    expect(VIEW_SETTINGS_WORKSPACE_ENTRIES.map((entry) => entry.id)).toEqual(IDS)
  })

  it('门面上出去的值就这一个', () => {
    // 类型在运行时不存在，这里说的是值的面：拆之前这个文件也只导出这一个值。
    expect(Object.keys(facade).sort()).toEqual(['VIEW_SETTINGS_WORKSPACE_ENTRIES'])
  })

  it('这十条在目录里整册连着排，中间没插进别的', () => {
    const ids = CATALOG.map((entry) => entry.id)
    const at = ids.indexOf(IDS[0])
    expect(at, '这一册的第一条不在 CATALOG 里').toBeGreaterThanOrEqual(0)
    expect(ids.slice(at, at + IDS.length), '这一册没连着排').toEqual(IDS)
  })
})
