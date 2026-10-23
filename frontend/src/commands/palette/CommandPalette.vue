<script setup lang="ts">
// 命令面板：在哪都能按 ⌘K / Ctrl K 叫出来，找一个话题、项目、成员、页面，或者做一件
// 此刻能做的事。桌面上是浮在页面上方的一块；手机上是一整页，从话题列表顶栏的搜索
// 图标进来。
//
// 这个文件是壳和键盘：开着没有、焦点在哪儿、哪个键归谁。画的部分在四件子组件里
// （顶栏 PaletteInput、结果 PaletteResults、操作清单 PaletteActions、底栏
// PaletteFoot），找什么在 usePaletteSearch，一条结果能做什么在 usePaletteActions。
// 面板里的东西全部来自数据源（sources.ts）和命令表（@/commands），这里只管输入、
// 选择和打开。整个应用只挂一个，在 App 里。
//
// 回车做一条结果最直接的那件事（打开、执行、定位消息）。Tab 列出对它还能做的事
// （复制链接、重命名……），手机上没有 Tab，长按一行升起同一份清单。
import type { MenuCommand } from '@/commands'
import type { ResultRow } from './results'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { actionId, optionId } from './ids'
import PaletteActions from './PaletteActions.vue'
import PaletteFoot from './PaletteFoot.vue'
import PaletteInput from './PaletteInput.vue'
import PaletteResults from './PaletteResults.vue'
import { readRecents, recordVisit } from './recents'
import { paletteSources } from './sources'
import { paletteAsk, paletteOpen } from './state'
import { usePaletteActions } from './usePaletteActions'
import { usePaletteSearch } from './usePaletteSearch'

import { defineCommands, menuActionOf } from '@/commands'
import MobileActionSheet from '@/components/common/MobileActionSheet.vue'
import { t } from '@/i18n'

const router = useRouter()
const route = useRoute()
const { mdAndUp } = useDisplay()

const input = ref('')
const selected = ref(0)
const recents = ref(readRecents())
const bar = ref<InstanceType<typeof PaletteInput> | null>(null)

const { scope, ctx, scopeName, groups, rows, searching } = usePaletteSearch({
  input,
  recents,
  open: paletteOpen,
  route,
  router,
})

const { acting, actSelected, verbOf, hasMore, openActions, onRowContextMenu, openSheet, sheetOpen } = usePaletteActions(
  { rows, selected, choose, mdAndUp }
)

const selectedRow = computed(() => rows.value[selected.value])

function focusField() {
  void nextTick(() => bar.value?.focus())
}

// 返回手势 / 返回键关掉的是面板这一层，不是它底下那一页：开着的时候在历史里压一
// 格（地址不变，只在 state 上做个记号），返回把它弹掉，这里听到就关。手机上从话题
// 列表叫出面板、按返回却换了页，就是少了这一格。
//
// 用面板里的办法关掉（左上角、Esc、点空白）时，那一格得自己退掉，不在身后留一格
// 空的——否则下一次返回什么也不做。正要选一条结果离开这一页时不退：那一格正好是
// 「回到打开面板的地方」。
const HISTORY_MARK = 'commandPalette'
let historyPushed = false

function onPopState() {
  if ((history.state as Record<string, unknown> | null)?.[HISTORY_MARK]) return
  historyPushed = false
  close()
}

function pushHistory() {
  if (historyPushed) return
  history.pushState({ ...(history.state as object | null), [HISTORY_MARK]: true }, '')
  historyPushed = true
}

function dropHistory() {
  if (!historyPushed) return
  historyPushed = false
  if (!(history.state as Record<string, unknown> | null)?.[HISTORY_MARK]) return
  // 关掉之后这一轮里页面可能正要跳去别处（选中了一条结果）。等这一轮结束再看：
  // 地址变了就把那一格留给「返回」，不要退掉它。
  const href = location.href
  setTimeout(() => {
    if (location.href !== href) return
    if ((history.state as Record<string, unknown> | null)?.[HISTORY_MARK]) history.back()
  }, 0)
}

onMounted(() => window.addEventListener('popstate', onPopState))
onBeforeUnmount(() => window.removeEventListener('popstate', onPopState))

// 这一栏钉在顶上：一打开、一问一答完、换了范围，光标都该回来。
let returnFocus: HTMLElement | null = null
watch(paletteOpen, async (open) => {
  acting.value = null
  paletteAsk.value = null
  scope.value = undefined
  if (open) {
    pushHistory()
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    input.value = ''
    selected.value = 0
    recents.value = readRecents()
    await nextTick()
    bar.value?.focus()
  } else {
    // 底部那张操作清单是面板的一部分：面板关了，它不能自己留在页面上。
    sheetOpen.value = false
    dropHistory()
    returnFocus?.focus?.()
    returnFocus = null
  }
})

watch(input, () => {
  selected.value = 0
  acting.value = null
})
watch(rows, (next) => {
  if (selected.value >= next.length) selected.value = Math.max(next.length - 1, 0)
})

function close() {
  paletteOpen.value = false
}

function enterScope(projectId: string) {
  scope.value = projectId
  input.value = ''
  acting.value = null
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

function choose(item: ResultRow, newTab = false) {
  if (newTab && item.to) {
    window.open(router.resolve(item.to).href, '_blank', 'noopener')
    return
  }
  if (!item.remote) recordVisit(item)
  // 要去别处了：历史里那一格留着，从那边返回时正好回到打开面板的地方。
  if (item.to) historyPushed = false
  close()
  item.run?.()
  if (item.to) void router.push(item.to)
}

async function runAction(action: MenuCommand) {
  const row = acting.value?.row
  acting.value = null
  // 进到一个项目里搜：面板留着，换个范围接着打字。
  if (action.id === 'palette.scope' && row?.scope) {
    enterScope(row.scope)
    focusField()
    return
  }
  action.run?.()
  // 这件事要在面板里接着问一句（重命名）：面板留着，输入框换成它。
  if (paletteAsk.value) {
    askText.value = paletteAsk.value.value
    await nextTick()
    bar.value?.focus()
    bar.value?.select()
    return
  }
  // 同 choose()：这一件要去别处，历史里那一格留给返回。
  if (action.to) historyPushed = false
  close()
  if (action.to) void router.push(action.to)
}

// 长按（`PaletteResults` 认出来的）升起的那一份，和桌面上的清单是同一批事。
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
    focusField()
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

// A v-bottom-sheet's z-index prop is a number, so it cannot read the CSS token
// (--z-menu, design-system §3.8). This is the one JS copy of that rung: the
// sheet has to open above the palette layer (--z-overlay, 2400).
const SHEET_Z = 2500

// 底下这一层要按同一套号码指过去：Tab 出来的清单开着时指清单，否则指选中的那一行。
const activeDescendant = computed(() =>
  acting.value ? actionId(actSelected.value) : rows.value.length ? optionId(selected.value) : undefined
)
</script>

<template>
  <Teleport to="body">
    <Transition name="palette">
      <div v-if="paletteOpen" class="palette-layer" :class="{ 'palette-layer--page': !mdAndUp }">
        <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- clicking outside closes the palette; Escape closes it too, once it has cleared the query, the action list or ask mode -->
        <div v-if="mdAndUp" class="palette-scrim" @click="close" />
        <div class="palette" role="dialog" aria-modal="true" :aria-label="t('navigation.palette.open')">
          <PaletteInput
            ref="bar"
            v-model:input="input"
            v-model:ask-text="askText"
            :md-and-up="mdAndUp"
            :ask="paletteAsk"
            :scope-name="scopeName"
            :searching="searching"
            :expanded="rows.length > 0"
            :active-descendant="activeDescendant"
            @close="close"
            @keydown="onKeydown"
            @ask-keydown="onAskKeydown"
            @leave-scope="scope = null"
          />
          <PaletteResults
            v-if="!paletteAsk"
            :groups="groups"
            :selected="selected"
            :input="input"
            :searching="searching"
            :md-and-up="mdAndUp"
            @select="selected = $event"
            @choose="choose"
            @contextmenu="onRowContextMenu"
            @longpress="openSheet"
            @dismiss="acting && mdAndUp && (acting = null)"
          />
          <PaletteFoot
            v-if="mdAndUp && (paletteAsk || selectedRow)"
            :asking="!!paletteAsk"
            :action-title="acting?.actions[actSelected]?.title"
            :verb="selectedRow ? verbOf(selectedRow) : undefined"
            :can-search-in="!!selectedRow?.scope"
            :has-more="hasMore(selectedRow)"
            :has-target="!!selectedRow?.to"
          />
          <PaletteActions
            v-if="acting && mdAndUp"
            :title="acting.row.title"
            :actions="acting.actions"
            :selected="actSelected"
            @select="actSelected = $event"
            @run="runAction"
          />
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

/* 手机上是一整页：高度扣掉键盘，输入框钉在顶上。子组件里那几处随页变的尺寸各自按
   `--page` 判，这里只管整块的框。 */
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
