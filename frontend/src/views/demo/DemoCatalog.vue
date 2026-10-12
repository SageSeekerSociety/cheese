<script setup lang="ts">
// 组件预览站（`/demo/catalog`）的壳：顶栏 + 常驻左栏 + 窄屏抽屉。底下的每一页各是
// 一个文件：
//
//   /demo/catalog                 → 目录：每个组件一张卡，卡上一小格活预览
//   /demo/catalog/<id>            → 这个组件那一页：一格里一个状态
//   /demo/catalog/<id>/frame/<n>  → 上面那一页里 iframe 取的那一格（不带这个壳）
//
// 和 DemoView 一样不连后端、不登录：`installDemoBackend()` 把会自己取数的组件（验收
// 卡）接在演示后端的答案上。**这里只读目录，不写任何源文件** —— 想把新组件放进来，
// 改的是 `catalog.ts` 或它展开的分册。
//
// 地址照 DemoView 的做法由入口当 props 传进来（测试里换得了 props，换不了 location）。
import type { BackdropId } from './demoCatalogStudio'
import type { MotionMode } from './demoMotion'

import { computed, onBeforeUnmount, provide, reactive, ref, watch } from 'vue'
import { useDisplay, useTheme } from 'vuetify'

import { CATALOG, catalogEntry, catalogGroup } from './catalog'
import CatalogDetailPage from './CatalogDetailPage.vue'
import { installCatalogAnswers } from './catalogFixtures'
import CatalogFramePage from './CatalogFramePage.vue'
import CatalogIndexPage from './CatalogIndexPage.vue'
import CatalogRail from './CatalogRail.vue'
import { installDemoBackend } from './demoBackend'
import { defaultPrefs, groupCatalog, parseCatalogPath, STUDIO } from './demoCatalogStudio'
import { applyMotion } from './demoMotion'

import { applyResolvedTheme } from '@/theme'

const props = withDefaults(defineProps<{ path?: string; search?: string }>(), { path: '/', search: '' })

installDemoBackend()
installCatalogAnswers()

const route = ref(parseCatalogPath(props.path))
watch(
  () => props.path,
  (path) => {
    route.value = parseCatalogPath(path)
    drawer.value = false
  }
)

const id = computed(() => route.value.id)
const entry = computed(() => (id.value === null ? null : catalogEntry(id.value)))
const missing = computed(() => id.value !== null && entry.value === null)

/** iframe 里要画的那一格：地址上给了第几格，就画第几格。 */
const frameState = computed(() => {
  const index = route.value.frame
  if (index === null || !entry.value) return null
  return entry.value.states[index] ?? null
})

/** iframe 那一页只看地址上给的这几样：动效和底在它自己的文档里生效。 */
const frameQuery = computed<{ motion: MotionMode; bg: BackdropId }>(() => {
  const params = new URLSearchParams(props.search)
  const motion = params.get('motion')
  return {
    motion: motion === 'slow' || motion === 'off' ? motion : 'normal',
    bg: params.get('bg') === 'canvas' ? 'canvas' : 'surface',
  }
})

/** 预览偏好：顶栏和工具条改的是同一份。 */
const studio = reactive(defaultPrefs())
provide(STUDIO, studio)

/**
 * 主题落在文档自己的 `<html>` 上，不是某一格舞台上：`style.css` 的深色选择器是
 * `:root[data-theme='dark']`，给里面某个 div 加属性，深色那套 token 根本轮不到它。
 * iframe 里那几格是另外写的文档，靠地址上的 `?theme=` 各自落地。
 */
const theme = useTheme()
watch(
  () => studio.theme,
  (mode) => {
    applyResolvedTheme(mode)
    // `theme.global.name.value = …` 是旧写法，Vuetify 会就此报一条 deprecation：这里
    // 每一页都挂一遍，报出来就是满屏噪音。`change()` 是它指的那一个。
    theme.change(mode)
  },
  { immediate: true }
)

/**
 * 动效也是整篇文档一档。慢放改的是产品那几条 `--dur-*`（见 demoMotion.ts），不另写
 * 一套时长；iframe 里那几格靠地址上的 `?motion=` 各自落地。
 */
watch(
  () => studio.motion,
  (mode) => applyMotion(document.documentElement, mode),
  { immediate: true }
)

/** 目录页上的搜索：标题、说明、源码路径里出现就算。 */
const query = ref('')

const groups = computed(() => groupCatalog(CATALOG, query.value, catalogGroup))
/** 左栏永远列全份：搜出来几条的时候，别处还有什么是不能丢的。 */
const allGroups = computed(() => groupCatalog(CATALOG, '', catalogGroup))
const shown = computed(() => groups.value.reduce((total, group) => total + group.items.length, 0))

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

/** 窄屏那一下抽屉：宽屏上左栏一直在，这个开关在宽屏上根本不显示。 */
const drawer = ref(false)

// 窄不窄和 CSS 那条 `@media (width <= 900px)` 是同一个数：窄屏上左栏才收成抽屉。
// 这个数还要在 JS 里用一次 —— 收起时那一栏只是被 `translateX(-100%)` 挪出画面，
// 人眼看不见，键盘却还走得到它里面那排按钮，所以得给它 `inert`。
const NARROW_MAX_WIDTH = 900
const display = useDisplay()
const narrow = computed(() => (display.width.value ?? 0) <= NARROW_MAX_WIDTH)
/** 左栏这一会儿是不是看不见：宽屏上永远看得见，窄屏上要抽屉开着才看得见。 */
const railHidden = computed(() => narrow.value && !drawer.value)

// 抽屉开着的时候把窗口拉过 900px：CSS 那条 media 不再命中，遮罩那层就退回成一颗没有
// 样式的空按钮，留在页尾。宽回去了就自己把抽屉关掉，不留这颗东西。
watch(narrow, (isNarrow) => {
  if (!isNarrow) drawer.value = false
})

/** 搜到东西就直接看结果，不管现在站在哪一页上。 */
const showingIndex = computed(() => (id.value === null || query.value.trim() !== '') && !missing.value)

function toggleTheme(): void {
  studio.theme = studio.theme === 'dark' ? 'light' : 'dark'
}
</script>

<template>
  <!-- iframe 取的那一格：不带壳，只有这一格本身。 -->
  <CatalogFramePage
    v-if="frameState && entry"
    :entry="entry"
    :state="frameState"
    :motion="frameQuery.motion"
    :bg="frameQuery.bg"
  />
  <p v-else-if="route.frame !== null" class="catalog-missing">目录里没有这一个。</p>

  <div v-else class="studio" :data-drawer="drawer ? 'on' : 'off'">
    <header class="studio-top">
      <button
        type="button"
        class="studio-burger"
        aria-label="打开目录"
        aria-controls="studio-rail"
        :aria-expanded="drawer"
        @click="drawer = !drawer"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
        </svg>
      </button>

      <a class="studio-brand" href="/demo/catalog">
        <span class="studio-mark" aria-hidden="true">知</span>
        <span class="studio-name">组件站</span>
      </a>

      <span class="studio-find">
        <svg class="studio-find-icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <circle cx="11" cy="11" r="6" fill="none" stroke="currentColor" stroke-width="1.8" />
          <path d="m15.5 15.5 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
        </svg>
        <input
          v-model="query"
          class="studio-search"
          type="search"
          autocomplete="off"
          aria-label="按名字、说明或路径找组件"
          placeholder="按名字、说明或路径找"
        />
      </span>

      <span class="studio-count">{{ CATALOG.length }} 个组件</span>
      <a class="studio-demo" href="/demo/quickstart">动态演示 →</a>

      <button
        type="button"
        class="studio-icon"
        :aria-label="studio.theme === 'dark' ? '切到浅色' : '切到深色'"
        @click="toggleTheme"
      >
        <svg v-if="studio.theme === 'dark'" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" stroke-width="1.6" />
          <path
            d="M12 2.6v2.4M12 19v2.4M2.6 12h2.4M19 12h2.4M5.3 5.3 7 7M17 17l1.7 1.7M18.7 5.3 17 7M7 17l-1.7 1.7"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
          />
        </svg>
        <svg v-else viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path
            d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linejoin="round"
          />
        </svg>
      </button>
    </header>

    <div class="studio-body">
      <aside id="studio-rail" class="studio-rail" :inert="railHidden || undefined">
        <CatalogRail :groups="allGroups" :current="missing ? null : id" />
      </aside>

      <main class="studio-main">
        <CatalogIndexPage v-if="showingIndex" :groups="groups" :note="resultNote" />
        <CatalogDetailPage v-else-if="entry" :entry="entry" />
        <p v-if="missing" class="catalog-missing">
          目录里没有这一个。回<a class="catalog-missing-link" href="/demo/catalog">目录</a>看看。
        </p>
      </main>
    </div>

    <button v-if="drawer" type="button" class="studio-scrim" aria-label="收起目录" @click="drawer = false" />
  </div>
</template>

<style scoped>
.studio {
  display: flex;
  flex-direction: column;
  height: 100dvh;
  overflow: hidden;
  background: var(--canvas);
}

.studio-top {
  display: flex;
  gap: 10px;
  align-items: center;
  height: 56px;
  padding: 0 12px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

.studio-burger {
  display: none;
  padding: 6px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.studio-burger:hover {
  color: var(--ink);
  background: var(--fill);
}

.studio-brand {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  text-decoration: none;
}

.studio-mark {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  font-size: 13px;
  color: var(--accent-ink);
  background: var(--accent-wash);
  border-radius: var(--radius-sm);
}

.studio-find {
  display: flex;
  position: relative;
  max-width: 420px;
  min-width: 0;
  margin-left: 12px;
  flex: 1 1 200px;
  align-items: center;
}

.studio-find-icon {
  position: absolute;
  left: 10px;
  color: var(--faint);
  pointer-events: none;
}

.studio-search {
  width: 100%;
  padding: 6px 10px 6px 32px;

  /* 16px 以下 iOS Safari 聚焦时会把整页放大。 */
  font-size: 16px;
  color: var(--ink);
  background: var(--fill-2);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.studio-search:focus-visible {
  border-color: var(--accent);
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.studio-count,
.studio-demo {
  font-size: 13px;
  color: var(--muted);
}

.studio-count {
  margin-left: auto;
}

.studio-demo {
  text-decoration: none;
}

.studio-demo:hover {
  color: var(--accent-ink);
}

.studio-icon {
  display: grid;
  place-items: center;
  padding: 6px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.studio-icon:hover {
  color: var(--ink);
  background: var(--fill);
}

.studio-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.studio-rail {
  flex: none;
  width: 264px;
  overflow: auto;
  background: var(--surface);
  border-right: 1px solid var(--line);
}

.studio-main {
  flex: 1;
  min-width: 0;
  overflow: auto;
}

.catalog-missing {
  margin: 24px;
  color: var(--muted);
}

.catalog-missing-link {
  color: var(--accent-ink);
}

/* 窄下来左栏收成抽屉：地方没了，但目录还得找得到。 */
@media (width <= 900px) {
  .studio-burger {
    display: grid;
    place-items: center;
  }

  .studio-count,
  .studio-demo {
    display: none;
  }

  /* 上面那两个在窄屏收掉了，靠它把主题开关顶到右边。 */
  .studio-icon {
    margin-left: auto;
  }

  .studio-rail {
    position: fixed;
    top: 56px;
    bottom: 0;
    left: 0;
    z-index: 30;
    width: min(300px, 84vw);
    box-shadow: var(--shadow-2);
    transform: translateX(-100%);
    transition: transform var(--dur-base) var(--ease-out);
  }

  .studio[data-drawer='on'] .studio-rail {
    transform: none;
  }

  .studio-scrim {
    position: fixed;
    inset: 56px 0 0;
    z-index: 20;
    padding: 0;
    cursor: pointer;
    background: var(--overlay);
    border: 0;
  }
}
</style>
