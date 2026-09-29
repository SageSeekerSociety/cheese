<script setup lang="ts">
// 命令面板：在哪都能按 ⌘K / Ctrl K 叫出来，找一个话题、项目、成员、页面，或者做一件
// 此刻能做的事。桌面上是浮在页面上方的一块；手机上是一整页，从话题列表顶栏的搜索
// 图标进来。
//
// 面板里的东西全部来自数据源（sources.ts）和命令表（@/commands），这里只管输入、
// 选择和打开。整个应用只挂一个，在 App 里。
import type { ResultRow } from './results'
import type { PaletteItem, SourceContext } from './sources'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { readRecents, recordVisit } from './recents'
import { buildResults } from './results'
import { paletteSources } from './sources'
import { paletteOpen } from './state'

import { defineCommands } from '@/commands'
import { t } from '@/i18n'

const router = useRouter()
const route = useRoute()
const { mdAndUp } = useDisplay()

const input = ref('')
const selected = ref(0)
const recents = ref(readRecents())
const field = ref<HTMLInputElement | null>(null)
let returnFocus: HTMLElement | null = null

const ctx = computed<SourceContext>(() => ({
  projectId: typeof route.params.projectId === 'string' ? route.params.projectId : null,
  router,
}))

// 「最近去过」不列你现在就在的地方。
const recentElsewhere = computed(() =>
  recents.value.filter((entry) => {
    try {
      return router.resolve(entry.to).path !== route.path
    } catch {
      return false
    }
  })
)
const groups = computed(() =>
  paletteOpen.value ? buildResults(input.value, paletteSources, ctx.value, recentElsewhere.value) : []
)
const rows = computed(() => groups.value.flatMap((group) => group.rows))
const offsets = computed(() => {
  let at = 0
  return groups.value.map((group) => {
    const start = at
    at += group.rows.length
    return start
  })
})

watch(input, () => (selected.value = 0))
watch(rows, (next) => {
  if (selected.value >= next.length) selected.value = Math.max(next.length - 1, 0)
})

watch(paletteOpen, async (open) => {
  if (open) {
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    input.value = ''
    selected.value = 0
    recents.value = readRecents()
    await nextTick()
    field.value?.focus()
  } else {
    returnFocus?.focus?.()
    returnFocus = null
  }
})

function close() {
  paletteOpen.value = false
}

// ⌘K / Ctrl K：开着就关上。不进面板自己的列表——它就是面板本身。
onBeforeUnmount(
  defineCommands(() => [
    {
      id: 'palette.open',
      title: t('navigation.palette.open'),
      shortcut: 'mod+k',
      palette: false,
      run: () => (paletteOpen.value = !paletteOpen.value),
    },
  ])
)

// 从侧栏、链接去过的地方也算「最近去过」：哪一类认得这个地址，就记成那一类的一条。
onBeforeUnmount(
  router.afterEach((to) => {
    for (const source of paletteSources) {
      const item = source.fromRoute?.(to, ctx.value)
      if (item) {
        recordVisit(item)
        return
      }
    }
  })
)

function choose(item: PaletteItem, newTab = false) {
  if (newTab && item.to) {
    window.open(router.resolve(item.to).href, '_blank', 'noopener')
    return
  }
  recordVisit(item)
  close()
  item.run?.()
  if (item.to) void router.push(item.to)
}

function onKeydown(event: KeyboardEvent) {
  // 拼音还在组字的时候，回车和方向键是输入法的，不是面板的。
  if (event.isComposing || event.keyCode === 229) return
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (rows.value.length) selected.value = (selected.value + 1) % rows.value.length
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (rows.value.length) selected.value = (selected.value - 1 + rows.value.length) % rows.value.length
  } else if (event.key === 'Enter') {
    event.preventDefault()
    const row = rows.value[selected.value]
    if (row) choose(row, event.metaKey || event.ctrlKey)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    // 先清掉输入，空着再按一次才关上：打错了字不该连面板一起丢掉。
    if (input.value) input.value = ''
    else close()
  }
}

function segments(row: ResultRow): { text: string; hit: boolean }[] {
  if (!row.range) return [{ text: row.title, hit: false }]
  const [from, to] = row.range
  return [
    { text: row.title.slice(0, from), hit: false },
    { text: row.title.slice(from, to), hit: true },
    { text: row.title.slice(to), hit: false },
  ].filter((part) => part.text)
}

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
function shortcutLabel(shortcut: string | undefined): string | undefined {
  if (!shortcut) return undefined
  return shortcut
    .split('+')
    .map((part) => (part === 'mod' ? (isMac ? '⌘' : 'Ctrl') : part === 'shift' ? '⇧' : part.toUpperCase()))
    .join(isMac ? '' : ' ')
}

const optionId = (index: number) => `palette-option-${index}`
</script>

<template>
  <Teleport to="body">
    <Transition name="palette">
      <div v-if="paletteOpen" class="palette-layer" :class="{ 'palette-layer--page': !mdAndUp }">
        <div v-if="mdAndUp" class="palette-scrim" @click="close" />
        <div class="palette" role="dialog" aria-modal="true" :aria-label="t('navigation.palette.open')">
          <div class="palette__input">
            <v-btn
              v-if="!mdAndUp"
              icon="mdi-arrow-left"
              variant="text"
              size="small"
              class="tap-target"
              :aria-label="t('navigation.palette.close')"
              @click="close"
            />
            <v-icon v-else icon="mdi-magnify" size="20" class="palette__glass" />
            <input
              ref="field"
              v-model="input"
              type="text"
              role="combobox"
              autocomplete="off"
              spellcheck="false"
              aria-autocomplete="list"
              aria-controls="palette-results"
              :aria-expanded="rows.length > 0"
              :aria-activedescendant="rows.length ? optionId(selected) : undefined"
              :aria-label="t('navigation.palette.open')"
              :placeholder="t('navigation.palette.placeholder')"
              @keydown="onKeydown"
            />
          </div>
          <div id="palette-results" class="palette__list" role="listbox" :aria-label="t('navigation.palette.open')">
            <template v-for="(group, g) in groups" :key="group.key">
              <div class="palette__group t-eyebrow-read" role="presentation">{{ t(group.label) }}</div>
              <div
                v-for="(row, i) in group.rows"
                :id="optionId(offsets[g] + i)"
                :key="`${group.key}:${row.id}`"
                class="palette__row"
                :class="{ 'palette__row--selected': offsets[g] + i === selected }"
                role="option"
                :aria-selected="offsets[g] + i === selected"
                :title="shortcutLabel(row.shortcut)"
                @mousemove="selected = offsets[g] + i"
                @click="choose(row, $event.metaKey || $event.ctrlKey)"
              >
                <v-icon :icon="row.icon" size="18" class="palette__icon" />
                <span class="palette__text">
                  <span class="palette__title">
                    <template v-for="(part, p) in segments(row)" :key="p">
                      <mark v-if="part.hit" class="palette__hit">{{ part.text }}</mark>
                      <template v-else>{{ part.text }}</template>
                    </template>
                  </span>
                  <span v-if="row.subtitle" class="palette__subtitle">{{ row.subtitle }}</span>
                </span>
                <span
                  v-if="row.badge"
                  class="palette__badge"
                  :class="{ 'palette__badge--warn': row.badge.tone === 'warn' }"
                >
                  {{ row.badge.text }}
                </span>
              </div>
            </template>
            <div v-if="input.trim() && !rows.length" class="palette__empty t-body c-muted">
              {{ t('navigation.palette.empty') }}
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.palette-layer {
  position: fixed;
  inset: 0;
  z-index: 2400;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 12vh;
}
.palette-scrim {
  position: absolute;
  inset: 0;
  background: var(--overlay);
}
.palette {
  position: relative;
  display: flex;
  flex-direction: column;
  width: min(640px, calc(100vw - 32px));
  max-height: 64vh;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.palette__input {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  height: 52px;
  padding: 0 14px;
  border-bottom: 1px solid var(--line);
}
.palette__glass {
  color: var(--faint);
}
.palette__input input {
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
  border: 0;
  outline: 0;
  background: none;
  color: var(--ink);
  font-size: 15px;
  line-height: var(--lh-15);
}
.palette__input input::placeholder {
  color: var(--faint);
}
.palette__list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 6px 8px;
}
.palette__group {
  padding: 10px 10px 4px;
}
.palette__row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  color: var(--text);
  cursor: pointer;
}
.palette__row--selected {
  background: var(--fill-2);
  color: var(--ink);
}
.palette__icon {
  flex: none;
  color: var(--muted);
}
.palette__text {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
}
.palette__title,
.palette__subtitle {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.palette__subtitle {
  color: var(--faint);
  font-size: 13px;
  line-height: var(--lh-13);
}
.palette__hit {
  background: none;
  color: var(--accent-ink);
  font-weight: 600;
}
.palette__badge {
  flex: none;
  padding: 0 8px;
  border-radius: var(--radius-pill);
  background: var(--fill-2);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.palette__badge--warn {
  background: var(--warn-wash);
  color: var(--warn-ink);
}
.palette__empty {
  padding: 24px 10px;
  text-align: center;
}

/* 手机上是一整页：高度扣掉键盘，输入框钉在顶上。 */
.palette-layer--page {
  padding-top: env(safe-area-inset-top, 0px);
}
.palette-layer--page .palette {
  width: 100%;
  height: calc(var(--app-height, 100dvh) - var(--keyboard-inset, 0px));
  max-height: none;
  border: 0;
  border-radius: 0;
  box-shadow: none;
}
.palette-layer--page .palette__input {
  height: 56px;
  padding: 0 8px;
}
.palette-layer--page .palette__row {
  min-height: 48px;
}

.palette-enter-active {
  transition: opacity var(--dur-base) var(--ease-out);
}
.palette-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}
.palette-enter-active .palette {
  transition: transform var(--dur-base) var(--ease-out);
}
.palette-leave-active .palette {
  transition: transform var(--dur-quick) var(--ease-in);
}
.palette-enter-from,
.palette-leave-to {
  opacity: 0;
}
.palette-enter-from .palette,
.palette-leave-to .palette {
  transform: translateY(-6px);
}
.palette-layer--page.palette-enter-from .palette,
.palette-layer--page.palette-leave-to .palette {
  transform: translateY(12px);
}
</style>
