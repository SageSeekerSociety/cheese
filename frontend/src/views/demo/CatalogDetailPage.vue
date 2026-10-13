<script setup lang="ts">
// 单组件页：三栏 —— 站的左栏、这一页的内容、右边的「本页内容」。
//
// 工具条粘在顶上：往下翻到第 4 格的时候还能就地把它切到手机宽，不用翻回页头。切的是
// 这一页所有格子（主题和动效是整篇文档一档，见 DemoCatalog.vue），设备档各有各的框。
import type { CatalogEntry, CatalogState } from './catalog'
import type { BackdropId, DeviceId, ThemeId } from './demoCatalogStudio'

import { computed, ref } from 'vue'

import { stateProps } from './catalog'
import CatalogStage from './CatalogStage.vue'
import { DEVICES, needsOf, useStudio } from './demoCatalogStudio'
import { MOTION_MODES } from './demoMotion'

const props = defineProps<{ entry: CatalogEntry }>()

const studio = useStudio()

const THEMES: ReadonlyArray<{ id: ThemeId; label: string }> = [
  { id: 'light', label: '浅色' },
  { id: 'dark', label: '深色' },
]

const BACKDROPS: ReadonlyArray<{ id: BackdropId; label: string }> = [
  { id: 'surface', label: '表面' },
  { id: 'canvas', label: '画布' },
]

/** 每一格各记各的重放次数：重放第 3 格不该让第 1 格也重新挂一遍。 */
const replays = ref<Record<number, number>>({})

function bump(index: number): void {
  replays.value = { ...replays.value, [index]: (replays.value[index] ?? 0) + 1 }
}

/** 挑一档设备就退出并排；并排是第五档，不是某一台设备。 */
function pickDevice(id: DeviceId | 'dual'): void {
  studio.dual = id === 'dual'
  if (id !== 'dual') studio.device = id
}

/**
 * 参数里可能有函数、DOM 节点、互相指着的对象：`JSON.stringify` 要么抛，要么把一坨
 * 引用关系打满整屏。这三种各自换成一句话，剩下的照常给人看。
 */
function safeJson(value: unknown): string {
  // 认环靠的是**当前这条祖先链**，不是「这个对象见过没有」：同一个对象挂在两个字段下
  // （不是环，只是共用）是正常的，按「见过」判会把它第二次写成「循环引用」，参数表里
  // 显示的就不是真参数了。`JSON.stringify` 不给「离开一层」的回调，但每调用一次替换函数，
  // `this` 就是此刻在遍历的那个对象 —— 靠它把栈收敛回当前这一条链。
  const chain: unknown[] = []
  try {
    return JSON.stringify(
      value,
      function (this: unknown, _key: string, item: unknown) {
        if (typeof item === 'function') return '[函数]'
        if (item instanceof HTMLElement) return '[DOM 节点]'
        if (typeof item === 'object' && item !== null) {
          while (chain.length && chain[chain.length - 1] !== this) chain.pop()
          if (chain.includes(item)) return '[循环引用]'
          chain.push(item)
        }
        return item
      },
      2
    )
  } catch {
    return '（这一格的参数没法序列化，看 catalog.ts 里那一格）'
  }
}

function argsOf(state: CatalogState): string {
  return safeJson(stateProps(props.entry, state))
}

const cases = computed(() => props.entry.states.map((state, i) => ({ state, id: `case-${i}`, index: i })))
</script>

<template>
  <article class="doc">
    <div class="doc-main">
      <nav class="crumb" aria-label="位置">
        <a class="crumb-link" href="/demo/catalog">组件目录</a>
        <span class="crumb-sep" aria-hidden="true">/</span>
        <code class="crumb-file">{{ entry.file }}</code>
      </nav>

      <h1 class="catalog-name t-page-title">{{ entry.title }}</h1>
      <p class="catalog-about">{{ entry.about }}</p>
      <p class="catalog-card-meta">
        <span>独立渲染需要：{{ needsOf(entry.needs) }}</span>
        <span>{{ entry.states.length }} 格</span>
      </p>

      <!-- 粘在顶上那一条：往下翻的时候还能就地换档。窄屏上每一段各自换行，不挤成一条。 -->
      <div class="ptool">
        <div class="ptool-group" role="group" aria-label="主题">
          <span class="ptool-label">主题</span>
          <button
            v-for="mode in THEMES"
            :key="mode.id"
            type="button"
            class="ptool-btn"
            :aria-pressed="studio.theme === mode.id"
            @click="studio.theme = mode.id"
          >
            {{ mode.label }}
          </button>
        </div>

        <div class="ptool-group" role="group" aria-label="设备">
          <span class="ptool-label">设备</span>
          <button
            v-for="device in DEVICES"
            :key="device.id"
            type="button"
            class="ptool-btn"
            :aria-pressed="!studio.dual && studio.device === device.id"
            @click="pickDevice(device.id)"
          >
            {{ device.label }}
          </button>
          <button type="button" class="ptool-btn" :aria-pressed="studio.dual" @click="pickDevice('dual')">
            双端并排
          </button>
        </div>

        <div class="ptool-group" role="group" aria-label="底色">
          <span class="ptool-label">底色</span>
          <button
            v-for="backdrop in BACKDROPS"
            :key="backdrop.id"
            type="button"
            class="ptool-btn"
            :aria-pressed="studio.bg === backdrop.id"
            @click="studio.bg = backdrop.id"
          >
            {{ backdrop.label }}
          </button>
        </div>

        <div class="ptool-group" role="group" aria-label="动效">
          <span class="ptool-label">动效</span>
          <button
            v-for="mode in MOTION_MODES"
            :key="mode.id"
            type="button"
            class="ptool-btn"
            :aria-pressed="studio.motion === mode.id"
            @click="studio.motion = mode.id"
          >
            {{ mode.label }}
          </button>
        </div>
      </div>

      <section v-for="item in cases" :id="item.id" :key="item.id" class="catalog-case">
        <header class="catalog-case-head">
          <h2 class="catalog-case-name">{{ item.state.name }}</h2>
          <span class="catalog-case-index" aria-hidden="true">#{{ item.index + 1 }}</span>
          <span v-if="item.state.needs" class="catalog-case-needs">这里只装：{{ needsOf(item.state.needs) }}</span>
          <button type="button" class="case-replay" @click="bump(item.index)">重放这一格</button>
        </header>
        <p class="catalog-case-note">{{ item.state.note }}</p>
        <CatalogStage :entry="entry" :state="item.state" :index="item.index" :replay="replays[item.index] ?? 0" />
        <details class="case-args">
          <summary class="case-args-sum">这一格的参数</summary>
          <pre class="case-args-body">{{ argsOf(item.state) }}</pre>
        </details>
      </section>
    </div>

    <aside class="doc-toc" aria-label="本页内容">
      <p class="doc-toc-title">本页内容</p>
      <ul class="doc-toc-list">
        <li v-for="item in cases" :key="item.id">
          <a class="doc-toc-link" :href="`#${item.id}`">{{ item.state.name }}</a>
        </li>
      </ul>
    </aside>
  </article>
</template>

<style scoped>
.doc {
  display: grid;
  gap: 32px;
  grid-template-columns: minmax(0, 1fr) 196px;
  padding: 16px 24px 48px;
}

.doc-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.crumb {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin: 0 0 8px;
  font-size: 13px;
}

.crumb-link {
  color: var(--accent-ink);
  text-decoration: none;
}

.crumb-link:hover {
  text-decoration: underline;
}

.crumb-sep,
.crumb-file {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}

/* 字号和字重走 `.t-page-title`（23px 那一档）：手写 20px 不在设计系统的字号阶梯上，
   而这一页的标题就是这一页的页面标题。 */
.catalog-name {
  margin: 0 0 4px;
}

.catalog-about {
  max-width: 70ch;
  margin: 0 0 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.catalog-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0 12px;
  margin: 0 0 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
  overflow-wrap: anywhere;
}

/* 粘在滚动的那一栏顶上：底下的格子从它下面过。 */
.ptool {
  display: flex;
  position: sticky;
  top: 0;
  z-index: 2;
  padding: 8px 0;
  margin-bottom: 12px;
  background: var(--canvas);
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
}

.ptool-group {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.ptool-label {
  font-size: 12px;
  color: var(--faint);
}

.ptool-btn {
  padding: 3px 10px;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
}

.ptool-btn:hover {
  color: var(--ink);
  border-color: var(--accent);
}

.ptool-btn[aria-pressed='true'] {
  color: var(--accent-ink);
  background: var(--accent-wash);
  border-color: var(--accent);
}

.catalog-case {
  display: flex;
  flex-direction: column;
}

.catalog-case-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
}

.catalog-case-name {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.catalog-case-index {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}

.catalog-case-needs {
  font-size: 12px;
  color: var(--faint);
}

.case-replay {
  padding: 3px 10px;
  margin-left: auto;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
}

.case-replay:hover {
  color: var(--ink);
  border-color: var(--accent);
}

.catalog-case-note {
  max-width: 70ch;
  margin: 2px 0 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.case-args {
  margin-top: 8px;
  font-size: 12px;
  color: var(--muted);
}

.case-args-sum {
  cursor: pointer;
}

.case-args-body {
  max-height: 320px;
  padding: 10px 12px;
  margin: 8px 0 0;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--text);
  background: var(--fill-2);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.doc-toc {
  position: sticky;
  top: 16px;
  align-self: start;
}

.doc-toc-title {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--faint);
}

.doc-toc-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.doc-toc-link {
  display: block;
  padding: 4px 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-decoration: none;
  border-radius: var(--radius-sm);
}

.doc-toc-link:hover {
  color: var(--ink);
  background: var(--fill);
}

/* 窄下来就一栏：右边那一列收掉。工具条自己会换行，不横向溢出。 */
@media (width <= 900px) {
  .doc {
    grid-template-columns: minmax(0, 1fr);
    padding: 16px 16px 40px;
  }

  .doc-toc {
    display: none;
  }
}
</style>
