<script setup lang="ts">
// 频道行只收 props、发事件；隐藏后代和收起任务的状态共用行尾一盏灯。
// 子话题和任务是两种折叠，保留各自的箭头，均放在名称前。
import type { MenuCommand } from '@/commands'
import type { Topic } from '@/cx_types'
import type { RailMemberMark } from '@/lib/memberActivity'
import type { RowLight } from '@/lib/roomLight'
import type { VisibleRow } from '@/lib/topicTree'

import { computed, ref, watch } from 'vue'

import TopicRailLight from './TopicRailLight.vue'
import TopicRailTaskFold from './TopicRailTaskFold.vue'

import { menuActionOf } from '@/commands'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { brightest } from '@/lib/roomLight'
import { channelGlyph, topicTitle } from '@/lib/topicState'
import { TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'

const props = defineProps<{
  row: VisibleRow<Topic>
  /** 这一行是不是当前打开的那个房间。 */
  selected: boolean
  /** 整页形态（手机）：操作入口常驻，使用触控尺寸。 */
  page: boolean
  /** 这一行正在就地改名。 */
  renaming: boolean
  /** 这一行的 ⋯ 菜单开着——开着的时候那颗按钮必须留屏幕上，它是菜单的根。 */
  menuOpen: boolean
  /** 父级带钟算出来的：这一行里有没有成员卡住了（自己的，不含收起来的后代）。 */
  stalled: boolean
  /** 卡住成员的原因，仅供唯一状态灯的文字说明。 */
  marks?: RailMemberMark[]
  /** 这一行此刻画哪盏灯（父级带钟算的）；不亮是 null。 */
  light?: RowLight | null
  /** 折叠开关 hover 时说的那句话（里面有什么，父级知道）。 */
  toggleTitle: string
  /** 这一行的 ⋯ 里有哪几项（要 router 才算得出链接，所以由父级给）。 */
  actions: (topic: Topic) => MenuCommand[]
  /** 主线上有我没读过的新消息：名字加粗（数字按通知档位另算，可以是 0）。 */
  fresh?: boolean
  /** 挂在它下面的那一块任务收起来了没有。没给就是不收——这个频道没挂任务。 */
  tasksCollapsed?: boolean
  /** 这个频道进行中的任务数；只用于决定是否提供任务折叠入口。 */
  taskCount?: number
  /** 收起来的那一块该画的灯（父级带钟算好）；不亮是 null。 */
  taskLight?: RowLight | null
}>()

const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'hover', id: string): void
  (e: 'leave'): void
  /** 按下去了（还没松开）：不必再等「停住」，直接预取。 */
  (e: 'press', id: string): void
  (e: 'toggle-collapse', id: string): void
  /** 改名提交（回车或失焦）：值没变就不落盘，由父级比对原名字决定。 */
  (e: 'commit-rename', draft: string): void
  (e: 'cancel-rename'): void
  (e: 'update:menu-open', open: boolean): void
  /** 挂在它下面的那一整块任务收起来或展开。 */
  (e: 'toggle-tasks', id: string): void
}>()

// 每行只显示一盏：保留已有状态事实和优先级，不另取任务或重算状态。
const rowLight = computed<RowLight | null>(() =>
  brightest([
    props.light,
    props.row.hiddenLight,
    props.stalled || props.row.hiddenStalled ? { kind: 'red', breathe: false } : null,
    props.tasksCollapsed ? props.taskLight : null,
  ])
)

const rowLightLabel = computed(() => {
  if (props.stalled)
    return (
      props.marks
        ?.filter((mark) => mark.state === 'stalled')
        .map((mark) => mark.title)
        .join('\n') || undefined
    )
  return props.row.hiddenStalled ? t('work.sidebar.hiddenStalled') : undefined
})

// Status: only show when notable (archived / draft); active is implicit. Shown as a
// small neutral dot + text, never a colored chip.
function statusBadge(status: string): string | null {
  if (status === 'archived') return t('work.sidebar.archived')
  if (status === 'draft') return t('work.topicState.draft')
  return null
}

// 就地改名：草稿归这一行，起点是它现在的名字（原来草稿放在父级，父级只有一个，
// 改两行时上一次的草稿会漏到下一行）。
const draftTitle = ref('')
watch(
  () => props.renaming,
  (on) => {
    // 还没名字的话题从空白开始改：占位标题不是谁起的名字。
    if (on) draftTitle.value = props.row.topic.title
  },
  { immediate: true }
)

// 右键一行，弹出的就是 ⋯ 那一份操作，只是弹在鼠标那一点上。正在改名时右键留给
// 输入框（复制、粘贴）。点 ⋯ 打开时照旧挂在 ⋯ 下面。
const menuPoint = ref<[number, number] | null>(null)
function openMenuAt(e: MouseEvent) {
  if (props.page || props.renaming) return
  e.preventDefault()
  menuPoint.value = [e.clientX, e.clientY]
  emit('update:menu-open', true)
}
function onMenuToggle(open: boolean) {
  if (!open) menuPoint.value = null
  emit('update:menu-open', open)
}
</script>

<template>
  <!-- 行自己是一个 Tab 停靠点：Vuetify 给列表里的可点行标 tabindex="-2"，
       Tab 就走不到行上、只落在行尾那颗 ⋯ 上（2026-10-03 lz123y 报的）。 -->
  <v-list-item
    tabindex="0"
    :data-room-id="row.topic.id"
    :data-row-actions="row.topic.id"
    :active="selected"
    rounded="lg"
    class="topic-row rail-channel"
    :class="{
      'rail-channel-page': page,
      'is-active': selected,
      'is-sub': row.depth > 0,
      'is-menu-open': menuOpen,
    }"
    :style="{
      paddingInlineStart: 8 + row.depth * 20 + 'px',
      '--guide-x': 16 + (row.depth - 1) * 20 + 'px',
    }"
    @click="emit('select', row.topic.id)"
    @mouseenter="emit('hover', row.topic.id)"
    @mouseleave="emit('leave')"
    @focusin="emit('hover', row.topic.id)"
    @focusout="emit('leave')"
    @pointerdown="$event.pointerType === 'mouse' && $event.button === 0 && emit('press', row.topic.id)"
    @contextmenu="openMenuAt"
  >
    <!-- Independent fold controls share a fixed leading slot; private/type markers stay visible. -->
    <template #prepend>
      <span class="row-leading">
        <button
          v-if="row.hasChildren"
          type="button"
          class="row-slot subtree-toggle"
          :title="toggleTitle"
          :aria-label="toggleTitle"
          :aria-expanded="!row.collapsed"
          @click.stop="emit('toggle-collapse', row.topic.id)"
        >
          <v-icon size="15">
            {{ row.collapsed ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
          </v-icon>
        </button>
        <TopicRailTaskFold
          v-if="taskCount"
          :collapsed="tasksCollapsed === true"
          :page="page"
          @toggle="emit('toggle-tasks', row.topic.id)"
        />
        <span v-if="!row.hasChildren && (!taskCount || channelGlyph(row.topic) !== 'mdi-pound')" class="row-slot">
          <v-icon
            size="16"
            class="row-glyph"
            :class="{ 'row-glyph--unread': row.unreadTotal > 0 || fresh }"
            :icon="channelGlyph(row.topic)"
            :title="row.topic.members_only ? t('work.channel.privateTip') : undefined"
          />
        </span>
      </span>
    </template>
    <v-list-item-title class="d-flex align-center topic-title">
      <v-text-field
        v-if="renaming"
        v-model="draftTitle"
        autocomplete="off"
        density="compact"
        variant="outlined"
        autofocus
        :maxlength="TOPIC_TITLE_MAX_LENGTH"
        :counter="TOPIC_TITLE_MAX_LENGTH"
        class="rename-field"
        @click.stop
        @keyup.enter="emit('commit-rename', draftTitle)"
        @keyup.esc="emit('cancel-rename')"
        @blur="emit('commit-rename', draftTitle)"
      />
      <template v-else>
        <span
          class="text-truncate"
          :class="{
            'title-unread': row.unreadTotal > 0 || fresh,
            'title-visiting': row.topic.joined === false,
          }"
          :data-user-content="row.topic.title || undefined"
          >{{ topicTitle(row.topic) }}</span
        >
        <span v-if="statusBadge(row.topic.status)" class="d-inline-flex align-center ga-1 c-faint topic-status ms-2">
          <span class="status-dot status-dot--warn" />
          {{ statusBadge(row.topic.status) }}
        </span>
      </template>
    </v-list-item-title>
    <template #append>
      <TopicRailLight v-if="rowLight" :light="rowLight" :label="rowLightLabel" />
      <!-- The menu has its own layout slot, including on touch screens. -->
      <div class="row-actions" @click.stop>
        <AdaptiveMenu
          :model-value="menuOpen"
          :actions="actions(row.topic).map(menuActionOf)"
          :title="topicTitle(row.topic)"
          :point="menuPoint"
          @update:model-value="onMenuToggle"
        >
          <template #activator="{ props: menuProps }">
            <!-- eslint-disable-next-line vue/no-restricted-syntax -- nav bar button whose look this component styles exactly (design-system §3.6 exception) -->
            <v-btn
              v-bind="menuProps"
              :tabindex="selected ? 0 : -1"
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
</template>

<style scoped>
/* Topic row: title ink, quiet by default.

   行盒必须跟着字号一起给。v-list 的 nav 变体把 .v-list-item-title 的行盒钉在
   1rem（16px）上，与这里的字号无关；而 14px 的字身（PingFang 这类 CJK 字体约
   1.4em ≈ 19.6px）比 16px 的行盒还高，标题又自带 overflow: hidden —— 高出来的
   那 1.8px 上下各切一刀，g / y / p 这些下伸的字母下缘就被切平。汉字不下伸，
   所以只有拉丁字母看得出来。行盒高度是字号阶梯的属性（docs/design-system.md
   §3.2），这里照 --lh-14 取，和 style.css 里菜单列表项那条规则一致。 */
.topic-row :deep(.v-list-item-title) {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}
.topic-title {
  color: var(--text);
}
.rename-field {
  max-width: 220px;
}
.rename-field :deep(.v-field__input) {
  padding-top: 2px;
  padding-bottom: 2px;
  min-height: 28px;
  font-size: 14px;
}
/* 未读：标题自己带这个信号。 */
.title-unread {
  font-weight: 650;
  color: var(--text);
}
/* 正在看、还没加入的频道：斜体，说明它只是此刻摆在这里（离开就不在侧栏了）。 */
.title-visiting {
  font-style: italic;
  color: var(--muted);
}

/* 三态：静默（透明，露出 rail 的 --canvas）/ hover --fill-2 / 选中 --line-2。
   没有琥珀左竖条——选中态靠底色和字重就够了（Slack/Discord 的行选中态也只是底色），
   左条纹在这套设计语言里只留给引用块和树的结构线。

   为什么不是 --fill：--fill 的定义就是「hover on --surface」，而这条 rail 坐在
   --canvas 上。浅色主题里 --fill #f4f5f7 压在 --canvas #f7f8fa 上对比度只有
   1.027:1（3/255），等于看不见；hover 还用同一个值，两态也彼此不可分。

   为什么两个主题共用一组 token：三档明暗次序在两个主题间是反的（浅色
   surface > canvas > fill > fill-2，深色 fill-2 > fill > surface > canvas），
   所以选的依据是**对 --canvas 的对比度**而不是名字。--fill-2 → --line-2 这一对在
   两个主题下都是单调递增地离开 canvas，所以不需要任何 [data-theme='dark'] 分支。
   Kill Vuetify's default active overlay so no amber bleeds in. */
.topic-row.is-active {
  background: var(--line-2);
}
.topic-row.is-active :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}
.topic-row.is-active :deep(.v-list-item-title) {
  color: var(--ink);
  font-weight: 600;
}
.topic-row.is-active :deep(.v-icon) {
  color: var(--muted) !important;
}
.topic-row:hover {
  background: var(--fill-2);
}
/* 选中的行 hover 不能倒退回 hover 档——否则鼠标一扫过，选中态反而变浅。 */
.topic-row.is-active:hover {
  background: var(--line-2);
}

.topic-status {
  font-size: 13px;
}

/* 核心修正：Vuetify 的 prepend spacer 默认 ~32px，把图标和标题隔出一条鸿沟，
   稀释了一切缩进关系。压到 8px，缩进的台阶才立得起来。 */
.topic-row :deep(.v-list-item__spacer) {
  width: 8px !important;
}
/* 图标槽统一成 16px 定宽方块：锁定 prepend 里图标的位置，icon-left 与 text-left
   才能双双成列。 */
.topic-row :deep(.v-list-item__prepend) {
  align-items: center;
}

/* 行左边那一个 16px 定宽槽。所有行共用（话题行的状态/开关、置顶行的图标），
   所以图标列和文字列在整条侧栏上都成列。空槽也占满 16px：同层级的标题左缘
   必须齐，参差比多一点留白难看得多。 */
.row-glyph {
  color: var(--faint);
}
.row-glyph--unread {
  color: var(--accent);
}
.row-slot {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
}

/* 子话题折叠开关：占同一个槽，顶替状态点。 */
.subtree-toggle {
  border-radius: var(--radius-sm);
  cursor: pointer;
}
/* 开关不给底色 hover：它坐在的行底色有三档（静默/hover/选中），任何一个固定
   的底色 token 都会在其中某一档上糊掉。改成图标本身变深——16px 的小控件靠
   墨色变化做反馈就够，也不用跟行底色抢层次。 */
.subtree-toggle :deep(.v-icon) {
  color: var(--faint);
}
.subtree-toggle:hover :deep(.v-icon) {
  color: var(--text);
}
/* 分身组的竖向引导线：把一串子话题挂在父话题下（Linear/Notion 树形手法）。
   这是结构线，不是强调条——左条纹禁令不管它。 */
.topic-row.is-sub::before {
  content: '';
  position: absolute;
  left: var(--guide-x, 24px);
  top: -3px;
  bottom: -3px;
  width: 1px;
  background: var(--line-2);
}

/* 整页形态：手指点的地方至少 44px 高；改名的输入框 16px，iOS 聚焦时才不会整页放大。 */
.topic-rail--page .topic-row {
  min-height: 44px;
}
.topic-rail--page .subtree-toggle {
  position: relative;
}
.topic-rail--page .rename-field {
  max-width: none;
}
.topic-rail--page .rename-field :deep(.v-field__input) {
  min-height: 36px;
  font-size: 16px;
}
</style>

<style src="./rail-channel-row.css"></style>
