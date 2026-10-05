<script setup lang="ts">
// 项目名下面那一行：这个项目自己的几页（资料库、成员、项目文档……），压成一行图标按钮。
// 侧栏留给频道；这一行固定一行高，放不下的收进行末的「⋯」，以后多一页也不会把频道
// 往下挤。哪几页露出来（顺序、壳、收起来过没收起来过）由父级算好传进来，点了去哪儿由
// 父级决定。
import { computed } from 'vue'

import TopicRailBadge from './TopicRailBadge.vue'

import { t } from '@/i18n'

/** 一行里直接摆几个，多出来的进「⋯」。 */
const INLINE = 3

const props = defineProps<{
  /** 这一版前端认得、而且这个项目的壳摆出来了的那几页（顺序就是壳说的顺序）。 */
  pages: { key: string; label: string; icon: string }[]
  /** 当前页的名字，用来画选中态。 */
  routeName: string | null
  /** 壳换了词之后的项目词汇表（「项目文档」可能得叫「工作文档」）。 */
  terms: { project: string; topic: string }
  /** 项目文档那一项是不是选中态（四种文档里任何一种开着都算）。 */
  docsActive: boolean
  /** 私聊未读的总数，挂在「成员」那一项上。 */
  privateUnreadTotal: number
  /** 整页形态（手机）：这几页收进了项目菜单，这一行不画。 */
  page: boolean
}>()

const emit = defineEmits<{
  (e: 'open-page', key: string): void
  (e: 'hover-page', key: string): void
  (e: 'cancel-prefetch'): void
  (e: 'select-docs'): void
}>()

interface Entry {
  key: string
  label: string
  icon: string
  active: boolean
  badge: number
}
const DOCS = 'project-docs'
const entries = computed<Entry[]>(() => [
  ...props.pages.map((p) => ({
    key: p.key,
    // 文案走词表：壳把「项目」叫「工作」的时候，「{project}文档」跟着变。
    label: t(p.label, props.terms),
    icon: p.icon,
    active: props.routeName === p.key,
    // 私聊的未读挂在「成员」上：这是「有人找你」在主导航上唯一会亮的地方。
    badge: p.key === 'project-members' ? props.privateUnreadTotal : 0,
  })),
  {
    key: DOCS,
    label: t('navigation.project.docs'),
    icon: 'mdi-file-document-outline',
    active: props.docsActive,
    badge: 0,
  },
])
const inline = computed(() => entries.value.slice(0, INLINE))
const overflow = computed(() => entries.value.slice(INLINE))

function open(key: string) {
  if (key === DOCS) emit('select-docs')
  else emit('open-page', key)
}
</script>

<template>
  <nav v-if="!page" class="page-bar" :aria-label="t('navigation.project.pages')">
    <button
      v-for="e in inline"
      :key="e.key"
      type="button"
      class="page-bar__item"
      :class="{ 'is-active': e.active }"
      :aria-current="e.active ? 'page' : undefined"
      @click="open(e.key)"
      @mouseenter="e.key !== DOCS && emit('hover-page', e.key)"
      @mouseleave="emit('cancel-prefetch')"
    >
      <v-icon size="16" class="page-bar__icon" :icon="e.icon" />
      <span class="page-bar__label">{{ e.label }}</span>
      <TopicRailBadge v-if="e.badge > 0" :count="e.badge" />
    </button>
    <v-menu v-if="overflow.length" location="bottom start">
      <template #activator="{ props: menuProps }">
        <button
          v-bind="menuProps"
          type="button"
          class="page-bar__item page-bar__more"
          :class="{ 'is-active': overflow.some((e) => e.active) }"
          :title="t('navigation.project.morePages')"
          :aria-label="t('navigation.project.morePages')"
        >
          <v-icon size="16" class="page-bar__icon" icon="mdi-dots-horizontal" />
        </button>
      </template>
      <v-list density="compact">
        <v-list-item
          v-for="e in overflow"
          :key="e.key"
          :active="e.active"
          :prepend-icon="e.icon"
          :title="e.label"
          @click="open(e.key)"
        >
          <template v-if="e.badge > 0" #append><TopicRailBadge :count="e.badge" /></template>
        </v-list-item>
      </v-list>
    </v-menu>
  </nav>
</template>

<style scoped>
.page-bar {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 8px;
}
.page-bar__item {
  display: inline-flex;
  flex: 0 1 auto;
  align-items: center;
  gap: 4px;
  min-width: 0;
  height: 32px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  font-family: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.page-bar__item:hover {
  background: var(--fill-2);
}
.page-bar__item.is-active {
  background: var(--line-2);
  color: var(--ink);
  font-weight: 600;
}
.page-bar__more {
  flex: none;
  margin-left: auto;
}
.page-bar__icon {
  flex: none;
  color: var(--faint);
}
.page-bar__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
