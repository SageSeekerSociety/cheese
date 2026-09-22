<script setup lang="ts">
import type { FeedbackDetail, FeedbackPriority, FeedbackStatus } from '@/cx_types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminAssigneeSelect from '@/components/admin/AdminAssigneeSelect.vue'
import StatusRail from '@/components/admin/StatusRail.vue'
import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import { KIND_LABEL, PRIORITY_META, SOURCE_LABEL, statusMeta } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

/**
 * AdminQueueDetail.vue — 一条反馈的详情（§4.4），三个断点共用这一个组件：
 *
 *   ≥1280  两栏：440px 左栏 + `minmax(0,1fr)` 右栏，64px 操作栏粘底
 *   <1280  一栏（右栏那 520px 抽屉里就是这个组件的窄形态）
 *   <768   一栏，操作栏照样粘底
 *
 * **三种形态是同一份模板 + 一条媒体查询**，不是三个组件。上一版把「宽屏那一栏」和
 * 「窄屏那个抽屉」写成了两套内容，于是详情里每加一个字段都要写两遍，而漏掉的那一遍
 * 在另一个断点上才看得见 —— 那种 bug 只有在别人换屏幕时才被抓到。
 *
 * **内容不在这里拉。** id 与防抖归调用方（F-06 的 150ms + `detailSeq` 在队列页），
 * 因为「手上这一条是谁」是页面级的判断（列表光标、抽屉开关都算进去），放在这一层会
 * 变成第二个「当前是哪条」。这里只画 `item`，以及在 `item` 为空时画加载 / 打不开。
 *
 * **左栏那一块分诊是「分诊面板」**（§8 的 `Esc` 逐层后退、§9 验收第 23 条）：它收起
 * 时只是一行只读摘要（状态 · 指派 · 优先级），展开才出现四个控件。收起的默认值由
 * 调用方给（宽屏默认展开、抽屉里默认收起），收起状态也是调用方的 —— 因为 `Esc` 要
 * 「一次只退一层」，而那一层的顺序由谁拿着详情谁决定。这里的按钮只负责把用户的意图
 * 报上去。
 *
 * 写操作分两路：**状态**从 `triage` 出去（调用方要记撤销条，那是页面级的栈），
 * **指派 / 优先级 / 安全问题 / 内部备注**直接落到 store —— 它们没有撤销条（撤销是给
 * 单键误触的保险，而这几样都要先点开面板、再选一个值）。
 */
defineOptions({ name: 'AdminQueueDetail' })

const props = defineProps<{
  item: FeedbackDetail | null
  loading: boolean
  /** 拉取失败时的原话。为空表示这一趟没出错。 */
  error: string | null
  /** 分诊面板展开着没有。见文件开头：状态在调用方。 */
  triageOpen: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update:triageOpen', v: boolean): void
  (e: 'triage', to: FeedbackStatus): void
}>()

const store = useFeedbackStore()
const { t } = useI18n()

/** 下一格。和 `AdminQueueRow` 的那张表同一份 —— 详情里那颗主按钮推的就是这一格。 */
const NEXT: Record<FeedbackStatus, FeedbackStatus | null> = {
  received: 'in_progress',
  in_progress: 'resolved',
  resolved: 'deployed',
  deployed: null,
}

/** 按钮文案按**目标**状态取（和队列行同一个理由：按钮说出口的是它要做的那件事）。
 *  键名逐字来自 wave 0 的 `feedback.queue.advance.*`，两个地方共用同一批字。 */
const ADVANCE_LABEL: Record<FeedbackStatus, string> = {
  in_progress: 'feedback.queue.advance.inProgress',
  resolved: 'feedback.queue.advance.resolved',
  deployed: 'feedback.queue.advance.deployed',
  received: '',
}

const next = computed(() => (props.item ? NEXT[props.item.status] : null))
const advanceLabel = computed(() => (next.value ? t(ADVANCE_LABEL[next.value]) : ''))

/** 状态梯子用**服务端**那份（meta 里的 `status_ladder`）：管理员看到的顺序就是这条
 *  反馈真会走的顺序，前端不另存一份。 */
const statusItems = computed(() => store.statusLadder.map((s) => ({ title: statusMeta(s).label, value: s })))
const priorityItems = computed(() =>
  Object.entries(PRIORITY_META).map(([value, meta]) => ({ title: meta.label, value }))
)

const status = computed(() => statusMeta(props.item?.status))
const priority = computed(() => (props.item?.priority ? PRIORITY_META[props.item.priority] : null))

/** 收起时那一行摘要要说的三件事。用 `·` 连接的空值一律不画，免得出现「已修复 · · 高」。 */
const summary = computed(() => {
  const item = props.item
  if (!item) return ''
  return [
    status.value.label,
    item.assignee_handle ?? t('feedback.queueDetail.unassigned'),
    priority.value ? t('feedback.queueDetail.priorityValue', { label: priority.value.label }) : '',
  ]
    .filter(Boolean)
    .join(' · ')
})

const noteDraft = ref('')
const assignWrap = ref<HTMLElement | null>(null)

/** `A` 键要落到的那个输入框。按类名进去找（组件之间的契约里没有「把 input 交出来」
 *  这一项，而 `.v-field__input` 是 Vuetify 自己的、不是我们两家的私约）。 */
function focusAssignee() {
  assignWrap.value?.querySelector<HTMLInputElement>('input')?.focus()
}

defineExpose({ focusAssignee })

async function addNote() {
  const body = noteDraft.value.trim()
  if (!body || !props.item) return
  // 先清草稿再发：失败时用户手上那份字会没掉，但服务端回的是一句原话（`store.error`
  // 画在这一页上），而留在框里的话，他会以为「加进去了」再点一次，于是同一条备注
  // 写两遍 —— 备注是只增不改的流水，重复的那一条删不掉。
  noteDraft.value = ''
  await store.addNote(props.item.id, body)
}

function onSecurity(v: unknown) {
  if (props.item) void store.setSecurity(props.item.id, !!v)
}
</script>

<template>
  <div class="qdet">
    <div class="qdet__panes">
      <!-- 左栏。窄屏时它在上面，宽屏时它是左边那 440px。 -->
      <aside class="qdet__side">
        <div class="qdet__side-head">
          <button type="button" class="qdet__back" @click="emit('close')">
            <v-icon icon="mdi-arrow-left" size="16" aria-hidden="true" />
            {{ t('feedback.queueDetail.back') }}
          </button>
        </div>

        <div v-if="item" class="qdet__ident">
          <StatusRail :status="item.status" />
          <div class="qdet__ident-text">
            <div class="qdet__ident-top">
              <span class="qdet__word" :style="{ color: status.ink }">{{ status.label }}</span>
              <span class="t-meta-read t-num">{{ item.display_id }}</span>
            </div>
            <h2 class="qdet__title">{{ item.title }}</h2>
            <div class="t-meta-read t-num">{{ t('feedback.queue.supports', { n: item.supports }) }}</div>
          </div>
        </div>

        <!-- 分诊面板（§8 的 Esc 第 1 层、§9 验收第 23 条）。收起时只留一行摘要：
             小屏上左栏这一块占的是**纵向**空间，展开着会把正文挤到第二屏去。 -->
        <section v-if="item" class="qdet__block">
          <div class="qdet__block-head">
            <span class="t-eyebrow-read">{{ t('feedback.queueDetail.triage') }}</span>
            <button v-if="!triageOpen" type="button" class="qdet__toggle" @click="emit('update:triageOpen', true)">
              {{ t('feedback.queueDetail.expand') }}
            </button>
            <button v-else type="button" class="qdet__toggle" @click="emit('update:triageOpen', false)">
              {{ t('feedback.queueDetail.collapse') }}
            </button>
          </div>

          <p v-if="!triageOpen" class="qdet__summary t-body">{{ summary }}</p>

          <div v-else class="qdet__fields">
            <div class="qdet__field">
              <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.status') }}</div>
              <!-- 梯子竖着排、每一格是按钮：`1/2/3` 三个键对应的就是这三行，横排的话
                   键位和位置对不上。当前那一格不画按钮（它就是现状），只标出来。 -->
              <div class="qdet__ladder" role="radiogroup" :aria-label="t('feedback.queueDetail.status')">
                <button
                  v-for="s in statusItems"
                  :key="s.value"
                  type="button"
                  role="radio"
                  class="qdet__rung"
                  :class="{ 'qdet__rung--on': item.status === s.value }"
                  :aria-checked="item.status === s.value"
                  @click="item.status !== s.value && emit('triage', s.value)"
                >
                  {{ s.title }}
                </button>
              </div>
            </div>

            <div class="qdet__field">
              <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.assign') }}</div>
              <div ref="assignWrap">
                <AdminAssigneeSelect
                  :model-value="item.assignee_handle"
                  :label="t('feedback.queueDetail.assignTo')"
                  dense
                  @update:model-value="(v: string | null) => store.assign(item!.id, v)"
                />
              </div>
            </div>

            <div class="qdet__field">
              <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.priority') }}</div>
              <v-select
                :model-value="item.priority"
                :items="priorityItems"
                autocomplete="off"
                density="compact"
                hide-details
                @update:model-value="(v: FeedbackPriority) => store.setPriority(item!.id, v)"
              />
            </div>
          </div>
        </section>

        <section v-if="item" class="qdet__block">
          <div class="qdet__facts">
            <div class="qdet__fact">
              <span class="t-meta-read">{{ t('feedback.queueDetail.author') }}</span>
              <span class="qdet__fact-value">
                <FeedbackAuthorAvatar
                  :handle="item.author_handle"
                  :is-agent="item.author_is_agent"
                  :avatar-id="item.author_avatar_id"
                  :size="20"
                />
                {{ item.author_handle }}
              </span>
            </div>
            <div class="qdet__fact">
              <span class="t-meta-read">{{ t('feedback.queueDetail.created') }}</span>
              <span class="qdet__fact-value">{{ relTime(item.created_at) }}</span>
            </div>
            <div class="qdet__fact">
              <span class="t-meta-read">{{ t('feedback.queueDetail.lastActivity') }}</span>
              <span class="qdet__fact-value">{{ relTime(item.last_activity_at ?? item.created_at) }}</span>
            </div>
            <div class="qdet__fact">
              <span class="t-meta-read">{{ t('feedback.queueDetail.source') }}</span>
              <span class="qdet__fact-value">{{ SOURCE_LABEL[item.author_is_agent ? 'agent' : 'user'] }}</span>
            </div>
            <div class="qdet__fact">
              <span class="t-meta-read">{{ t('feedback.queueDetail.kind') }}</span>
              <span class="qdet__fact-value">{{ KIND_LABEL[item.kind] }}</span>
            </div>
          </div>
        </section>

        <section v-if="item" class="qdet__block">
          <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.timeline') }}</div>
          <ol class="qdet__timeline">
            <li v-for="(entry, i) in item.timeline" :key="`${entry.at}-${i}`" class="qdet__tl-row">
              <span class="status-dot" :style="{ background: statusMeta(entry.status).dot }" aria-hidden="true" />
              <span class="qdet__tl-word">{{ statusMeta(entry.status).label }}</span>
              <span class="t-meta-read t-num">{{ relTime(entry.at) }}</span>
            </li>
          </ol>
        </section>
      </aside>

      <div class="qdet__main">
        <div class="qdet__main-head">
          <!-- 一行事实挤在一个 `<span>` 里（不是一堆 flex 项）：截断要生效，被截的必须
               是**一行文字**，而 flex 容器的文字会变成匿名 flex 项，`text-overflow`
               在它身上不生效。 -->
          <span v-if="item" class="qdet__main-who">
            {{ item.author_handle }} · {{ relTime(item.created_at) }} ·
            {{ t('feedback.queue.supports', { n: item.supports }) }} · {{ t('feedback.queueDetail.comments') }}
            {{ item.comments }} · {{ t('feedback.queueDetail.assign') }}
            {{ item.assignee_handle ?? t('feedback.queueDetail.unassigned') }}
          </span>
        </div>

        <div class="qdet__body">
          <template v-if="item">
            <section class="qdet__section">
              <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.problem') }}</div>
              <p class="t-body-readable qdet__text">{{ item.problem }}</p>
              <template v-if="item.why">
                <div class="t-eyebrow-read qdet__label qdet__label--sub">{{ t('feedback.queueDetail.why') }}</div>
                <p class="t-body-readable qdet__text">{{ item.why }}</p>
              </template>
              <template v-if="item.expectation">
                <div class="t-eyebrow-read qdet__label qdet__label--sub">
                  {{ t('feedback.queueDetail.expectation') }}
                </div>
                <p class="t-body-readable qdet__text">{{ item.expectation }}</p>
              </template>
            </section>

            <section v-if="item.what_happened || item.repro || item.evidence || item.logs" class="qdet__section">
              <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.context') }}</div>
              <template v-if="item.what_happened">
                <p class="t-body-readable qdet__text">{{ item.what_happened }}</p>
              </template>
              <template v-if="item.repro">
                <div class="t-meta-read qdet__label--sub">{{ t('feedback.queueDetail.repro') }}</div>
                <pre class="qdet__pre">{{ item.repro }}</pre>
              </template>
              <template v-if="item.evidence">
                <div class="t-meta-read qdet__label--sub">{{ t('feedback.queueDetail.evidence') }}</div>
                <p class="t-body-readable qdet__text">{{ item.evidence }}</p>
              </template>
              <template v-if="item.logs">
                <div class="t-meta-read qdet__label--sub">{{ t('feedback.queueDetail.logs') }}</div>
                <pre class="qdet__pre">{{ item.logs }}</pre>
              </template>
            </section>

            <section v-if="item.session_id || item.environment" class="qdet__section">
              <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.sessionInfo') }}</div>
              <div v-if="item.session_id" class="qdet__kv">
                <span class="t-meta-read">{{ t('feedback.queueDetail.session') }}</span>
                <code class="qdet__code">{{ item.session_id }}</code>
              </div>
              <div v-if="item.environment" class="qdet__kv">
                <span class="t-meta-read">{{ t('feedback.queueDetail.environment') }}</span>
                <span class="t-body-readable">{{ item.environment }}</span>
              </div>
            </section>

            <section class="qdet__section">
              <div class="t-eyebrow-read qdet__label">
                {{ t('feedback.queueDetail.discussion') }} {{ item.comments }}
              </div>
              <p v-if="!item.thread.length" class="t-meta-read">{{ t('feedback.queueDetail.noReplies') }}</p>
              <div v-for="comment in item.thread" :key="comment.id" class="qdet__comment">
                <div class="t-meta-read qdet__comment-head">
                  <FeedbackAuthorAvatar
                    :handle="comment.author_handle"
                    :is-agent="comment.author_is_agent"
                    :avatar-id="comment.author_avatar_id"
                    :size="20"
                  />
                  <span>{{ comment.author_handle }} · {{ relTime(comment.created_at) }}</span>
                </div>
                <p class="t-body-readable qdet__text">{{ comment.body }}</p>
              </div>
            </section>

            <!-- 安全问题这一格单独放，且写成一句话说明后果：勾上之后这条对**同事**就
                 不见了（服务端把它收窄到提交者 + 管理员），不是加了个标签。 -->
            <section class="qdet__section">
              <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.security') }}</div>
              <p class="t-meta-read qdet__label--sub">
                {{ t('feedback.queueDetail.securityHint') }}
              </p>
              <v-switch
                :model-value="item.security"
                color="primary"
                density="compact"
                hide-details
                :label="t('feedback.queueDetail.securityMark')"
                @update:model-value="onSecurity"
              />
            </section>

            <section class="qdet__section">
              <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.notes') }}</div>
              <p class="t-meta-read qdet__label--sub">{{ t('feedback.queueDetail.notesHint') }}</p>

              <div v-for="note in item.notes" :key="note.id" class="qdet__note">
                <!-- 备注恒为真人写的，所以这里不传 `is-agent`：管理端的五条写路由都先过
                     `_require_admin`，而它第一步就是拒绝 agent 身份。 -->
                <div class="t-meta-read qdet__comment-head">
                  <FeedbackAuthorAvatar :handle="note.author_handle" :avatar-id="note.author_avatar_id" :size="20" />
                  <span>{{ note.author_handle }} · {{ relTime(note.created_at) }}</span>
                </div>
                <p class="t-body-readable qdet__text">{{ note.body }}</p>
              </div>
              <p v-if="!item.notes.length" class="t-meta-read">{{ t('feedback.queueDetail.noNotes') }}</p>

              <v-textarea
                v-model="noteDraft"
                class="qdet__note-input"
                autocomplete="off"
                density="compact"
                rows="3"
                hide-details
                :placeholder="t('feedback.queueDetail.notePlaceholder')"
              />
            </section>

            <v-alert v-if="store.error" type="error" density="compact" variant="tonal" class="qdet__alert">
              {{ store.error }}
            </v-alert>
          </template>

          <template v-else-if="loading">
            <!-- 骨架：左栏 6 行、右栏 4 段（§9.5 的加载态）。窄屏时左栏在上面，
                 所以下面这几条骨头的顺序对两种断点都成立。 -->
            <div v-for="i in 4" :key="`skel-${i}`" class="qdet__skel">
              <span class="qdet__bone qdet__bone--head" />
              <span class="qdet__bone qdet__bone--line" />
              <span class="qdet__bone qdet__bone--line" />
            </div>
          </template>

          <div v-else class="qdet__none">
            <!-- 读不到和「没有这条」是两件事：前者是网络，后者是这条已经不在你能看的
                 范围里（被删、或者它不是你的）。文案取自 `feedback.detail.*`。 -->
            <p class="qdet__none-title">
              {{ error ? t('feedback.detail.error.title') : t('feedback.detail.missing.title') }}
            </p>
            <p class="qdet__none-desc">
              {{ error ? t('feedback.detail.error.desc') : t('feedback.detail.missing.desc') }}
            </p>
            <!-- 服务端给的原话照旧画出来（全仓的规矩：失败的原话直接显示，不另写一句
                 「操作失败」）。上面那两句是给不知道往哪看的人的地图，这一行是事实。 -->
            <p v-if="error" class="qdet__none-raw t-meta-read">{{ error }}</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 64px 操作栏（§4.4）。**这一屏唯一的琥珀**是左边那颗主按钮（§7.4）；
         右边那颗「提交备注」是次要动作，中性描边。底部内边距吃掉
         `env(safe-area-inset-bottom)`：窄屏上操作栏会压在 iOS 那条横条下面。 -->
    <div class="qdet__bar">
      <button v-if="next" type="button" class="qdet__primary" @click="emit('triage', next)">
        {{ advanceLabel }}
      </button>
      <span class="qdet__bar-spacer" />
      <button type="button" class="qdet__submit" :disabled="!noteDraft.trim()" @click="addNote">
        {{ t('feedback.queueDetail.submitNote') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.qdet {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--canvas);
}

.qdet__panes {
  display: grid;
  flex: 1 1 auto;
  grid-template-rows: minmax(0, 1fr);
  /* 一栏。宽屏那一档在下面那条媒体查询里改成 440 + 1fr。 */
  grid-template-columns: minmax(0, 1fr);
  min-height: 0;
}

.qdet__side,
.qdet__main {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
  background: var(--surface);
}

.qdet__side {
  border-right: 1px solid var(--line);
}

.qdet__side-head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  box-sizing: border-box;
  height: 56px;
  padding: 0 16px;
  border-bottom: 1px solid var(--line-2);
}

/* 返回是一个次级动作：中性文字 + 图标，`--fill` 底只在 hover 时出现。 */
.qdet__back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qdet__back:hover {
  color: var(--text);
  background: var(--fill);
}

.qdet__ident {
  display: flex;
  flex: 0 0 auto;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--line);
}

.qdet__ident-text {
  min-width: 0;
}

.qdet__ident-top {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.qdet__word {
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
}

/* 标题 18 / --lh-18 26（§4.4 左栏）。 */
.qdet__title {
  margin: 4px 0;
  color: var(--ink);
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
}

.qdet__block {
  flex: 0 0 auto;
  padding: 16px;
  border-bottom: 1px solid var(--line);
}

.qdet__block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.qdet__toggle {
  height: 24px;
  padding: 0 8px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  cursor: pointer;
  background: transparent;
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  transition: background-color 0.12s ease;
}

.qdet__toggle:hover {
  background: var(--fill);
}

.qdet__summary {
  margin: 8px 0 0;
  color: var(--muted);
}

.qdet__fields {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
}

.qdet__label {
  margin-bottom: 4px;
}

.qdet__label--sub {
  margin-top: 12px;
}

.qdet__ladder {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* 梯子那一格 28px 高：和 `AdminNumberList` 的行同高，两块竖排的列表挨在一起时
   行距不会一紧一松。 */
.qdet__rung {
  display: block;
  width: 100%;
  height: 28px;
  padding: 0 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.qdet__rung:hover {
  color: var(--text);
  background: var(--fill);
}

/* 当前那一格：`--fill-2` 底 + `--ink` 字。**不是琥珀** —— 详情这一屏的琥珀留给
   操作栏那颗主按钮（§7.4 的详情 = 1）。 */
.qdet__rung--on,
.qdet__rung--on:hover {
  color: var(--ink);
  font-weight: 600;
  background: var(--fill-2);
}

.qdet__facts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.qdet__fact {
  display: flex;
  align-items: center;
  gap: 12px;
}

.qdet__fact > .t-meta-read {
  flex: 0 0 64px;
}

.qdet__fact-value {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.qdet__timeline {
  margin: 0;
  padding: 0;
  list-style: none;
}

.qdet__tl-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
}

.qdet__tl-word {
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.qdet__main-head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  box-sizing: border-box;
  /* 56px：和左栏那一行的 `返回` 同高，两栏顶上那条分隔线才对得齐。 */
  height: 56px;
  min-width: 0;
  padding: 0 16px;
  border-bottom: 1px solid var(--line-2);
}

.qdet__main-who {
  /* 窄屏时右栏那一行整块对齐到一个左边界，读起来是一句而不是一列格子。 */
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
  text-overflow: ellipsis;
}

.qdet__body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 16px;
}

.qdet__section + .qdet__section {
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid var(--line);
}

/* 用户写的正文是**多段**的（换行要保留）。 */
.qdet__text {
  margin-bottom: 0;
  white-space: pre-wrap;
}

.qdet__pre {
  margin: 0;
  padding: 8px 12px;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--fill);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

.qdet__kv {
  display: flex;
  gap: 12px;
  margin-bottom: 4px;
}

.qdet__code {
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12px;
}

.qdet__comment + .qdet__comment,
.qdet__note + .qdet__note {
  margin-top: 12px;
}

.qdet__comment-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.qdet__comment {
  padding: 8px 12px;
  background: var(--fill);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

.qdet__note-input {
  margin-top: 12px;
}

.qdet__skel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}

.qdet__bone {
  display: block;
  height: 12px;
  background: var(--fill-2);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

.qdet__bone--head {
  width: 30%;
}

.qdet__bone--line {
  width: 100%;
}

/* 空态 / 错误态：宽 320px，主副两行、间距 8px（§9.2 那一套）。 */
.qdet__none {
  width: 320px;
  margin: 96px auto 0;
}

.qdet__none-title {
  margin: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.qdet__none-desc {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.qdet__none-raw {
  margin: 8px 0 0;
  word-break: break-word;
}

.qdet__bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  box-sizing: border-box;
  height: 64px;
  padding: 0 16px;
  /* iOS 那条横条压在操作栏上时，按钮点不到。安全区只在有它的时候才加，
     没有这个变量时 `0px` 是空操作。 */
  padding-bottom: env(safe-area-inset-bottom, 0);
  background: var(--surface);
  border-top: 1px solid var(--line);
}

/* 主操作。全屏唯一的琥珀（§7.4）。字色走 `--v-theme-on-primary`，和全站每一颗
   琥珀填充按钮同一处取值（见 `MyDevicesView` 那段豁免说明：浅色下它对 #F57F17
   推出来的就是白字，2.65:1，是 2026-08-16 拍板保留的已知代价，别在这里「修好」）。 */
.qdet__primary {
  height: 40px;
  padding: 0 16px;
  color: rgb(var(--v-theme-on-primary));
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  cursor: pointer;
  background: var(--accent);
  border: 0;
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  transition: background-color 0.12s ease;
}

.qdet__primary:hover {
  background: var(--accent-press);
}

.qdet__bar-spacer {
  flex: 1 1 auto;
}

.qdet__submit {
  height: 40px;
  padding: 0 16px;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  transition: background-color 0.12s ease;
}

.qdet__submit:hover:not(:disabled) {
  background: var(--fill);
}

.qdet__submit:disabled {
  color: var(--muted);
  cursor: default;
}

.qdet__alert {
  margin-top: 24px;
}

/* 宽屏：§4.4 的左 440 + 右 `minmax(0,1fr)`。断点是 1280 —— 下面这条和
   `AdminQueuePage` 里那条决策（宽屏用这一页、窄屏用抽屉）必须是同一个数，改一处
   就要改两处。 */
@media (min-width: 1280px) {
  .qdet__panes {
    grid-template-columns: 440px minmax(0, 1fr);
  }
}
</style>
