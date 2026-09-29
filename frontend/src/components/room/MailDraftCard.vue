<script setup lang="ts">
// 芝士写好的一封邮件，在它被写出来的房间里等邮箱主人确认。
//
// 过去确认要去「我的连接 → 待发送」，一个人读到「芝士写好了草稿」时并不在那一页
// （chiruotong 2026-09-27）。卡片上摆出发出去的全部内容：收件人、抄送、主题、正文、
// 附件名。只有邮箱主人看得到按钮；发送仍由后端核对主人身份和附件有没有被改过，
// 这里的按钮只是入口，不是闸门。
import type { MailDraftView, MailOutcome } from '../../lib/platformNotice'

import { computed, ref } from 'vue'

import { discardMailDraft, sendMailDraft } from '../../api'
import { myHandle } from '../../me'

const props = defineProps<{
  mail: MailDraftView
  /** 房间里已经记下的下落；没有就是还在等。 */
  outcome: MailOutcome | null
  /** 正在看这个房间的人；不给就是当前登录的账号。 */
  me?: string | null
}>()

const busy = ref<'' | 'send' | 'discard'>('')
const error = ref('')
/** 刚在这里点完、房间事件还没回来的那一刻，先按自己的结果画。 */
const local = ref<MailOutcome | null>(null)
const ended = computed(() => local.value ?? props.outcome)
const mine = computed(() => {
  const me = props.me ?? myHandle()
  return !!me && me === props.mail.owner
})

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : ''
}

async function send() {
  busy.value = 'send'
  error.value = ''
  try {
    const result = await sendMailDraft(props.mail.draftId)
    local.value = { status: 'sent', sentAt: result.draft.sent_at ?? null, reason: null }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '没有发出去'
  } finally {
    busy.value = ''
  }
}

async function discard() {
  busy.value = 'discard'
  error.value = ''
  try {
    await discardMailDraft(props.mail.draftId)
    local.value = { status: 'discarded', sentAt: null, reason: null }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '没有放弃成功'
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <div class="mail-card" data-testid="mail-draft-card">
    <div class="mail-card__head">
      <span class="mail-card__title">{{ mail.subject || '（无主题）' }}</span>
      <span class="mail-card__from">从 {{ mail.account }}</span>
    </div>
    <dl class="mail-card__fields">
      <dt>收件人</dt>
      <dd>{{ mail.to.join('、') }}</dd>
      <template v-if="mail.cc.length">
        <dt>抄送</dt>
        <dd>{{ mail.cc.join('、') }}</dd>
      </template>
      <template v-if="mail.attachments.length">
        <dt>附件</dt>
        <dd>{{ mail.attachments.map((a) => a.name).join('、') }}</dd>
      </template>
    </dl>
    <details class="mail-card__body">
      <summary>正文</summary>
      <pre>{{ mail.body }}</pre>
    </details>

    <div v-if="ended" class="mail-card__state" :class="`mail-card__state--${ended.status}`" data-testid="mail-state">
      <template v-if="ended.status === 'sent'"
        >已发送<template v-if="ended.sentAt"> · {{ when(ended.sentAt) }}</template></template
      >
      <template v-else-if="ended.status === 'discarded'">已放弃</template>
      <template v-else
        >没有发出去<template v-if="ended.reason">：{{ ended.reason }}</template></template
      >
    </div>
    <div v-else-if="mine" class="mail-card__actions">
      <v-btn size="small" color="primary" variant="flat" :loading="busy === 'send'" :disabled="!!busy" @click="send">
        确认发送
      </v-btn>
      <v-btn size="small" variant="text" :loading="busy === 'discard'" :disabled="!!busy" @click="discard">
        放弃
      </v-btn>
      <span class="mail-card__hint">发出去的就是上面这封；芝士不能替你发。</span>
    </div>
    <div v-else class="mail-card__state" data-testid="mail-waiting">等 {{ mail.owner }} 确认后才会发送</div>
    <div v-if="error" class="mail-card__error" role="alert">{{ error }}</div>
  </div>
</template>

<style scoped>
.mail-card {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
  padding: 10px 12px;
  margin-top: 4px;
  max-width: 560px;
}
.mail-card__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
}
.mail-card__title {
  font-weight: 600;
}
.mail-card__from,
.mail-card__hint {
  font-size: 12px;
  opacity: 0.7;
}
.mail-card__fields {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px 10px;
  margin: 6px 0;
  font-size: 13px;
}
.mail-card__fields dt {
  opacity: 0.7;
}
.mail-card__body pre {
  white-space: pre-wrap;
  font: inherit;
  font-size: 13px;
  margin: 4px 0 0;
}
.mail-card__actions {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 8px;
}
.mail-card__state {
  margin-top: 8px;
  font-size: 13px;
  opacity: 0.8;
}
.mail-card__state--failed,
.mail-card__error {
  color: rgb(var(--v-theme-error));
}
.mail-card__error {
  margin-top: 6px;
  font-size: 13px;
}
</style>
