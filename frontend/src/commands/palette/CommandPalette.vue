<script setup lang="ts">
// 命令面板：在哪都能按 ⌘K / Ctrl K 叫出来，找一个话题、项目、成员、页面，或者做一件
// 此刻能做的事。桌面上是浮在页面上方的一块；手机上是一整页，从话题列表顶栏的搜索
// 图标进来。
//
// 面板里的东西全部来自数据源（sources.ts）和命令表（@/commands），这里只管输入、
// 选择和打开。整个应用只挂一个，在 App 里。
//
// 回车做一条结果最直接的那件事（打开、执行、定位消息）。Tab 列出对它还能做的事
// （复制链接、重命名……），手机上没有 Tab，长按一行升起同一份清单。
import type { MenuCommand } from '@/commands'
import type { ResultRow } from './results'
import type { PaletteItem, SourceContext } from './sources'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { useLongPress } from '@/composables/useLongPress'

import { readRecents, recordVisit } from './recents'
import { buildResults, remoteSources, splitPrefix } from './results'
import { paletteSources } from './sources'
import { paletteAsk, paletteOpen } from './state'

import { defineCommands, menuActionOf } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import MobileActionSheet from '@/components/common/MobileActionSheet.vue'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const router = useRouter()
const route = useRoute()
const { mdAndUp } = useDisplay()

const input = ref('')
const selected = ref(0)
const recents = ref(readRecents())
const field = ref<HTMLInputElement | null>(null)
let returnFocus: HTMLElement | null = null

// 在哪个项目里找。默认跟着当前页；空着按退格去掉范围（跨项目只找名字），在一个项目
// 上按 Tab 进到那个项目里。每次打开都回到当前页所在的项目。
const routeCtx = computed<SourceContext>(() => ({
  projectId: typeof route.params.projectId === 'string' ? route.params.projectId : null,
  router,
}))
const scope = ref<string | null | undefined>(undefined)
const ctx = computed<SourceContext>(() =>
  scope.value === undefined ? routeCtx.value : { projectId: scope.value, router }
)
const store = useWorkspaceStore()
const scopeName = computed(() =>
  ctx.value.projectId ? store.projects.find((project) => project.id === ctx.value.projectId)?.name : undefined
)

function enterScope(projectId: string) {
  scope.value = projectId
  input.value = ''
  acting.value = null
}

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

// 远程搜：停止打字一会儿再问，问回来时输入已经变了就扔掉。本地结果不等它。
const SEARCH_DELAY_MS = 200
const remote = ref(new Map<string, PaletteItem[]>())
const searching = ref(false)
let asked = 0
let timer: ReturnType<typeof setTimeout> | undefined
watch([input, () => ctx.value.projectId, paletteOpen], () => {
  clearTimeout(timer)
  const ask = ++asked
  remote.value = new Map()
  // 内容只在一个项目里搜。
  const targets = paletteOpen.value && ctx.value.projectId ? remoteSources(input.value, paletteSources) : []
  searching.value = targets.length > 0
  if (!targets.length) return
  const text = splitPrefix(input.value).query
  timer = setTimeout(async () => {
    const found = await Promise.all(
      targets.map((source) => source.search!(text, ctx.value).catch(() => [] as PaletteItem[]))
    )
    if (ask !== asked) return
    remote.value = new Map(targets.map((source, i) => [source.id, found[i]]))
    searching.value = false
  }, SEARCH_DELAY_MS)
})
onBeforeUnmount(() => clearTimeout(timer))

const groups = computed(() =>
  paletteOpen.value ? buildResults(input.value, paletteSources, ctx.value, recentElsewhere.value, remote.value) : []
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

watch(input, () => {
  selected.value = 0
  acting.value = null
})
watch(rows, (next) => {
  if (selected.value >= next.length) selected.value = Math.max(next.length - 1, 0)
})

watch(paletteOpen, async (open) => {
  acting.value = null
  paletteAsk.value = null
  scope.value = undefined
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
      const item = source.fromRoute?.(to, routeCtx.value)
      if (item) {
        recordVisit(item)
        return
      }
    }
  })
)

function choose(item: ResultRow, newTab = false) {
  if (newTab && item.to) {
    window.open(router.resolve(item.to).href, '_blank', 'noopener')
    return
  }
  if (!item.remote) recordVisit(item)
  close()
  item.run?.()
  if (item.to) void router.push(item.to)
}

// ---- 更多操作 --------------------------------------------------------------

function verbOf(row: ResultRow): string {
  return row.verb ?? t(row.to ? 'navigation.palette.verbOpen' : 'navigation.palette.verbRun')
}

/** 一条结果能做的全部事：回车那一件在最前，然后是在新标签页打开，然后是它自己的。 */
function actionsOf(row: ResultRow): MenuCommand[] {
  const list: MenuCommand[] = [{ id: 'palette.primary', title: verbOf(row), icon: row.icon, run: () => choose(row) }]
  // 手机上没有标签页可开。
  if (row.to && mdAndUp.value)
    list.push({
      id: 'palette.newTab',
      title: t('navigation.palette.newTab'),
      icon: 'mdi-open-in-new',
      run: () => choose(row, true),
    })
  if (row.scope)
    list.push({ id: 'palette.scope', title: t('navigation.palette.searchIn'), icon: 'mdi-magnify', run: () => {} })
  return [...list, ...(row.actions?.() ?? [])]
}

/** 只有回车那一件可做的（一条操作）不开清单。 */
function hasMore(row: ResultRow | undefined): boolean {
  return !!row && actionsOf(row).length > 1
}

const acting = ref<{ row: ResultRow; actions: MenuCommand[] } | null>(null)
const actSelected = ref(0)

function openActions(row: ResultRow | undefined) {
  if (!hasMore(row)) return
  acting.value = { row: row!, actions: actionsOf(row!) }
  actSelected.value = 0
}

async function runAction(action: MenuCommand) {
  const row = acting.value?.row
  acting.value = null
  // 进到一个项目里搜：面板留着，换个范围接着打字。
  if (action.id === 'palette.scope' && row?.scope) {
    enterScope(row.scope)
    await nextTick()
    field.value?.focus()
    return
  }
  action.run?.()
  // 这件事要在面板里接着问一句（重命名）：面板留着，输入框换成它。
  if (paletteAsk.value) {
    askText.value = paletteAsk.value.value
    await nextTick()
    field.value?.focus()
    field.value?.select()
    return
  }
  close()
  if (action.to) void router.push(action.to)
}

// 手机上长按一行：同一份清单从底部升起。
const list = ref<HTMLElement | null>(null)
const sheetOpen = ref(false)
useLongPress(list, (event) => {
  const index = Number((event.target as HTMLElement | null)?.closest?.('[data-row]')?.getAttribute('data-row'))
  const row = rows.value[index]
  if (!hasMore(row)) return
  selected.value = index
  acting.value = { row: row!, actions: actionsOf(row!) }
  sheetOpen.value = true
})
watch(sheetOpen, (open) => {
  if (!open && !paletteAsk.value) acting.value = null
})
const sheetActions = computed(() =>
  sheetOpen.value && acting.value
    ? acting.value.actions.map((action) => ({ ...menuActionOf(action), onSelect: () => void runAction(action) }))
    : []
)

// ---- 在面板里问一句 --------------------------------------------------------

const askText = ref('')

function submitAsk() {
  const ask = paletteAsk.value
  if (!ask) return
  ask.submit(askText.value)
  close()
}

function onAskKeydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  if (event.key === 'Enter') {
    event.preventDefault()
    submitAsk()
  } else if (event.key === 'Escape') {
    // 不改了：回到刚才那张结果列表，输入的字还在。
    event.preventDefault()
    paletteAsk.value = null
    void nextTick(() => field.value?.focus())
  }
}

function onActingKeydown(event: KeyboardEvent, actions: MenuCommand[]) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    actSelected.value = (actSelected.value + 1) % actions.length
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    actSelected.value = (actSelected.value - 1 + actions.length) % actions.length
  } else if (event.key === 'Enter') {
    event.preventDefault()
    void runAction(actions[actSelected.value])
  } else if (event.key === 'Escape' || event.key === 'Tab') {
    // Esc 只收起清单，不连面板一起关。
    event.preventDefault()
    acting.value = null
  }
}

function onKeydown(event: KeyboardEvent) {
  // 拼音还在组字的时候，回车和方向键是输入法的，不是面板的。
  if (event.isComposing || event.keyCode === 229) return
  if (acting.value) {
    onActingKeydown(event, acting.value.actions)
    return
  }
  if (event.key === 'Tab') {
    // Tab 在面板里不挪焦点：焦点一离开输入框，方向键和回车就不归面板了。
    event.preventDefault()
    const row = rows.value[selected.value]
    if (row?.scope) enterScope(row.scope)
    else openActions(row)
  } else if (event.key === 'Backspace' && !input.value && ctx.value.projectId) {
    // 空着按退格：去掉项目范围，跨项目只找名字。
    event.preventDefault()
    scope.value = null
  } else if (event.key === 'ArrowDown') {
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

// A v-bottom-sheet's z-index prop is a number, so it cannot read the CSS token
// (--z-menu, design-system §3.7). This is the one JS copy of that rung: the
// sheet has to open above the palette layer (--z-overlay, 2400).
const SHEET_Z = 2500

const optionId = (index: number) => `palette-option-${index}`
const actionId = (index: number) => `palette-action-${index}`

const selectedRow = computed(() => rows.value[selected.value])
const enterKey = isMac ? '⌘' : 'Ctrl'
</script>

<template>
  <Teleport to="body">
    <Transition name="palette">
      <div v-if="paletteOpen" class="palette-layer" :class="{ 'palette-layer--page': !mdAndUp }">
        <div v-if="mdAndUp" class="palette-scrim" @click="close" />
        <div class="palette" role="dialog" aria-modal="true" :aria-label="t('navigation.palette.open')">
          <div class="palette__input">
            <BaseButton
              v-if="!mdAndUp"
              icon="mdi-arrow-left"
              size="sm"
              class="tap-target"
              :aria-label="t('navigation.palette.close')"
              @click="close"
            />
            <v-icon v-else icon="mdi-magnify" size="20" class="palette__glass" />
            <template v-if="paletteAsk">
              <span class="palette__asking">{{ paletteAsk.title }}</span>
              <input
                ref="field"
                v-model="askText"
                type="text"
                autocomplete="off"
                spellcheck="false"
                :aria-label="paletteAsk.title"
                :placeholder="paletteAsk.placeholder"
                @keydown="onAskKeydown"
              />
            </template>
            <button
              v-if="!paletteAsk && scopeName"
              type="button"
              class="palette__asking palette__scope"
              :title="t('navigation.palette.leaveScope')"
              :aria-label="t('navigation.palette.leaveScope')"
              @click="scope = null"
            >
              {{ scopeName }}<v-icon icon="mdi-chevron-right" size="14" />
            </button>
            <input
              v-if="!paletteAsk"
              ref="field"
              v-model="input"
              type="text"
              role="combobox"
              autocomplete="off"
              spellcheck="false"
              aria-autocomplete="list"
              aria-controls="palette-results"
              :aria-expanded="rows.length > 0"
              :aria-activedescendant="acting ? actionId(actSelected) : rows.length ? optionId(selected) : undefined"
              :aria-label="t('navigation.palette.open')"
              :placeholder="t('navigation.palette.placeholder')"
              @keydown="onKeydown"
            />
            <v-progress-linear
              :active="searching"
              indeterminate
              absolute
              location="bottom"
              height="2"
              color="primary"
            />
          </div>
          <div
            v-if="!paletteAsk"
            id="palette-results"
            ref="list"
            class="palette__list"
            role="listbox"
            :aria-label="t('navigation.palette.open')"
            @click.capture="acting && mdAndUp && (acting = null)"
          >
            <template v-for="(group, g) in groups" :key="group.key">
              <div v-if="group.label" class="palette__group t-eyebrow-read" role="presentation">
                {{ t(group.label) }}
              </div>
              <div
                v-for="(row, i) in group.rows"
                :id="optionId(offsets[g] + i)"
                :key="`${group.key}:${row.id}`"
                class="palette__row"
                :class="{ 'palette__row--selected': offsets[g] + i === selected }"
                role="option"
                :aria-selected="offsets[g] + i === selected"
                :data-row="offsets[g] + i"
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
                  :class="row.badge.tone && `palette__badge--${row.badge.tone}`"
                >
                  {{ row.badge.text }}
                </span>
              </div>
            </template>
            <div
              v-if="input.trim() && input.trim() !== '?' && !rows.length && !searching"
              class="palette__empty t-body c-muted"
            >
              {{ t('navigation.palette.empty') }}
            </div>
          </div>
          <!-- 底栏只在桌面上有：手机上没有这几个键。 -->
          <div v-if="mdAndUp && (paletteAsk || selectedRow)" class="palette__foot">
            <span class="palette__foot-main">
              <template v-if="paletteAsk">{{ t('navigation.palette.save') }}</template>
              <template v-else-if="acting">{{ acting.actions[actSelected].title }}</template>
              <template v-else-if="selectedRow">{{ verbOf(selectedRow) }}</template>
              <kbd>↵</kbd>
            </span>
            <span class="palette__keys">
              <span v-if="paletteAsk || acting">{{ t('navigation.palette.back') }} <kbd>Esc</kbd></span>
              <template v-else-if="selectedRow">
                <span v-if="selectedRow.scope">{{ t('navigation.palette.searchIn') }} <kbd>Tab</kbd></span>
                <span v-else-if="hasMore(selectedRow)">{{ t('navigation.palette.more') }} <kbd>Tab</kbd></span>
                <span v-if="selectedRow.to">
                  {{ t('navigation.palette.newTab') }} <kbd>{{ enterKey }}</kbd> <kbd>↵</kbd>
                </span>
              </template>
            </span>
          </div>
          <div
            v-if="acting && mdAndUp"
            class="palette__actions"
            role="menu"
            :aria-label="acting.row.title"
            @mousedown.prevent
          >
            <div class="palette__actions-head">{{ acting.row.title }}</div>
            <div
              v-for="(action, i) in acting.actions"
              :id="actionId(i)"
              :key="action.id"
              class="palette__action"
              :class="{ 'palette__action--selected': i === actSelected, 'palette__action--danger': action.danger }"
              role="menuitem"
              @mousemove="actSelected = i"
              @click="runAction(action)"
            >
              <v-icon :icon="action.icon" size="16" class="palette__icon" />
              <span class="palette__title">{{ action.title }}</span>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
  <MobileActionSheet v-model="sheetOpen" :actions="sheetActions" :title="acting?.row.title" :z-index="SHEET_Z" />
</template>

<style scoped>
.palette-layer {
  position: fixed;
  inset: 0;
  z-index: var(--z-overlay);
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
  position: relative;
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
.palette__badge--ok {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.palette__asking {
  flex: none;
  padding: 0 8px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
.palette__scope {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: 0;
  cursor: pointer;
}
.palette__foot {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 14px;
  border-top: 1px solid var(--line);
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
.palette__foot-main {
  color: var(--muted);
}
.palette__keys {
  display: flex;
  gap: 12px;
}
.palette__foot kbd {
  display: inline-block;
  margin-left: 4px;
  min-width: 18px;
  padding: 0 4px;
  border: 1px solid var(--line-2);
  border-bottom-width: 2px;
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  text-align: center;
}
.palette__actions {
  position: absolute;
  right: 10px;
  bottom: 44px;
  width: 260px;
  padding: 4px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-2);
}
.palette__actions-head {
  overflow: hidden;
  padding: 6px 8px 4px;
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.palette__action {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  color: var(--text);
  cursor: pointer;
}
.palette__action--selected {
  background: var(--fill-2);
  color: var(--ink);
}
.palette__action--danger {
  color: var(--danger-ink);
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
