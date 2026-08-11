import { describe, expect, it } from 'vitest'

import {
  autosavePaused,
  docSaveStatus,
  dropStash,
  planExternalUpdate,
  planSourceModeEntry,
  popStash,
  pushStash,
} from './docEditState'

// The file on disk uses syntax the visual editor mangles (that's what makes a
// doc "lossy"); FROM_EDITOR is what the visual editor would write back.
const ON_DISK = '# 标题\n\n<details>\n<summary>折叠</summary>\n\n内容\n\n</details>\n'
const FROM_EDITOR = '# 标题\n\n折叠\n\n内容\n'
const USER_EDIT = '# 标题\n\n折叠\n\n内容\n\n用户新写的一段\n'

describe('进入源码模式：绝不静默丢内容', () => {
  it('干净文档：源码显示磁盘原文', () => {
    const r = planSourceModeEntry({ lossy: false, dirty: false, rawDoc: ON_DISK, visualMarkdown: FROM_EDITOR })
    expect(r.draft).toBe(ON_DISK)
    expect(r.dirty).toBe(false)
    expect(r.stashed).toBeNull()
  })

  it('非 lossy 且有未保存改动：改动直接带进源码模式', () => {
    const r = planSourceModeEntry({ lossy: false, dirty: true, rawDoc: FROM_EDITOR, visualMarkdown: USER_EDIT })
    expect(r.draft).toBe(USER_EDIT)
    expect(r.dirty).toBe(true)
    expect(r.stashed).toBeNull()
  })

  // 这条钉住 P0-6 最刺眼的症状：「确认覆盖保存？」对话框里推荐按钮「用源码模式」
  // 调的就是这条路径，旧实现把用户改动直接扔了。
  it('lossy 且有未保存改动：磁盘原文进编辑器，用户改动必须被交回而不是丢弃', () => {
    const r = planSourceModeEntry({ lossy: true, dirty: true, rawDoc: ON_DISK, visualMarkdown: USER_EDIT })
    // 源码视图必须是磁盘原文——否则逃生口本身就把不支持的语法毁了
    expect(r.draft).toBe(ON_DISK)
    // 用户的改动一个字都不能消失
    expect(r.stashed).toBe(USER_EDIT)
    expect(r.stashed).toContain('用户新写的一段')
    // 编辑器内容 == 磁盘，所以此刻不脏
    expect(r.dirty).toBe(false)
  })

  it('lossy 但没有未保存改动：无需暂存', () => {
    const r = planSourceModeEntry({ lossy: true, dirty: false, rawDoc: ON_DISK, visualMarkdown: FROM_EDITOR })
    expect(r.draft).toBe(ON_DISK)
    expect(r.stashed).toBeNull()
  })

  it('任何情况下，两份内容至少有一份留在手里', () => {
    for (const lossy of [true, false]) {
      for (const dirty of [true, false]) {
        const r = planSourceModeEntry({ lossy, dirty, rawDoc: ON_DISK, visualMarkdown: USER_EDIT })
        const kept = [r.draft, r.stashed].filter(Boolean)
        if (dirty) expect(kept).toContain(USER_EDIT)
        expect(kept.length).toBeGreaterThan(0)
      }
    }
  })
})

describe('自动保存暂停时 UI 不许谎报', () => {
  const base = {
    loading: false,
    saving: false,
    dirty: false,
    lossy: false,
    sourceMode: false,
    editable: true,
    savedAt: null,
  }

  it('lossy + 可视化模式 + 有改动 = 暂停，不是「编辑中」', () => {
    expect(docSaveStatus({ ...base, dirty: true, lossy: true })).toBe('paused')
    expect(autosavePaused({ dirty: true, lossy: true, sourceMode: false, editable: true })).toBe(true)
  })

  it('源码模式下自动保存照常，不算暂停', () => {
    expect(docSaveStatus({ ...base, dirty: true, lossy: true, sourceMode: true })).toBe('dirty')
    expect(autosavePaused({ dirty: true, lossy: true, sourceMode: true, editable: true })).toBe(false)
  })

  it('普通文档有改动就是「编辑中」', () => {
    expect(docSaveStatus({ ...base, dirty: true })).toBe('dirty')
  })

  it('只读态下仍有未保存改动 = 暂停（自动保存不会跑）', () => {
    expect(autosavePaused({ dirty: true, lossy: false, sourceMode: false, editable: false })).toBe(true)
    expect(autosavePaused({ dirty: false, lossy: false, sourceMode: false, editable: false })).toBe(false)
  })

  it('没有改动就没有暂停', () => {
    expect(autosavePaused({ dirty: false, lossy: true, sourceMode: false, editable: true })).toBe(false)
    expect(docSaveStatus({ ...base, lossy: true, savedAt: 1 })).toBe('saved')
  })

  it('加载/保存中优先于一切', () => {
    expect(docSaveStatus({ ...base, loading: true, dirty: true, lossy: true })).toBe('loading')
    expect(docSaveStatus({ ...base, saving: true, dirty: true, lossy: true })).toBe('saving')
  })
})

describe('暂存区：第二次暂存不许顶掉第一次', () => {
  const A = '改动 A\n'
  const B = '改动 B\n'

  it('连续两次暂存都留着', () => {
    let stack = pushStash([], A)
    stack = pushStash(stack, B)
    expect(stack).toEqual([A, B])
  })

  it('重复暂存同一份不叠加', () => {
    expect(pushStash(pushStash([], A), A)).toEqual([A])
  })

  it('恢复是交换：屏幕上那份自动回到暂存区，不会被顶掉', () => {
    const r = popStash([A], B, ON_DISK)
    expect(r.restored).toBe(A)
    expect(r.stack).toEqual([B])
  })

  it('屏幕上就是磁盘原文时，恢复不留垃圾', () => {
    const r = popStash([A], ON_DISK, ON_DISK)
    expect(r.restored).toBe(A)
    expect(r.stack).toEqual([])
  })

  it('多份暂存逐份恢复，全程不丢', () => {
    let stack = pushStash(pushStash([], A), B)
    const first = popStash(stack, ON_DISK, ON_DISK)
    expect(first.restored).toBe(B)
    stack = first.stack
    const second = popStash(stack, B, ON_DISK)
    expect(second.restored).toBe(A)
    expect(second.stack).toEqual([B])
  })

  it('空暂存区恢复是安全的空操作', () => {
    expect(popStash([], A, ON_DISK)).toEqual({ restored: null, stack: [] })
  })

  it('丢弃只丢最上面那份', () => {
    expect(dropStash([A, B])).toEqual([A])
    expect(dropStash([])).toEqual([])
  })
})

describe('外部更新 vs 本地脏改动：两边都不能丢', () => {
  it('磁盘没变：忽略', () => {
    expect(planExternalUpdate({ dirty: true, incoming: ON_DISK, rawDoc: ON_DISK })).toBe('ignore')
    expect(planExternalUpdate({ dirty: false, incoming: ON_DISK, rawDoc: ON_DISK })).toBe('ignore')
  })

  it('本地干净：直接载入磁盘新版本', () => {
    expect(planExternalUpdate({ dirty: false, incoming: USER_EDIT, rawDoc: ON_DISK })).toBe('install')
  })

  // 旧实现在这里直接 return——芝士的更新被静默丢弃，面板与磁盘从此永久分叉。
  it('本地有未保存改动 + 磁盘被芝士改了：必须报冲突，不能任一边静默胜出', () => {
    expect(planExternalUpdate({ dirty: true, incoming: USER_EDIT, rawDoc: ON_DISK })).toBe('conflict')
  })

  it('冲突解决后（本地版本已落盘）不再重复报冲突', () => {
    // 用户选了「用我的版本覆盖」：rawDoc 变成本地版本，下一次活动拉取拿到同一份
    expect(planExternalUpdate({ dirty: false, incoming: USER_EDIT, rawDoc: USER_EDIT })).toBe('ignore')
  })
})
