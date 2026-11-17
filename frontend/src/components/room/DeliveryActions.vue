<script setup lang="ts">
// 主线上一件任务交了一版、在等人审阅时，关于它的最新那一条下面的一排按钮：谁都能去看改动
// （有代码改动时）和预览（有东西可预览时）；这一版的审阅人还能就地采纳、退回。挂在队友说这件
// 任务的最新那条话下面；队友交了之后没说话，就挂在「已提交审阅」那一行下面——交上来的东西
// 总有地方处理。按下去做什么由页面定：这里只说按了哪一颗。
import type { DeliveryView, TaskLine } from '@/lib/channelTasks'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  task: TaskLine
  /** 看的人：是这一版的审阅人才给采纳和退回。 */
  viewer: string
  /** 「采纳」按下去了，还在等结果。 */
  adopting?: boolean
}>()

const emit = defineEmits<{ (e: 'delivery', act: { taskId: string; view: DeliveryView }): void }>()

const delivery = computed(() => props.task.delivery ?? null)
const decides = computed(() => !!delivery.value?.reviewers.includes(props.viewer))

function act(view: DeliveryView) {
  emit('delivery', { taskId: props.task.id, view })
}
</script>

<template>
  <div
    v-if="delivery && (delivery.code || delivery.preview || decides)"
    class="delivery-acts"
    role="group"
    :aria-label="t('work.room.message.delivery.label', { title: task.title })"
  >
    <BaseButton v-if="delivery.code" kind="secondary" size="sm" @click="act('changes')">
      {{ t('work.room.message.delivery.changes') }}
    </BaseButton>
    <BaseButton v-if="delivery.preview" kind="secondary" size="sm" @click="act('preview')">
      {{ t('work.room.message.delivery.preview') }}
    </BaseButton>
    <template v-if="decides">
      <BaseButton kind="secondary" size="sm" :disabled="adopting" @click="act('return')">
        {{ t('work.room.message.delivery.return') }}
      </BaseButton>
      <BaseButton kind="primary" size="sm" :loading="adopting" :disabled="adopting" @click="act('adopt')">
        {{ t('work.room.message.delivery.adopt') }}
      </BaseButton>
    </template>
  </div>
</template>

<style scoped>
.delivery-acts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
</style>
