<script setup lang="ts">
// 对话左上角的书签条：浮在对话上面，平时默认收起成一颗小角标，不挡对话正文；鼠标移上去
// 或点一下才展开列表，移开自动收起。没有书签时整块不画，也不占地方。
import type { MenuAction } from '@/components/common/menuAction'
import type { RefNames } from '@/lib/refChip'
import type { ChapterRow } from '@/types/chapters'

import { computed, nextTick, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { replySnippet } from '@/lib/blockDisplay'
import { inReadingOrder } from '@/lib/chapters'

const props = defineProps<{
  /** 这段对话的书签，按读到的顺序（组件自己按读序排，编号才是对话里的次序）。 */
  chapters: ChapterRow[]
  /** 能不能改：说得上话、没归档。 */
  canChapter: boolean
  refs: RefNames
  /** 此刻停在的那一条，跳过去之后把列表里对应的一行点亮。 */
  activeId?: string | null
}>()

const emit = defineEmits<{
  (e: 'jump', blockId: string): void
  (e: 'unpin', blockId: string): void
  (e: 'rename', blockId: string, title: string): void
}>()

const ordered = computed(() => inReadingOrder(props.chapters))
const shown = computed(() => ordered.value.length > 0)

// 默认收起成左上角小角标；指针移入或点击展开，移出或在触屏上点面板外时自动收起。
const open = ref(false)
const menuForId = ref<string | null>(null)
const railRef = ref<HTMLElement | null>(null)

function onLeave() {
  if (!editingId.value && !menuForId.value) open.value = false
}

function jumpTo(blockId: string) {
  menuForId.value = null
  emit('jump', blockId)
}

function unpinChapter(blockId: string) {
  menuForId.value = null
  emit('unpin', blockId)
}

useEventListener(typeof document !== 'undefined' ? document : undefined, 'pointerdown', (e: PointerEvent) => {
  if (!open.value || editingId.value || menuForId.value) return
  const el = railRef.value
  if (el && e.target instanceof Node && !el.contains(e.target)) open.value = false
})

/** 这一条叫什么：存的标题（人改的或模型起的），没有就退回消息的头一句。 */
function titleOf(chapter: ChapterRow): string {
  return chapter.title || replySnippet(chapter.block, props.refs, 60) || t('work.channel.chapters.untitled')
}

// 就地重命名：正在改的那一条、它里面的草稿。改完或取消都回到列表。
const editingId = ref<string | null>(null)
const draft = ref('')
// `ref` 在 v-for 里落下来的是一组元素（哪怕同一刻只画一个），所以取第一个。
const inputRef = ref<HTMLInputElement | HTMLInputElement[] | null>(null)

async function startRename(chapter: ChapterRow) {
  menuForId.value = null
  editingId.value = chapter.block.id
  draft.value = titleOf(chapter)
  await nextTick()
  const el = Array.isArray(inputRef.value) ? inputRef.value[0] : inputRef.value
  el?.focus()
}
function cancelRename() {
  menuForId.value = null
  editingId.value = null
  draft.value = ''
}
function saveRename() {
  const id = editingId.value
  const name = draft.value.trim()
  if (!id) return
  if (!name) return cancelRename()
  emit('rename', id, name)
  cancelRename()
}
watch(
  () => props.chapters,
  () => {
    menuForId.value = null
    if (editingId.value) cancelRename()
  }
)
// 菜单只在能改的时候画。它被卸掉时没有人会再报「菜单关了」，留着那个 id 会让
// 浮条再也收不起来：移开鼠标、点面板外面都被这两条判定挡回去。
watch(
  () => props.canChapter,
  (can) => {
    if (!can) menuForId.value = null
  }
)

function actions(chapter: ChapterRow): MenuAction[] {
  // 点这一行本来就跳过去了（`bm-rail__jump`），菜单里再放一条「跳转」是同一件事
  // 说两遍，还让人以为点行不跳。
  const out: MenuAction[] = []
  if (props.canChapter) {
    out.push({
      key: 'rename',
      label: t('work.channel.chapters.rename'),
      icon: 'mdi-pencil-outline',
      onSelect: () => void startRename(chapter),
    })
    out.push({
      key: 'unpin',
      label: t('work.channel.chapters.unpin'),
      icon: 'mdi-bookmark',
      onSelect: () => unpinChapter(chapter.block.id),
    })
  }
  return out
}
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/mouse-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- the toggle button inside handles keyboard and touch expand/collapse -->
  <div
    v-if="shown"
    ref="railRef"
    class="bm-rail"
    :class="{ 'bm-rail--open': open }"
    data-testid="bookmark-rail"
    @mouseenter="open = true"
    @mouseleave="onLeave"
  >
    <button
      type="button"
      class="bm-rail__toggle"
      :aria-expanded="open"
      :title="open ? t('work.channel.chapters.collapse') : t('work.channel.chapters.expand')"
      :aria-label="open ? t('work.channel.chapters.collapse') : t('work.channel.chapters.expand')"
      @click="open = !open"
    >
      <v-icon size="15">{{ open ? 'mdi-bookmark' : 'mdi-bookmark-outline' }}</v-icon>
      <span v-if="!open" class="bm-rail__count" aria-hidden="true">{{ ordered.length }}</span>
    </button>
    <div v-show="open" class="bm-rail__panel">
      <p class="bm-rail__title">{{ t('work.channel.chapters.title', { count: ordered.length }) }}</p>
      <ul class="bm-rail__list">
        <li v-for="(chapter, i) in ordered" :key="chapter.block.id" class="bm-rail__row">
          <template v-if="editingId === chapter.block.id">
            <input
              ref="inputRef"
              v-model="draft"
              class="bm-rail__input t-body"
              type="text"
              autocomplete="off"
              maxlength="80"
              :aria-label="t('work.channel.chapters.renameHint')"
              @keydown.enter.prevent="saveRename"
              @keydown.esc.prevent="cancelRename"
              @blur="cancelRename"
            />
          </template>
          <template v-else>
            <button
              type="button"
              class="bm-rail__jump"
              :class="{ 'bm-rail__jump--here': chapter.block.id === activeId }"
              :aria-current="chapter.block.id === activeId ? 'true' : undefined"
              :aria-label="titleOf(chapter)"
              @click="jumpTo(chapter.block.id)"
            >
              <span class="bm-rail__no" aria-hidden="true">{{ i + 1 }}</span>
              <span class="bm-rail__name">{{ titleOf(chapter) }}</span>
            </button>
            <AdaptiveMenu
              v-if="canChapter"
              :model-value="menuForId === chapter.block.id"
              :actions="actions(chapter)"
              :title="t('work.channel.chapters.menu')"
              @update:model-value="
                (v: boolean) => (menuForId = v ? chapter.block.id : menuForId === chapter.block.id ? null : menuForId)
              "
            >
              <template #activator="{ props: menuProps }">
                <BaseButton
                  v-bind="menuProps"
                  icon="mdi-dots-horizontal"
                  size="sm"
                  class="tap-target bm-rail__more"
                  :aria-label="t('work.channel.chapters.menu', { name: titleOf(chapter) })"
                />
              </template>
            </AdaptiveMenu>
          </template>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
/* 浮在对话左上角：对话本身不动宽度，长消息照旧读得完整。 */
.bm-rail {
  position: absolute;
  top: 8px;
  left: 12px;
  z-index: 4;
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.bm-rail__toggle {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: 0;
  padding: 5px 6px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--raised);
  color: var(--muted);
  cursor: pointer;
}
.bm-rail--open .bm-rail__toggle {
  color: var(--ink);
}
.bm-rail__count {
  font-size: 13px;
  line-height: var(--lh-13);
  font-variant-numeric: tabular-nums;
}
.bm-rail__panel {
  width: 232px;
  max-height: 60vh;
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-1);
}
.bm-rail__title {
  margin: 0 4px 6px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.bm-rail__list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.bm-rail__row {
  display: flex;
  align-items: center;
  gap: 2px;
}
.bm-rail__jump {
  display: flex;
  flex: 1 1 auto;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  padding: 5px 6px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  text-align: left;
  cursor: pointer;
}
.bm-rail__jump:hover {
  background: var(--fill);
}
/* 此刻停在哪一节：这一列里唯一带色的那一条，和未读线同一个道理。 */
.bm-rail__jump--here {
  background: var(--accent-wash);
}
.bm-rail__no {
  flex: none;
  font-size: 13px;
  line-height: var(--lh-13);
  font-variant-numeric: tabular-nums;
  color: var(--muted);
}
.bm-rail__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}
.bm-rail__jump--here .bm-rail__name {
  color: var(--accent-ink);
}
/* 桌面上 ⋯ 只在指针停在这一行时出现；触屏上没有悬停，直接画出来才点得到。 */
.bm-rail__more {
  flex: none;
  opacity: 0;
}
.bm-rail__row:hover .bm-rail__more,
.bm-rail__more:focus-visible {
  opacity: 1;
}
@media (hover: none) {
  .bm-rail__more {
    opacity: 1;
  }
}
.bm-rail__input {
  width: 100%;
  padding: 5px 6px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text);
}
</style>
