<script setup lang="ts">
// 频道分组里的第一行：项目自带的频道「综合」。它和别的频道同一个分组、同一种行，
// 只是固定排在第一位、不能归档；挂在它下面的任务由父级填进 slot。
import type { MenuAction } from '@/components/common/menuAction'
import type { Topic } from '@/cx_types'
import type { RowLight } from '@/lib/roomLight'

import { computed } from 'vue'

import { useRowMenu } from '@/composables/useRowMenu'

import TopicRailBadge from './TopicRailBadge.vue'
import TopicRailLight from './TopicRailLight.vue'
import TopicRailTaskFold from './TopicRailTaskFold.vue'

import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
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
  /** 我静音了的频道：名字变灰，行尾画一个静音标记。 */
  mutedOf?: (id: string) => boolean
  /** 「综合」这一行画哪盏灯（和频道行同一个算法，父级带钟算好）；不亮是 null。 */
  lightOf?: (id: string) => RowLight | null
  /** 这一行的操作（和频道行 ⋯ 同一份）：悬停浮出的那颗 ⋯ 和右键弹的都读它。 */
  rootActions?: MenuAction[]
  /** 挂在它下面的任务收起来了没有。 */
  tasksCollapsed?: boolean
  /** 这个频道里进行中的任务数；收起来时行尾画它。 */
  taskCount?: number
  /** 收起来的那一块该画的灯；不亮是 null。 */
  taskLight?: RowLight | null
}>()

/** 这一行自己的灯。父级还没读到「综合」这个话题时（`rootTopic` 为空）这一行不画，
 *  也就没有灯。 */
const rootLight = computed(() => (props.rootTopic ? props.lightOf?.(props.rootTopic.id) ?? null : null))

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
      class="nav-row pinned-row"
      :class="{
        'is-active': rootTopic.id === selectedTopicId,
        'is-menu-open': menuOpen,
        'pinned-row--hover-actions': !page,
        'pinned-row--tasks': !!taskCount,
        'pinned-row--tasks-collapsed': !!taskCount && tasksCollapsed === true,
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
        <!-- 「综合」的槽里画 #：它是频道，和下面的频道行同一个记号。 -->
        <span class="row-slot">
          <v-icon
            size="16"
            class="row-glyph"
            :class="{ 'row-glyph--unread': unreadOf(rootTopic.id) > 0 || freshOf?.(rootTopic.id) }"
            icon="mdi-pound"
          />
        </span>
      </template>
      <v-list-item-title
        :class="{
          'title-unread': unreadOf(rootTopic.id) > 0 || freshOf?.(rootTopic.id),
          'title-muted': mutedOf?.(rootTopic.id) && unreadOf(rootTopic.id) === 0,
        }"
        >{{ topicTitle(rootTopic) }}</v-list-item-title
      >
      <template #append>
        <v-icon
          v-if="mutedOf?.(rootTopic.id)"
          size="14"
          class="row-muted"
          icon="mdi-bell-off-outline"
          :aria-label="t('work.room.menu.muted')"
          :title="t('work.room.menu.muted')"
        />
        <TopicRailBadge v-if="unreadOf(rootTopic.id) > 0" :count="unreadOf(rootTopic.id)" />
        <TopicRailLight v-if="rootLight" :light="rootLight" />
        <!-- 挂在它下面的那一整块任务的把手：收起来时它自己顶上那一块的灯和条数。 -->
        <TopicRailTaskFold
          v-if="taskCount"
          :collapsed="tasksCollapsed === true"
          :count="taskCount"
          :light="taskLight ?? null"
          @toggle="emit('toggle-tasks')"
        />
        <!-- hover 浮出的操作入口：一颗 ⋯，和频道行那一套同形（那一半写在
             `TopicRailRow.vue`）。整页形态（手机）没有它：那里长按一行打开同一组操作。 -->
        <div v-if="!page && rootActions?.length" class="row-actions" @click.stop>
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
.title-muted {
  color: var(--faint);
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
/* 置顶行和下面的话题行、别处侧栏的行（common.scss 的 .side-nav）一样高。
   position 是给行尾那颗 hover 浮出的 ⋯ 当基准的（话题行那一份同样这么写）。 */
.nav-row.pinned-row {
  position: relative;
  min-height: 36px;
}
/* 整页形态：手指点的地方至少 44px 高。 */
.topic-rail--page .nav-row {
  min-height: 44px;
}

.row-muted {
  color: var(--faint);
}
/* 行尾那盏灯（`TopicRailLight` 自己画颜色和闪不闪），间距靠这里给。 */
.nav-row .row-light {
  margin-left: 6px;
}

/* Hover-only action overlay: absolutely positioned over the row's tail, zero layout
   width — the title gets the full rail. Hover detection is the WHOLE row. 「综合」和
   别的频道行是同一颗 ⋯（`.topic-row` 那一半在 `TopicRailRow.vue`）；两份有意各写一份，
   好让两个组件都能单独渲染（/demo/catalog），不靠对方在场。 */
.row-actions {
  position: absolute;
  right: 5px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px 3px;
  opacity: 0;
  pointer-events: none;
  /* 有意为之的浮动工具条（Linear 手法）：白底+细边+微影，
     在任何行底色上都成立——不再试图和行底色融为一体。 */
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
  transition: opacity var(--dur-quick) var(--ease-standard);
  color: var(--muted);
}
/* 工具条里的那颗 ⋯ 要有自己的悬停反馈——否则不像能按的东西。
   舒适可点，但必须小于行高（~36px）：25px 按钮 + 16px 图标，稳稳落在行内。 */
.row-actions :deep(.v-btn) {
  width: 25px;
  height: 25px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.row-actions :deep(.v-btn .v-icon) {
  font-size: 16px;
}
.row-actions :deep(.v-btn:hover) {
  background: var(--fill);
  color: var(--text);
}
/* 触摸屏没有 hover，键盘焦点又要先 Tab 到它——这两条规则加起来，⋯ 菜单在桌面宽度
   的触屏上（平板横屏、带触摸屏的笔记本）根本摸不到。所以在没有 hover 能力的设备上
   它常驻。整页形态（手机）不画这颗 ⋯，那里长按一行打开同一组操作。 */
@media (hover: none) {
  .row-actions {
    opacity: 1;
    pointer-events: auto;
  }
  .nav-row .unread-badge {
    opacity: 1;
  }
}
/* 菜单展开时那颗 ⋯ 必须留着：它是菜单的 activator，跟 hover 一起消失的话
   鼠标一离开行、菜单就没了根。键盘焦点这里用 :focus-visible（行自己）加
   :has(:focus-visible)（行里的 ⋯），不用 :focus-within：鼠标点一下行也会让它拿到
   焦点，:focus-within 从那以后一直命中，⋯ 就挂在行尾不走了。 */
.nav-row:hover .row-actions,
.nav-row:focus-visible .row-actions,
.nav-row:has(:focus-visible) .row-actions,
.nav-row.is-menu-open .row-actions {
  opacity: 1;
  pointer-events: auto;
}
/* While the actions are out, the count steps aside (they share the tail). 只在有那颗
   ⋯ 的行上：手机上点过一行之后 :hover 会一直粘着，未读数不能因此消失。 */
.pinned-row--hover-actions:hover .unread-badge,
.pinned-row--hover-actions:focus-visible .unread-badge,
.pinned-row--hover-actions:has(:focus-visible) .unread-badge,
.nav-row.is-menu-open .unread-badge {
  opacity: 0;
}
/* 有折叠加手的行，那颗 ⋯ 往左让出一个把手的宽度：不让的话它正好压住把手，而把手是
   那一块唯一的入口，压住就成了「看着有、点不着」。两档的数和话题行那一份一样（见
   `TopicRailRow.vue` 的同名规则）：展开态 45px，收起态 81px——收起时把手还顶着一盏灯
   和一颗条数。这些数跟着字号走，字号变了要重量。 */
.pinned-row--tasks .row-actions {
  right: 45px;
}
.pinned-row--tasks-collapsed .row-actions {
  right: 81px;
}
</style>
