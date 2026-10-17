<script setup lang="ts">
import type { FeedbackDetail } from '@/cx_types'

import { useI18n } from 'vue-i18n'

import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import { relTime } from '@/lib/relTime'

/**
 * AdminQueueDetailBody.vue — 详情右栏：顶上那一行事实，和正文那几段（问题、上下文、
 * 会话信息、讨论、安全问题、备注），外加加载 / 空 / 读不到三档。
 *
 * 从 `AdminQueueDetail.vue` 拆出来的（那一件原来 949 行，顶到 `frontend/src` 的行数
 * 上限）。切法是「哪一栏」：右栏成件，父组件只剩两栏的版式和底下那根操作栏 —— 这一栏
 * 在宽屏是右边那份、窄屏是下面那段，那是父组件的一条媒体查询，所以 `.qdet__main` 自己
 * 的盒子留在父组件的 `<style>` 里。
 *
 * **只吃 props，写操作全部报出去**：安全问题报 `security`。备注那个框是 `v-model` 的
 * 一半 —— 草稿和底下那颗「提交备注」都在**父组件**手里（按钮在操作栏里，两边看的是同一
 * 份字），这里只管画和改。
 */
defineOptions({ name: 'AdminQueueDetailBody' })

const props = withDefaults(
  defineProps<{
    item: FeedbackDetail | null
    /** 正在拉这一条。拿不到内容之前先摆骨架（四条骨头、各三行），不是一块空白：
     *  这一页从列表点进来几乎必然经过这一档（§9.5 的加载态）。窄屏时左栏在上面，所以
     *  下面那几条骨头的顺序对两种断点都成立。 */
    loading: boolean
    /** 拉取失败时的原话。为空表示这一趟没出错。
     *
     *  「读不到」和「没有这条」是两件事：前者是网络，后者是这条已经不在你能看的范围里
     *  （被删、或者它不是你的），文案各取 `feedback.detail.error.*` 和
     *  `feedback.detail.missing.*`。 */
    error: string | null
    /** 写操作失败的原话，由调用方的 store 给。它画在这里是因为「刚才那一下没成」要贴着
     *  刚点过的控件说，隔着一屏没人看得见。 */
    writeError?: string
  }>(),
  { writeError: '' }
)

const emit = defineEmits<{
  (e: 'security', id: string, security: boolean): void
  (e: 'update:noteDraft', v: string): void
}>()

/** 备注草稿。上面那段说过：字在父组件手里，这里只是它的一半。 */
const noteDraft = defineModel<string>('noteDraft', { required: true })

const { t } = useI18n()

/** 安全问题那一格勾上之后，这条对**同事**就不见了（服务端把它收窄到提交者 + 管理员），
 *  不是加了个标签而已 —— 所以那一格单独放、配一句后果说明。 */
function onSecurity(v: unknown) {
  if (props.item) emit('security', props.item.id, !!v)
}

/* 模板里那几处讲究，以前是模板里的 HTML 注释，现在写在这儿：新文件的中文 HTML 注释过不了
   `lint:i18n`（那道闸门只放开存量，理由是注释会留在 vitest 的 DOM 里，被「渲染结果里不该
   有中文」的那几条用例读到），而中文的解释又不该翻成英文 —— 所以归到 `<script>` 块里。
 *
 *   · 服务端给的原话**照旧画出来**（全仓的规矩：失败的原话直接显示，不另写一句「操作
 *     失败」）。空态上面那两句是给不知道往哪看的人的地图，最后那一行才是事实。
 *   · 备注恒为真人写的，所以那几行不传 `is-agent`：管理端的五条写路由都先过
 *     `_require_admin`，而它第一步就是拒绝 agent 身份。 */
</script>

<template>
  <div class="qdet__main">
    <div class="qdet__main-head">
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
          <div class="t-eyebrow-read qdet__label">{{ t('feedback.queueDetail.discussion') }} {{ item.comments }}</div>
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

        <v-alert v-if="writeError" type="error" density="compact" variant="tonal" class="qdet__alert">
          {{ writeError }}
        </v-alert>
      </template>

      <template v-else-if="loading">
        <div v-for="i in 4" :key="`skel-${i}`" class="qdet__skel">
          <span class="qdet__bone qdet__bone--head" />
          <span class="qdet__bone qdet__bone--line" />
          <span class="qdet__bone qdet__bone--line" />
        </div>
      </template>

      <div v-else class="qdet__none">
        <p class="qdet__none-title">
          {{ error ? t('feedback.detail.error.title') : t('feedback.detail.missing.title') }}
        </p>
        <p class="qdet__none-desc">
          {{ error ? t('feedback.detail.error.desc') : t('feedback.detail.missing.desc') }}
        </p>
        <p v-if="error" class="qdet__none-raw t-meta-read">{{ error }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 这一件的根节点 `.qdet__main` 自己**不在这里画**：它在宽屏是右边那一栏、窄屏是下面
   那一段，连同 1280 那个断点一起留在父组件的 `<style>` 里（改一处就要和
   `AdminQueuePage` 那条「宽屏整页 / 窄屏抽屉」的决策一起改）。这里只管栏里面。 */
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
  /* 一行事实挤在一个节点里（不是一堆 flex 项）：截断要生效，被截的必须**是一行文字**，
     而 flex 容器的文字会变成匿名 flex 项，`text-overflow` 在它身上不生效。窄屏时右栏
     这一行整块对齐到一个左边界，读起来是一句而不是一列格子。 */
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

/* 栏位标签那一行：这一件和左边那一件各写一份（同一件里的东西不该由别人替它画），4px 的
   间距两边必须一样。 */
.qdet__label {
  margin-bottom: 4px;
}

.qdet__label--sub {
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

.qdet__alert {
  margin-top: 24px;
}
</style>
