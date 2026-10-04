<script setup lang="ts">
// 话题列表里的一行 —— 只凭 props 画，凭事件往回说。**它不认识路由，也不取数。**
//
// 行左边只有一个 16px 槽：有子话题 → 折叠开关；否则「等你处理」→ 琥珀点（等的是
// 读这一行的人自己）；都没有就空着（空槽仍占 16px，否则同层级的标题左缘会参差）。
//
// 房间自己没有「在跑」「卡住了」这种状态。行右边画的是成员：此刻在这里干活的队友，
// 和房间在等、等太久了的那一位（`TopicRailMembers`），悬停说出是谁、为什么。
//
// 有子话题的行，开关自己带颜色——「自己的 + 收起来的后代的」并成一个信号：红 = 里面
// 有成员卡住了，黄 = 里面有事等你，绿 = 里面有队友在干活。扫侧栏时要的是「这里面
// 有动静」，收起来不能把它藏掉。
//
// 「哪些行看得见、收起来的行替谁背着未读和动静、这一行画哪几位成员」都是
// `composables/useTopicRail.ts` 的事（`row` / `stalled` / `marks` / `toggleTitle`
// 就是它的答案）。这一份只管那一段模板和它自己的 CSS。
import type { MenuCommand } from '@/commands'
import type { Topic } from '@/cx_types'
import type { RailMemberMark } from '@/lib/memberActivity'
import type { VisibleRow } from '@/lib/topicTree'

import { computed, ref, watch } from 'vue'

import TopicRailBadge from './TopicRailBadge.vue'
import TopicRailMembers from './TopicRailMembers.vue'

import { menuActionOf } from '@/commands'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'
import { TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'
import { countLabel } from '@/lib/topicTree'

const props = defineProps<{
  row: VisibleRow<Topic>
  /** 这一行是不是当前打开的那个房间。 */
  selected: boolean
  /** 整页形态（手机）：没有 hover 浮出来的 ⋯，那里长按一行打开同一组操作。 */
  page: boolean
  /** 这一行正在就地改名。 */
  renaming: boolean
  /** 这一行的 ⋯ 菜单开着——开着的时候那颗按钮必须留屏幕上，它是菜单的根。 */
  menuOpen: boolean
  /** 父级带钟算出来的：这一行里有没有成员卡住了（自己的，不含收起来的后代）。 */
  stalled: boolean
  /** 这一行右边画的那几位成员（在干活的、卡住了的），父级带钟算好。 */
  marks?: RailMemberMark[]
  /** 折叠开关 hover 时说的那句话（里面有什么，父级知道）。 */
  toggleTitle: string
  /** 这一行的 ⋯ 里有哪几项（要 router 才算得出链接，所以由父级给）。 */
  actions: (topic: Topic) => MenuCommand[]
  /** 我静音了这间房：未读不计数（父级已经去掉了），行尾留一个静音标记说明为什么。 */
  muted?: boolean
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
}>()

// 折叠开关的颜色：自己的状态 + 收起来的后代的，并成一个信号（红光最亮，压过其余）。
const rowStalled = computed(() => props.stalled || props.row.hiddenStalled)
const rowAwaits = computed(() => props.row.topic.awaits_me === true || props.row.hiddenAwaits)
const rowWorking = computed(() => (props.marks ?? []).some((m) => m.state === 'working') || props.row.hiddenWorking)

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
    if (on) draftTitle.value = props.row.topic.title_source === 'placeholder' ? '' : props.row.topic.title
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
    class="topic-row"
    :class="{
      'topic-row--hover-actions': !page,
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
    <!-- 干净行：左边只有一个 16px 槽（状态，或顶替它的折叠开关），身份靠标题本身，
         种类标签不要（缩进表达层级），操作 hover 才浮现。原先这里还有一颗每行都
         一样的装饰图标——同一层级里人人相同的标记区分不了任何东西，删掉了。 -->
    <template #prepend>
      <button
        v-if="row.hasChildren"
        type="button"
        class="row-slot subtree-toggle"
        :class="{
          'tap-target': page,
          'subtree-toggle--stalled': rowStalled,
          'subtree-toggle--awaits': !rowStalled && rowAwaits,
          'subtree-toggle--working': !rowStalled && !rowAwaits && rowWorking,
        }"
        :title="toggleTitle"
        :aria-expanded="!row.collapsed"
        @click.stop="emit('toggle-collapse', row.topic.id)"
      >
        <v-icon size="15">
          {{ row.collapsed ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
        </v-icon>
      </button>
      <!-- 等你处理：有点名给你的验收卡、没答的决策请求，或芝士停在一道只有你能
           回答的问题上。未读的 @ 不点这颗灯——芝士汇报、递卡都 @人，算进来几乎
           每行都亮，灯就没意义了；未读有右边的数字。 -->
      <span v-else-if="row.topic.awaits_me" class="row-slot">
        <span class="await-dot" :title="t('work.sidebar.awaitsTip')" />
      </span>
      <span v-else class="row-slot" />
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
          :class="{ 'title-unread': row.unreadTotal > 0 }"
          :data-user-content="row.topic.title || undefined"
          :title="row.topic.title_source === 'auto' ? t('work.sidebar.autoTitle') : undefined"
          >{{ topicTitle(row.topic) }}</span
        >
        <!-- 收起来了就说清楚收了多少——「这里还有内容」得看得见。 -->
        <span
          v-if="row.collapsed && row.hiddenCount > 0"
          class="subtree-count ms-2"
          :title="t('work.sidebar.hiddenCount', { count: row.hiddenCount })"
          >{{ countLabel(row.hiddenCount) }}</span
        >
        <span v-if="statusBadge(row.topic.status)" class="d-inline-flex align-center ga-1 c-faint topic-status ms-2">
          <span class="status-dot status-dot--warn" />
          {{ statusBadge(row.topic.status) }}
        </span>
      </template>
    </v-list-item-title>
    <template #append>
      <TopicRailMembers v-if="marks?.length" :marks="marks" class="me-1" />
      <v-icon
        v-if="muted"
        size="14"
        class="row-muted me-1"
        icon="mdi-bell-off-outline"
        :aria-label="t('work.room.menu.muted')"
        :title="t('work.room.menu.muted')"
      />
      <!-- 折叠不能把「有新消息」吞掉：收起来的后代的未读加到本行上。 -->
      <TopicRailBadge
        v-if="row.unreadTotal > 0"
        :count="row.unreadTotal"
        :title="row.hiddenUnread > 0 ? t('work.sidebar.hiddenUnread', { count: row.hiddenUnread }) : undefined"
      />
      <!-- hover 浮出的操作入口：一颗 ⋯，绝对定位覆盖行尾，不占布局宽度。
           整页形态（手机）没有它：那里长按一行打开同一组操作。 -->
      <div v-if="!page" class="row-actions" @click.stop>
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

/* Hover-only action overlay: absolutely positioned over the row's tail, zero
   layout width — the title gets the full rail. Hover detection is the WHOLE row
   (the old flicker came from hovering the buttons themselves). */
.topic-row {
  position: relative;
  min-height: 36px;
  margin-block: 2px;
}
.topic-row :deep(.v-list-item__content) {
  padding-block: 0;
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
/* 收起来的父话题会把子话题里成员的动静整个藏掉——开关自己带聚合色补上：红 = 里面
   有成员卡住了，黄 = 里面有事等你，绿 = 里面有队友在干活。hover 不改这三个颜色，
   状态优先于反馈。 */
/* !important 是被逼的，不是偷懒：上面 .topic-row.is-active :deep(.v-icon) 为了
   压住 Vuetify 的琥珀 active overlay 用了 !important，选中的那一行会连带把这里
   的状态色刷成 --muted——正好是「这一行收起来了、里面有事等你」最该看见的时候。 */
.subtree-toggle--stalled :deep(.v-icon),
.subtree-toggle--stalled:hover :deep(.v-icon) {
  color: var(--danger) !important;
}
.subtree-toggle--awaits :deep(.v-icon),
.subtree-toggle--awaits:hover :deep(.v-icon) {
  color: var(--signal-yellow) !important;
}
.subtree-toggle--working :deep(.v-icon),
.subtree-toggle--working:hover :deep(.v-icon) {
  color: var(--ok) !important;
}

/* 等你拍板：黄点。黄不用琥珀/橙：右边的未读数字就是琥珀色，同色会让人把「有新消息」
   和「等你拍板」读成一回事。 */
.await-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--signal-yellow);
}
/* 收起来了收了几个——形态沿用「已归档」那颗计数丸。 */
.subtree-count {
  flex: none;
  font-size: 12px;
  color: var(--faint);
  background: var(--fill);
  border-radius: 8px;
  padding: 1px 6px;
  font-variant-numeric: tabular-nums;
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
  .topic-row .unread-badge {
    opacity: 1;
  }
}
/* 菜单展开时那颗 ⋯ 必须留着：它是菜单的 activator，跟 hover 一起消失的话
   鼠标一离开行、菜单就没了根。
   键盘焦点这里用 :focus-visible（行自己）加 :has(:focus-visible)（行里的 ⋯），不用
   :focus-within：鼠标点一下行也会让它拿到焦点，:focus-within 从那以后一直命中，⋯ 就
   挂在行尾不走了（同一个先例见 style.css 的 .fb-row:has(.fbrow__link:focus-visible)）。
   行本身可聚焦，而 :has() 只看后代，所以 Tab 停在行上那一条要单写。 */
.topic-row:hover .row-actions,
.topic-row:focus-visible .row-actions,
.topic-row:has(:focus-visible) .row-actions,
.topic-row.is-menu-open .row-actions {
  opacity: 1;
  pointer-events: auto;
}
/* While the actions are out, the count steps aside (they share the tail). 只在有那颗
   ⋯ 的行上：手机上点过一行之后 :hover 会一直粘着，未读数不能因此消失。 */
.topic-row--hover-actions:hover .unread-badge,
.topic-row--hover-actions:focus-visible .unread-badge,
.topic-row--hover-actions:has(:focus-visible) .unread-badge,
.topic-row.is-menu-open .unread-badge {
  opacity: 0;
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

.row-muted {
  color: var(--faint);
}
</style>
