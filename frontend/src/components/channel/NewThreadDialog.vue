<script setup lang="ts">
// 在频道下新建一条支线（清单第 4 条）：填一个标题就开出来，不必先在主线上找一条
// 消息去「在支线中回复」。从支线页顶上那一颗和空态里那一颗打开，两处同一个对话框。
//
// 和 NewChannelDialog 同一个分工：这一份只管那张表和「能不能点」，开出来之后去哪由
// 打开它的页面决定。
import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'

const props = defineProps<{
  busy: boolean
  /** 这个频道叫什么，写在标题里（「在「产品设计」下新建支线」）。 */
  channel: string
}>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ (e: 'create', title: string): void }>()

const title = ref('')

watch(open, (now) => {
  if (now) title.value = ''
})

const ready = computed(() => title.value.trim().length > 0)

function submit() {
  if (!ready.value || props.busy) return
  emit('create', title.value.trim())
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    size="sm"
    :title="t('work.room.thread.newIn', { channel })"
    :primary-label="t('work.room.thread.new')"
    :primary-loading="busy"
    :primary-disabled="!ready"
    :close-disabled="busy"
    @primary="submit"
  >
    <form class="new-thread" data-testid="new-thread" @submit.prevent="submit">
      <!-- 和 NewChannelDialog 同例：这个弹窗就是为打一个标题而开，打开它本身就是用户的
           动作，焦点直接落在那一格上。 -->
      <!-- eslint-disable vuejs-accessibility/no-autofocus -- 见上一句 -->
      <v-text-field
        v-model="title"
        :label="t('work.room.thread.titleLabel')"
        :placeholder="t('work.room.thread.titlePlaceholder')"
        :maxlength="TOPIC_TITLE_MAX_LENGTH"
        variant="outlined"
        density="comfortable"
        hide-details
        autocomplete="off"
        autofocus
      />
      <!-- eslint-enable vuejs-accessibility/no-autofocus -->
    </form>
  </AdaptiveDialog>
</template>

<style scoped>
.new-thread {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
