// 「全部改动」那一面分批挂段：先挂够一屏，其余的等浏览器空下来再一批批补。
//
// 要钉住的是**代码走到了哪条路**：首屏只挂前几段（不是几十段全建），补完之后每一段
// 都在 DOM 里（不虚拟化），点到还没挂上的那一段时先把目标补出来再滚。
import type { Component } from 'vue'
import type { FileDiff } from '../../lib/diff'

import { defineComponent, h, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import ChangesDiffList from './ChangesDiffList.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const vuetify = createVuetify({ components, directives })
const global = { plugins: [vuetify, i18n] }

/** 一份 `lines` 行的差异：段头和上下文都有，形状和真 git 输出一样。 */
function diffOf(path: string, lines: number): FileDiff {
  const body = [
    `diff --git a/${path} b/${path}`,
    'index 0000000..1111111 100644',
    `--- a/${path}`,
    `+++ b/${path}`,
    `@@ -1,${lines} +1,${lines} @@`,
    ...Array.from({ length: lines }, (_, i) => `+const line${i} = ${i}`),
  ].join('\n')
  return { path, added: lines, removed: 0, status: 'modified', body }
}

const mountedPaths = () => Array.from(document.querySelectorAll<HTMLElement>('.diff-file')).map((el) => el.dataset.path)

function mount(diffs: FileDiff[]) {
  return render(ChangesDiffList as unknown as Component, { props: { diffs }, global })
}

describe('改动列表分批挂', () => {
  it('首屏只挂够一屏的几段，其余补完以后都在 DOM 里', async () => {
    // 每段 100 行：头一批 600 行，也就是 6 段。
    const diffs = Array.from({ length: 20 }, (_, i) => diffOf(`src/f${i}.ts`, 100))
    mount(diffs)
    await nextTick()
    const first = mountedPaths()
    expect(first.length).toBeGreaterThan(0)
    expect(first.length).toBeLessThan(diffs.length)
    expect(first).toEqual(diffs.slice(0, first.length).map((d) => d.path))

    // 浏览器空下来以后一批批补，最后一段都不少（不是虚拟化）。补的每一批要等浏览器
    // 空闲（`whenIdle` 最长 1500ms 兜底），所以这里的期限要放宽。
    await waitFor(() => expect(mountedPaths()).toHaveLength(diffs.length), { timeout: 15_000 })
    expect(mountedPaths()).toEqual(diffs.map((d) => d.path))
  })

  it('段少的时候一次挂完，不分批', async () => {
    const diffs = [diffOf('src/a.ts', 3), diffOf('src/b.ts', 4)]
    mount(diffs)
    await nextTick()
    expect(mountedPaths()).toEqual(['src/a.ts', 'src/b.ts'])
  })

  it('滚到还没挂上的一段：先把它补出来，再滚过去', async () => {
    const diffs = Array.from({ length: 12 }, (_, i) => diffOf(`src/f${i}.ts`, 100))
    const list = ref<{ scrollTo: (path: string) => Promise<void> } | null>(null)
    const Host = defineComponent({
      setup: () => () => h(ChangesDiffList as unknown as Component, { ref: list, diffs }),
    })
    render(Host, { global })
    await nextTick()
    expect(mountedPaths()).not.toContain('src/f11.ts')

    await list.value!.scrollTo('src/f11.ts')
    await nextTick()
    // 目标和它前面的都在了，滚的时候它已经挂在 DOM 里。
    expect(mountedPaths()).toContain('src/f11.ts')
    expect(mountedPaths()).toContain('src/f0.ts')
  })
})
