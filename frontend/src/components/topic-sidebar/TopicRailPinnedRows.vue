<script setup lang="ts">
// 话题列表顶上那几行置顶入口：全局房间、这个项目露出来的那几页（看板/资料库/
// 成员/…）、项目文档。
//
// 它们和话题行同一种视觉语法——同图标槽、同缩进基准、同选中态、同未读角标，所以
// 「点它会发生什么」不用另学一遍。这里只画：哪几页露出来了（顺序、壳、收起来过没
// 收起来过）由父级算好传进来，点了去哪儿由父级决定。
import type { Topic } from '@/cx_types'

import TopicRailBadge from './TopicRailBadge.vue'

import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'

/** 一列图标，一列文字：每条行的左侧都是「8px 起 + 一个 16px 槽」。 */
const ROW_INDENT = { paddingInlineStart: '8px' }

defineProps<{
  /** 项目本体（全局房间）——它自己的那一行，钉在最上面。 */
  rootTopic: Topic | null
  selectedTopicId: string | null
  /** 这一版前端认得、而且这个项目的壳摆出来了的那几页（顺序就是壳说的顺序）。 */
  pages: { key: string; label: string; icon: string }[]
  /** 当前页的名字，用来画选中态。 */
  routeName: string | null
  /** 壳换了词之后的项目词汇表（「项目文档」可能得叫「工作文档」）。 */
  terms: { project: string; topic: string }
  /** 项目文档那一行是不是选中态（四种文档里任何一种开着都算）。 */
  docsActive: boolean
  /** 私聊未读的总数，挂在「成员」那一行上。 */
  privateUnreadTotal: number
  /** 整页形态（手机）：这几行收进了项目菜单，列表只留话题。 */
  page: boolean
  unreadOf: (id: string) => number
  /** 我静音了的房间：行尾画一个静音标记（未读已经不计了）。 */
  mutedOf?: (id: string) => boolean
}>()

const emit = defineEmits<{
  (e: 'select-topic', id: string): void
  (e: 'hover-topic', id: string): void
  (e: 'press-topic', id: string): void
  (e: 'leave-topic'): void
  (e: 'open-page', key: string): void
  (e: 'hover-page', key: string): void
  (e: 'cancel-prefetch'): void
  (e: 'select-docs'): void
}>()
</script>

<template>
  <v-list density="compact" nav class="py-0 pt-1" tabindex="-1">
    <v-list-item
      v-if="rootTopic"
      tabindex="0"
      :active="rootTopic.id === selectedTopicId"
      rounded="lg"
      class="nav-row pinned-row"
      :class="{ 'is-active': rootTopic.id === selectedTopicId }"
      :style="ROW_INDENT"
      @click="emit('select-topic', rootTopic.id)"
      @mouseenter="emit('hover-topic', rootTopic.id)"
      @mouseleave="emit('leave-topic')"
      @focusin="emit('hover-topic', rootTopic.id)"
      @focusout="emit('leave-topic')"
      @pointerdown="$event.pointerType === 'mouse' && $event.button === 0 && emit('press-topic', rootTopic.id)"
    >
      <template #prepend>
        <!-- 置顶行的槽住的是它自己的图标：# / 看板 / 资料库 各不相同，
             是能区分行的信息，不是话题行上那种每行一模一样的装饰。 -->
        <span class="row-slot">
          <v-icon
            size="16"
            class="row-glyph"
            :class="{ 'row-glyph--unread': unreadOf(rootTopic.id) > 0 }"
            icon="mdi-pound"
          />
        </span>
      </template>
      <v-list-item-title :class="{ 'title-unread': unreadOf(rootTopic.id) > 0 }">{{
        topicTitle(rootTopic)
      }}</v-list-item-title>
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
      </template>
    </v-list-item>
    <!-- 挂在这个房间下面的任务。 -->
    <slot name="root-tasks" />

    <!-- 手机上这几行收进了项目菜单（项目名旁边那颗 ⌄），列表只留话题。 -->
    <v-list-item
      v-for="p in page ? [] : pages"
      :key="p.key"
      tabindex="0"
      :active="routeName === p.key"
      rounded="lg"
      class="nav-row pinned-row"
      :class="{ 'is-active': routeName === p.key }"
      :style="ROW_INDENT"
      @click="emit('open-page', p.key)"
      @mouseenter="emit('hover-page', p.key)"
      @mouseleave="emit('cancel-prefetch')"
    >
      <template #prepend>
        <span class="row-slot">
          <v-icon size="16" class="row-glyph" :icon="p.icon" />
        </span>
      </template>
      <!-- 文案走词表：壳把「项目」叫「工作」的时候，「{project}文档」跟着变。表里
           存的是 i18n key 而不是字面量，正因为壳能换词而组件不能。 -->
      <v-list-item-title>{{ t(p.label, terms) }}</v-list-item-title>
      <!-- 私聊的未读挂在「成员」这一行上。私聊那一栏撤掉之后，这是「有人找你」在
           主导航上唯一会亮的地方，所以它必须在这里；进了成员页才精确到是谁。 -->
      <template v-if="p.key === 'project-members' && privateUnreadTotal > 0" #append>
        <TopicRailBadge :count="privateUnreadTotal" />
      </template>
    </v-list-item>

    <!-- 项目文档：一行。四种文档的切换在页面里，所以它和资料库、成员一样是这个
         项目的一页，排在一起，不压在话题列表底下（话题一多就被挤出视野）。 -->
    <v-list-item
      v-if="!page"
      tabindex="0"
      :active="docsActive"
      rounded="lg"
      class="nav-row pinned-row docs-row"
      :class="{ 'is-active': docsActive }"
      :style="ROW_INDENT"
      @click="emit('select-docs')"
    >
      <template #prepend>
        <span class="row-slot">
          <v-icon size="16" class="row-glyph" icon="mdi-file-document-outline" />
        </span>
      </template>
      <v-list-item-title>{{ t('navigation.project.docs') }}</v-list-item-title>
    </v-list-item>
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
/* 置顶行和下面的话题行、别处侧栏的行（common.scss 的 .side-nav）一样高。 */
.nav-row.pinned-row {
  min-height: 36px;
}
/* 整页形态：手指点的地方至少 44px 高。 */
.topic-rail--page .nav-row {
  min-height: 44px;
}

.row-muted {
  color: var(--faint);
}
</style>
