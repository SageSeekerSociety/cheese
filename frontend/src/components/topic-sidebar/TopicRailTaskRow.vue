<script setup lang="ts">
// 侧栏里挂在频道下面的一个任务。比频道的字往里缩一点，左边一条竖线从频道的 # 下面
// 一直连到「展开其余」那一行，说「这几条是那个频道里的」；字和频道一样大。
//
// 行尾是那颗灯（`light`，父级按 `lib/roomLight.ts` 算好）：哪几盏、怎么排先后、
// 红灯说的是哪件事都在那一份里，这里只负责把它画出来。灯上只画一盏，蓝垫在状态那几
// 盏下面——「这条现在怎么了」比「这条归谁」先要知道。不亮就不画。
//
// 这一条不是和我有关的、是频道里别人的、被借来凑够三条的（`task.filling_in`）：整个
// 标题淡一档，鼠标停上去的那句说明写清它为什么在这儿 —— 不然侧栏里突然多出一条我没
// 参与过的活，看起来像我漏看了什么。
//
// 行的操作和频道行一样收进一颗 ⋯（悬停浮出，右键弹在同一处，手机上长按由父级那个
// `useLongPress` 升起面板）。眼下只有「重命名」一项，只画在和我有关的那些行上：借来
// 凑数的行不画，后端也只让参加这件活的人改。
import type { MenuCommand } from '@/commands'
import type { RowLight } from '@/lib/roomLight'
import type { TaskState } from '@/lib/taskState'
import type { RailTask } from './moreTasks'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import TopicRailLight from './TopicRailLight.vue'

import { menuActionOf } from '@/commands'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import { t } from '@/i18n'
import { taskTitle } from '@/lib/topicState'

const props = withDefaults(
  defineProps<{
    task: RailTask
    selected: boolean
    /** 这一行此刻画哪盏灯（父级带钟算的）；不亮是 null。 */
    light?: RowLight | null
    /** 行首的状态记号和标题下面那一行（父级按 `lib/taskState.ts` 算好）；不给就不画。 */
    state?: TaskState | null
    /** 任务里别人说了几句我还没读（只对负责人和协作者算，人和 AI 都算）。 */
    unread?: number
    // 它的频道在树里的第几层：竖线跟着频道的图标列走。
    depth?: number
    /** 整页形态（手机）：没有 hover 浮出的 ⋯，长按由父级升起同一份操作。 */
    page?: boolean
    /** 这一行正在就地改名。 */
    renaming?: boolean
    /** 这一行的 ⋯ 里有哪些项：和任务页右上角同一份清单（`commands/taskActions.ts`），
     *  父级已经按「行里敢点的」筛过。要 router 才算得出链接，所以由父级给；不给
     *  （组件预览、只画一行的宿主）就没有 ⋯，不是少传了参数。 */
    actions?: (task: RailTask) => MenuCommand[]
  }>(),
  { depth: 0, unread: 0, light: null, state: null, page: false, renaming: false, actions: undefined }
)

const fillingIn = computed(() => props.task.filling_in === true)
const actionsOf = computed(() => (props.actions ? props.actions(props.task) : []))

const emit = defineEmits<{
  (e: 'select', task: { roomId: string; taskId: string }): void
  // 鼠标左键按下去了：意图定了，不必再等指针停满 hover 那一百多毫秒（和频道行
  // `TopicRailRow` 的 `press` 同一条理由）。任务页的代码是懒加载的，早下这一百多
  // 毫秒，打开它就少等这一段。
  (e: 'press', task: { roomId: string; taskId: string }): void
  /** 回车或失焦：父级比对原名字，值没变就不落盘。 */
  (e: 'commit-rename', draft: string): void
  (e: 'cancel-rename'): void
}>()

// 点下去到任务页出来之间，这一行一直亮着：只靠按下的那一下，手指抬起后又变回原样，
// 等得久的人会以为没点到（同 TopicRailRow）。
const pending = ref(false)
let pendingTimer: number | undefined
function choose(): void {
  // 点的就是当前这一行：不跳转，也就没有「等它选中」这回事，别亮着不放。
  if (props.selected) {
    emit('select', { roomId: props.task.room_id, taskId: props.task.id })
    return
  }
  pending.value = true
  window.clearTimeout(pendingTimer)
  pendingTimer = window.setTimeout(() => (pending.value = false), 10_000)
  emit('select', { roomId: props.task.room_id, taskId: props.task.id })
}
watch(
  () => props.selected,
  (on) => {
    if (!on) return
    pending.value = false
    window.clearTimeout(pendingTimer)
  }
)
onBeforeUnmount(() => window.clearTimeout(pendingTimer))

// 就地改名：草稿归这一行，起点是它现在的名字（和频道行同一套：父级只说「这一行在
// 改名」，草稿不往上走）。
const draftTitle = ref('')
watch(
  () => props.renaming,
  (on) => {
    if (on) draftTitle.value = props.task.title
  },
  { immediate: true }
)
function commitRename() {
  emit('commit-rename', draftTitle.value)
}
function cancelRename() {
  // Esc 先关掉输入框，随后的 blur 不能再按「提交」走一遍。
  draftTitle.value = props.task.title
  emit('cancel-rename')
}

// 右键一行，弹出的就是 ⋯ 那一份操作，只是弹在鼠标那一点上。正在改名时右键留给
// 输入框（复制、粘贴）。点 ⋯ 打开时照旧挂在 ⋯ 下面。
const menuOpen = ref(false)
const menuPoint = ref<[number, number] | null>(null)
function openMenuAt(e: MouseEvent) {
  if (props.page || props.renaming || !actionsOf.value.length) return
  e.preventDefault()
  menuPoint.value = [e.clientX, e.clientY]
  menuOpen.value = true
}
function onMenuToggle(open: boolean) {
  if (!open) menuPoint.value = null
  menuOpen.value = open
}
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- contextmenu bubbles here from the row's own button and its ⋯ (the keyboard's Menu key fires it too); the ⋯ button opens the same menu -->
  <div
    class="rail-task-wrap"
    :class="{
      'rail-task-wrap--selected': selected,
      'rail-task-wrap--pending': pending,
      'is-menu-open': menuOpen,
      'is-renaming': renaming,
    }"
    :data-task-actions="!renaming && actionsOf.length ? task.id : undefined"
    @contextmenu="openMenuAt"
  >
    <!-- 改名时这一行换成一个输入框：回车或失焦提交，Esc 不改。 -->
    <div
      v-if="renaming"
      class="rail-task rail-task--renaming"
      :style="{ '--task-indent': 32 + depth * 20 + 'px', '--guide-x': 16 + depth * 20 + 'px' }"
    >
      <!-- eslint-disable vuejs-accessibility/no-autofocus -- the field replaces the title because the person chose Rename; focus moves into it -->
      <v-text-field
        v-model="draftTitle"
        autocomplete="off"
        density="compact"
        variant="outlined"
        autofocus
        maxlength="80"
        class="rail-task__rename-field"
        data-testid="task-rename-field"
        @click.stop
        @keyup.enter="commitRename"
        @keyup.esc="cancelRename"
        @blur="commitRename"
      />
      <!-- eslint-enable vuejs-accessibility/no-autofocus -->
    </div>
    <button
      v-else
      type="button"
      class="rail-task"
      :class="{ 'rail-task--selected': selected, 'rail-task--filled': fillingIn, 'rail-task--pending': pending }"
      :style="{ '--task-indent': 32 + depth * 20 + 'px', '--guide-x': 16 + depth * 20 + 'px' }"
      :aria-current="selected ? 'page' : undefined"
      :title="fillingIn ? t('work.sidebar.fillInTip') : undefined"
      @click="choose"
      @pointerdown="
        $event.pointerType === 'mouse' &&
          $event.button === 0 &&
          emit('press', { roomId: task.room_id, taskId: task.id })
      "
    >
      <TaskStateMark v-if="state" class="rail-task__state" :state="state" :size="14" icon-only />
      <span class="rail-task__text">
        <!-- 任务名是人起的（占位名除外，那是界面上的字）。 -->
        <span
          class="rail-task__title"
          :class="{ 'rail-task__title--unread': unread > 0 }"
          :data-user-content="task.title_source === 'placeholder' ? undefined : ''"
          >{{ taskTitle(task) }}</span
        >
        <span v-if="state?.line" class="rail-task__line" data-testid="rail-task-line">{{ state.line }}</span>
      </span>
      <TopicRailLight v-if="light" :light="light" />
    </button>
    <!-- hover 浮出的操作入口：一颗 ⋯，覆盖在行尾，不占布局宽度。整页形态（手机）
         没有它：那里长按一行，父级升起同一份操作。 -->
    <div v-if="!page && actionsOf.length && !renaming" class="rail-task__actions" @click.stop>
      <AdaptiveMenu
        :model-value="menuOpen"
        :actions="actionsOf.map(menuActionOf)"
        :title="taskTitle(task)"
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
            class="rail-task__actions-btn"
          />
        </template>
      </AdaptiveMenu>
    </div>
  </div>
</template>

<style scoped>
/* 行自己不改宽度：底下的按钮撑满，操作入口绝对定位在行尾。 */
.rail-task-wrap {
  position: relative;
}
.rail-task {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 32px;
  padding-block: 0;
  /* 文字的左缘；行首的状态记号按它往左摆（`.rail-task__state`）。 */
  --text-start: calc(var(--task-indent) + 20px);
  padding-inline-start: var(--text-start);
  padding-inline-end: 12px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  font-family: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rail-task:hover {
  background: var(--fill-2);
  color: var(--ink);
}
/* 按下那一帧就换底色：触屏没有 hover，任务页又要等一下才出来。 */
.rail-task:active {
  background: var(--line-2);
  color: var(--ink);
}
/* 手指抬起之后到任务页出来之前，这一行保持亮着。 */
.rail-task--pending {
  background: var(--line-2);
  color: var(--ink);
}
/* 选中沿用频道行那一档：底色 --line-2，标题再重一层。这条 rail 的底是 --canvas，
   --fill 在它上面只有 1.027:1，拿它当选中等于没画（同 TopicRailRow.vue 那段注释）。
   字重落在行上、由标题继承。选中的行不复用 hover 档——否则鼠标一扫过，选中态反而变浅。 */
.rail-task--selected,
.rail-task--selected:hover {
  background: var(--line-2);
  color: var(--ink);
  font-weight: 600;
}
/* 竖线落在频道 # 的中线上，一行接一行连成一条（上下各探 3px 盖住行间的空隙）。
   结构线，不是强调条。 */
.rail-task::before {
  content: '';
  position: absolute;
  left: var(--guide-x);
  top: -3px;
  bottom: -3px;
  width: 1px;
  background: var(--line-2);
}
/* 记号挂在文字前面的缩进里，文字的左缘不动（和频道行、「展开其余」那一行对齐）。 */
.rail-task__state {
  position: absolute;
  left: calc(var(--text-start) - 22px);
  top: 9px;
}
.rail-task__text {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  padding-block: 6px;
}
.rail-task__title,
.rail-task__line {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 标题下面那一行：在做什么、在等什么、卡在哪、待谁审阅。 */
.rail-task__line {
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
}
/* 未读只给名称加粗，不再另挂数字。 */
.rail-task__title--unread {
  font-weight: 650;
}
/* 借来凑数的那几条退到次要色：它们不是我的活，只是把频道里的三行凑齐。选中和 hover 要
   自己再抬一次——上面那两条改的是行盒的颜色，标题自己有颜色时继承不到。 */
.rail-task--filled .rail-task__title {
  color: var(--muted);
}
.rail-task--filled:hover .rail-task__title,
.rail-task--filled.rail-task--selected .rail-task__title {
  color: var(--ink);
}
/* 改名时那一行不改底色、也不像是能点的：它现在是一只输入框。 */
.rail-task--renaming {
  cursor: default;
}
.rail-task__rename-field {
  flex: 1 1 auto;
  max-width: 220px;
}
.rail-task__rename-field :deep(.v-field__input) {
  padding-top: 2px;
  padding-bottom: 2px;
  min-height: 28px;
  font-size: 14px;
}
/* 行尾那颗 ⋯：和频道行同一套浮出规则。白底细边、微影，落在任何行底色上都成立。 */
.rail-task__actions {
  position: absolute;
  right: 5px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  opacity: 0;
  pointer-events: none;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
  transition: opacity var(--dur-quick) var(--ease-standard);
  color: var(--muted);
}
/* 菜单展开时那颗 ⋯ 必须留着：它是菜单的 activator，跟 hover 一起消失的话
   鼠标一离开行、菜单就没了根。键盘焦点用 :has(:focus-visible) 而不是 :focus-within：
   鼠标点一下行也会让它拿到焦点，从那以后 ⋯ 就挂在行尾不走了（同 TopicRailRow）。 */
.rail-task-wrap:hover .rail-task__actions,
.rail-task-wrap:has(:focus-visible) .rail-task__actions,
.rail-task-wrap.is-menu-open .rail-task__actions {
  opacity: 1;
  pointer-events: auto;
}
.rail-task__actions :deep(.v-btn) {
  width: 31px;
  height: 29px;
  border-radius: var(--radius-lg);
  cursor: pointer;
}
.rail-task__actions :deep(.v-btn .v-icon) {
  font-size: 16px;
}
.rail-task__actions :deep(.v-btn:hover) {
  background: var(--fill);
  color: var(--text);
}
/* 触摸屏没有 hover：⋯ 常驻。键盘焦点照样够得着它。 */
@media (hover: none) {
  .rail-task__actions {
    opacity: 1;
    pointer-events: auto;
  }
}
</style>
