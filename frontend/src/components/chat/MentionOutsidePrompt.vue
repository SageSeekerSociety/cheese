<script setup lang="ts">
// 我刚发的那条 @ 了不在这个频道里的人：贴在输入框上方问一句要不要加进来，只有我看得见
// （Slack 的 "X isn't in this channel"）。文档里也一样：我在一份私密频道的文档里 @ 了
// 频道外的人，他打不开这份文档，就在文档上方问这一句。私密频道里他们看不到这条消息，所以要说出来；
// 公开频道里他们照样收到通知、读得到，只是没加入。我加不了人时不给那颗按钮，说清楚为什么。
import BaseButton from '../base/BaseButton.vue'

import { t } from '@/i18n'

defineProps<{
  /** 不在频道里的那几位，已经连成一串名字。 */
  names: string
  /** 私密频道：不在里面的人看不到这条消息。 */
  membersOnly: boolean
  /** 我能不能把他们加进来。 */
  mayAdd: boolean
  adding: boolean
  /** 说的是一份文档里的 @，不是一条消息：私密频道外的人打不开的是那份文档。 */
  inDocument?: boolean
}>()

const emit = defineEmits<{
  (e: 'add'): void
  (e: 'dismiss'): void
}>()
</script>

<template>
  <div class="mention-outside" role="status">
    <div class="mention-outside__text t-body">
      <span>{{
        membersOnly && inDocument
          ? t('work.room.mentionOutside.notInPrivateDoc', { names })
          : membersOnly
            ? t('work.room.mentionOutside.notInPrivate', { names })
            : t('work.room.mentionOutside.notInPublic', { names })
      }}</span>
      <span v-if="!mayAdd" class="mention-outside__why">{{ t('work.room.roster.readOnly') }}</span>
    </div>
    <div class="mention-outside__actions">
      <template v-if="mayAdd">
        <BaseButton kind="ghost" size="sm" :disabled="adding" @click="emit('dismiss')">{{
          t('work.room.mentionOutside.dismiss')
        }}</BaseButton>
        <BaseButton kind="secondary" size="sm" :loading="adding" @click="emit('add')">{{
          t('work.room.mentionOutside.add')
        }}</BaseButton>
      </template>
      <BaseButton v-else kind="ghost" size="sm" @click="emit('dismiss')">{{
        t('work.room.mentionOutside.close')
      }}</BaseButton>
    </div>
  </div>
</template>

<style scoped>
.mention-outside {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 16px 8px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--fill);
}
.mention-outside__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  color: var(--text);
}
.mention-outside__why {
  color: var(--muted);
}
.mention-outside__actions {
  display: flex;
  gap: 4px;
  margin-left: auto;
}
</style>
