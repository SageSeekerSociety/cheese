<script setup lang="ts">
// 组件预览站（`/demo/catalog`），文档动态演示那一套里的第二页。
//
//   /demo/catalog          → 目录：每个组件一张卡，写着它是什么、在哪儿、独立渲染要哪几样
//   /demo/catalog/<id>     → 这个组件的那一页：一格里一个状态，喂的是真产品的形状
//
// 和 DemoView 一样不连后端、不登录、不起真路由之外的任何东西：`installDemoBackend()`
// 把会自己取数的组件（验收卡）接在演示后端的答案上。**这一页只读目录，不写任何源
// 文件** —— 想把一个新的组件放进来，改的是 `catalog.ts`。
//
// 地址照 DemoView 的做法由入口当 props 传进来（测试里换得了 props，换不了 location）。
import type { CatalogEntry } from './catalog'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { CATALOG, catalogEntry, catalogGroup, stateProps } from './catalog'
import { installCatalogAnswers } from './catalogFixtures'
import { installDemoBackend } from './demoBackend'

const props = withDefaults(defineProps<{ path?: string }>(), { path: '/' })

installDemoBackend()
installCatalogAnswers()

/** 地址里那一段：`/demo/catalog/<id>`。没有就是目录。 */
const id = computed(() => /^\/demo\/catalog\/([\w-]+)/.exec(props.path)?.[1] ?? null)
const entry = computed(() => (id.value ? catalogEntry(id.value) : null))
const missing = computed(() => id.value !== null && entry.value === null)

const NEED_LABELS: Record<string, string> = {
  vuetify: 'Vuetify',
  i18n: '语言包',
  router: '路由',
  pinia: 'store',
}

/** 目录页上的搜索：标题、说明、源码路径里出现就算。 */
const query = ref('')

/**
 * 按源码目录分组，组内按标题排。几百条平铺成一页没人找得到；目录就是这个仓库自己
 * 给组件分的类，不用另起一套。
 */
const groups = computed(() => {
  const words = query.value.trim().toLowerCase()
  const hit = (item: CatalogEntry) =>
    !words || [item.title, item.about, item.file].some((text) => text.toLowerCase().includes(words))
  const byGroup = new Map<string, CatalogEntry[]>()
  for (const item of CATALOG.filter(hit)) {
    const key = catalogGroup(item)
    byGroup.set(key, [...(byGroup.get(key) ?? []), item])
  }
  return [...byGroup.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, items]) => ({ name, items: items.sort((a, b) => a.title.localeCompare(b.title)) }))
})

const shown = computed(() => groups.value.reduce((n, group) => n + group.items.length, 0))

/** 搜索的结果说一句：读屏软件听得到「找到几个」，什么都没对上时说的也是这一句。 */
const resultText = computed(() => {
  if (!query.value.trim()) return ''
  return shown.value ? `找到 ${shown.value} 个组件` : '没有对得上的组件。'
})

/**
 * 卡片随打随筛，那一句等手停下来才换：每敲一个字就换一次，读屏软件会把一串过时的
 * 「找到几个」排着队念出来，盖在它自己的按键回显上。
 */
const RESULT_SETTLE_MS = 600
const resultNote = ref('')
let settle: ReturnType<typeof setTimeout> | undefined
watch(resultText, (text) => {
  clearTimeout(settle)
  settle = setTimeout(() => (resultNote.value = text), RESULT_SETTLE_MS)
})
onBeforeUnmount(() => clearTimeout(settle))

function needsOf(needs: string[]): string {
  return needs.length ? needs.map((n) => NEED_LABELS[n] ?? n).join(' · ') : '一件都不用装'
}
</script>

<template>
  <div class="catalog">
    <header class="catalog-head">
      <a class="catalog-back" href="/demo/catalog">组件预览站</a>
      <span class="catalog-count">{{ CATALOG.length }} 个组件</span>
      <a class="catalog-demo" href="/demo/quickstart">去看动态演示 →</a>
    </header>

    <!-- 目录：扫一眼有哪些组件、各自要什么。这一页不挂组件，挂满一屏的组件没人看。 -->
    <template v-if="!entry">
      <h1 class="catalog-name">组件目录</h1>
      <p class="catalog-lede">
        每个组件一页，用真产品的形状渲染几种状态。这一页不连后端、不登录 —— <code>pnpm dev</code> 打开
        <code>/demo/catalog</code> 就能看。加一个组件：<code>node scripts/catalog-scaffold.mjs</code> 按 props
        生成骨架，补完后放进 <code>catalog.ts</code> 或它展开的分册。
      </p>
      <input
        v-model="query"
        class="catalog-search"
        type="search"
        autocomplete="off"
        aria-label="按名字、说明或路径找组件"
        placeholder="按名字、说明或路径找"
      />
      <!-- 一直在页面上的那一格：内容变了读屏软件才会念（中途插进来的 live region 常常不念）。 -->
      <p class="catalog-result" role="status">{{ resultNote }}</p>
      <section v-for="group in groups" :key="group.name" class="catalog-group">
        <h2 class="catalog-group-name">
          <code>{{ group.name }}</code> <span>{{ group.items.length }}</span>
        </h2>
        <ul class="catalog-index">
          <li v-for="item in group.items" :key="item.id">
            <a class="catalog-card" :href="`/demo/catalog/${item.id}`">
              <span class="catalog-card-title">{{ item.title }}</span>
              <span class="catalog-card-about">{{ item.about }}</span>
              <span class="catalog-card-meta">
                <code>{{ item.file }}</code>
                <span>{{ needsOf(item.needs) }}</span>
                <span>{{ item.states.length }} 格</span>
              </span>
            </a>
          </li>
        </ul>
      </section>
    </template>

    <!-- 这个组件的那一页：一格里一个状态。 -->
    <template v-else>
      <div class="catalog-title">
        <h1 class="catalog-name">{{ entry.title }}</h1>
        <p class="catalog-about">{{ entry.about }}</p>
        <p class="catalog-card-meta">
          <code>{{ entry.file }}</code>
          <span>独立渲染需要：{{ needsOf(entry.needs) }}</span>
        </p>
      </div>
      <section v-for="(state, i) in entry.states" :key="i" class="catalog-case">
        <header class="catalog-case-head">
          <h2 class="catalog-case-name">{{ state.name }}</h2>
          <span v-if="state.needs" class="catalog-case-needs">这里只装：{{ needsOf(state.needs) }}</span>
        </header>
        <p class="catalog-case-note">{{ state.note }}</p>
        <!-- 要坐在 Vuetify 布局里的那两件（底栏、底部动作面板）本来就长在 layout 里，
             别处没有它们的位置：这里给的就是它们在产品里的那一层。 -->
        <div class="catalog-stage">
          <v-layout v-if="entry.layout">
            <component :is="entry.component" v-bind="stateProps(entry, state)">{{ state.slot }}</component>
          </v-layout>
          <component :is="entry.component" v-else v-bind="stateProps(entry, state)">{{ state.slot }}</component>
        </div>
      </section>
    </template>

    <p v-if="missing" class="catalog-missing">目录里没有这一个。回<a href="/demo/catalog">目录</a>看看。</p>
  </div>
</template>

<style scoped>
.catalog {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100vh;
  padding: 16px 24px 32px;
  overflow: auto;
  background: var(--canvas);
}

.catalog-head {
  display: flex;
  gap: 12px;
  align-items: baseline;
  flex: none;
}

.catalog-back {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  text-decoration: none;
}

.catalog-count,
.catalog-demo {
  font-size: 13px;
  color: var(--muted);
}

.catalog-demo {
  margin-left: auto;
  text-decoration: none;
}

.catalog-lede {
  max-width: 60ch;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.catalog-lede code,
.catalog-card-meta code {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}

.catalog-search {
  max-width: 360px;
  padding: 6px 10px;
  /* 16px 以下 iOS Safari 聚焦时会把整页放大。 */
  font-size: 16px;
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.catalog-search:focus-visible {
  border-color: var(--accent);
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.catalog-result {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}

/* 没在搜的时候它是空的：不占那一行，但留在无障碍树里（display: none 会让它下次不被念）。 */
.catalog-result:empty {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}

.catalog-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.catalog-group-name {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
}

.catalog-group-name code {
  font-family: var(--font-mono);
}

.catalog-index {
  display: grid;
  gap: 8px;
  /* 窄屏（360 宽的手机减去左右留白）放不下 320px 一列：最窄退到整行宽。 */
  grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr));
  padding: 0;
  margin: 0;
  list-style: none;
}

.catalog-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 100%;
  padding: 12px 14px;
  color: inherit;
  text-decoration: none;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.catalog-card:hover {
  border-color: var(--accent);
}

.catalog-card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.catalog-card-about {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.catalog-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0 12px;
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
  /* 文件路径是最长的那一格，而 flex 子项的 min-content 就是「整条路径」的宽度：
     不折断的话它会把卡片撑破（深路径那几个正好卡在断行还没到的地方）。 */
  overflow-wrap: anywhere;
}

.catalog-title {
  flex: none;
}

.catalog-name {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
  color: var(--ink);
}

.catalog-about {
  max-width: 70ch;
  margin: 0 0 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.catalog-case {
  flex: none;
}

.catalog-case-head {
  display: flex;
  gap: 12px;
  align-items: baseline;
}

.catalog-case-name {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.catalog-case-needs {
  font-size: 12px;
  color: var(--faint);
}

.catalog-case-note {
  max-width: 70ch;
  margin: 2px 0 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

/* 摆得下这一格的地方。给一块底、一条边，免得组件自己没边的时候看不出它有多宽
   （KPI 卡、页签条都是「自己不带框」的那种）。 */
.catalog-stage {
  padding: 12px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}

.catalog-missing {
  margin: 0;
  color: var(--muted);
}
</style>
