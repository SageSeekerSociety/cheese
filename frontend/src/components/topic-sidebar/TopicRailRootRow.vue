<script setup lang="ts">
// 频道分组里的第一行：项目自带的频道「综合」。它和别的频道同一个分组、同一种行，
// 只是固定排在第一位、不能归档；挂在它下面的任务由父级填进 slot。
import type { MenuAction } from '@/components/common/menuAction'
import type { Topic } from '@/cx_types'
import type { RowLight } from '@/lib/roomLight'

import { computed } from 'vue'

import { useRowMenu } from '@/composables/useRowMenu'

import TopicRailLight from './TopicRailLight.vue'
import TopicRailTaskFold from './TopicRailTaskFold.vue'

import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { brightest } from '@/lib/roomLight'
import { topicTitle } from '@/lib/topicState'

/** 一列图标，一列文字：每条行的左侧都是「8px 起 + 一个 16px 槽」。 */
const ROW_INDENT = { paddingInlineStart: '8px' }

const props = defineProps<{
  rootTopic: Topic | null
  selectedTopicId: string | null
  page: boolean
  unreadOf: (id: string) => number
  /** 主线上有没有我没读过的新消息：名字加粗。 */
  freshOf?: (id: string) => boolean
  /** 「综合」这一行画哪盏灯（和频道行同一个算法，父级带钟算好）；不亮是 null。 */
  lightOf?: (id: string) => RowLight | null
  /** 这一行的操作（和频道行 ⋯ 同一份）：悬停浮出的那颗 ⋯ 和右键弹的都读它。 */
  rootActions?: MenuAction[]
  /** 挂在它下面的任务收起来了没有。 */
  tasksCollapsed?: boolean
  /** 这个频道进行中的任务数；只决定是否提供折叠入口。 */
  taskCount?: number
  /** 收起来的那一块该画的灯；不亮是 null。 */
  taskLight?: RowLight | null
}>()

/** 这一行自己的灯。父级还没读到「综合」这个话题时（`rootTopic` 为空）这一行不画，
 *  也就没有灯。 */
const rootLight = computed(() =>
  props.rootTopic
    ? brightest([props.lightOf?.(props.rootTopic.id), props.tasksCollapsed ? props.taskLight : null])
    : null
)

// 和频道行一样：行尾那颗 ⋯ 是操作入口，右键弹的是同一份、只是弹在鼠标那一点上；
// 手机上长按（父级那一个 useLongPress 认 data-row-actions）。
const rowMenu = useRowMenu<'root'>()

/** 这一行的 ⋯ 菜单开着没有——开着的时候那颗按钮必须留屏幕上，它是菜单的根。 */
const menuOpen = computed(() => rowMenu.bind('root').modelValue)

const emit = defineEmits<{
  (e: 'select-topic', id: string): void
  (e: 'hover-topic', id: string): void
  (e: 'press-topic', id: string): void
  (e: 'leave-topic'): void
  (e: 'toggle-tasks'): void
}>()
</script>

<template>
  <v-list density="compact" nav class="py-0" tabindex="-1">
    <v-list-item
      v-if="rootTopic"
      tabindex="0"
      :active="rootTopic.id === selectedTopicId"
      rounded="lg"
      class="nav-row pinned-row rail-channel"
      :class="{
        'is-active': rootTopic.id === selectedTopicId,
        'is-menu-open': menuOpen,
        'rail-channel-page': page,
      }"
      :style="ROW_INDENT"
      :data-row-actions="rootTopic.id"
      @click="emit('select-topic', rootTopic.id)"
      @contextmenu="!page && rootActions?.length && rowMenu.open('root', $event)"
      @mouseenter="emit('hover-topic', rootTopic.id)"
      @mouseleave="emit('leave-topic')"
      @focusin="emit('hover-topic', rootTopic.id)"
      @focusout="emit('leave-topic')"
      @pointerdown="$event.pointerType === 'mouse' && $event.button === 0 && emit('press-topic', rootTopic.id)"
    >
      <template #prepend>
        <span class="row-leading">
          <!-- 「综合」的槽里画 #：它是频道，和下面的频道行同一个记号。 -->
          <TopicRailTaskFold
            v-if="taskCount"
            :collapsed="tasksCollapsed === true"
            :page="page"
            @toggle="emit('toggle-tasks')"
          />
          <span v-else class="row-slot">
            <v-icon
              size="16"
              class="row-glyph"
              :class="{ 'row-glyph--unread': unreadOf(rootTopic.id) > 0 || freshOf?.(rootTopic.id) }"
              icon="mdi-pound"
            />
          </span>
        </span>
      </template>
      <v-list-item-title
        :class="{
          'title-unread': unreadOf(rootTopic.id) > 0 || freshOf?.(rootTopic.id),
        }"
        >{{ topicTitle(rootTopic) }}</v-list-item-title
      >
      <template #append>
        <TopicRailLight v-if="rootLight" :light="rootLight" />
        <div v-if="rootActions?.length" class="row-actions" @click.stop>
          <AdaptiveMenu v-bind="rowMenu.bind('root')" :actions="rootActions" :title="topicTitle(rootTopic)">
            <template #activator="{ props: menuProps }">
              <!-- eslint-disable-next-line vue/no-restricted-syntax -- nav bar button whose look this component styles exactly (design-system §3.6 exception) -->
              <v-btn
                v-bind="menuProps"
                :tabindex="rootTopic.id === selectedTopicId ? 0 : -1"
                icon="mdi-dots-horizontal"
                size="small"
                variant="text"
                color="on-surface-variant"
                density="comfortable"
                :title="t('work.sidebar.moreActions')"
                class="row-actions__btn"
              />
            </template>
          </AdaptiveMenu>
        </div>
      </template>
    </v-list-item>
    <!-- 挂在这个房间下面的任务。 -->
    <slot name="root-tasks" />
  </v-list>
</template>

<style scoped>
/* 置顶行和话题行共用一套「行」的底子：行盒、选中态、内边距。这份是给置顶行的那
   一半（话题行那一半在 `TopicRailRow.vue`）——两份有意各写一份，好让两个组件都能
   单独渲染（/demo/catalog），不靠对方在场。 */
.nav-row :deep(.v-list-item-title) {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}
.title-unread {
  font-weight: 650;
  color: var(--text);
}
/* 三态：静默（透明，露出 rail 的 --canvas）/ hover --fill-2 / 选中 --line-2。
   没有琥珀左竖条——选中态靠底色和字重就够了。--line-2 是拿来当底色用的，它在
   ramp 上正好是「比 fill-2 再深一档」的那个中性色。两个主题共用一组 token：选的
   依据是对 --canvas 的对比度而不是名字。Kill Vuetify's active overlay. */
.nav-row.is-active {
  background: var(--line-2);
}
.nav-row.is-active :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}
.nav-row.is-active :deep(.v-list-item-title) {
  color: var(--ink);
  font-weight: 600;
}
.nav-row.is-active :deep(.v-icon) {
  color: var(--muted) !important;
}
.nav-row:hover {
  background: var(--fill-2);
}
/* 选中的行 hover 不能倒退回 hover 档——否则鼠标一扫过，选中态反而变浅。 */
.nav-row.is-active:hover {
  background: var(--line-2);
}
/* 核心修正：Vuetify 的 prepend spacer 默认 ~32px，把图标和标题隔出一条鸿沟，
   稀释了一切缩进关系。压到 8px，缩进的台阶才立得起来。话题行共用同一套：否则
   图标虽同列，文字却各自缩进（话题 24px、项目文档 56px），两列文字对不齐。 */
.nav-row :deep(.v-list-item__spacer) {
  width: 8px !important;
}
/* 图标槽统一成 16px 定宽方块：锁定 prepend 里图标的位置，icon-left 与 text-left
   才能双双成列。 */
.nav-row :deep(.v-list-item__prepend) {
  align-items: center;
}
.nav-row :deep(.v-list-item__prepend > .v-icon) {
  font-size: 16px;
  width: 16px;
  height: 16px;
  margin: 0;
}
/* 行左边那一个 16px 定宽槽。所有行共用（话题行的状态/开关、置顶行的图标），
   所以图标列和文字列在整条侧栏上都成列。空槽也占满 16px：同层级的标题左缘
   必须齐。 */
.row-slot {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
}
/* 置顶行的图标：# / 看板 / 资料库，几个各不相同所以留着；未读转琥珀。 */
.row-glyph {
  color: var(--faint);
}
.row-glyph--unread {
  color: var(--accent);
}
</style>

<style src="./rail-channel-row.css"></style>
