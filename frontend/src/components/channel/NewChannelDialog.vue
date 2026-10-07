<script setup lang="ts">
// 新建频道：名称、说明（选填）、要不要私密。从「浏览频道」打开；建好之后去哪由打开
// 它的页面决定。
import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'

const DESCRIPTION_MAX_LENGTH = 500

const props = defineProps<{ busy: boolean }>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{
  (e: 'create', channel: { title: string; description: string; membersOnly: boolean }): void
}>()

const title = ref('')
const description = ref('')
const membersOnly = ref(false)

watch(open, (now) => {
  if (!now) return
  title.value = ''
  description.value = ''
  membersOnly.value = false
})

const ready = computed(() => title.value.trim().length > 0)

function submit() {
  if (!ready.value || props.busy) return
  emit('create', { title: title.value.trim(), description: description.value.trim(), membersOnly: membersOnly.value })
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    size="sm"
    :title="t('work.projectSettings.channels.create')"
    :primary-label="t('work.projectSettings.channels.create')"
    :primary-loading="busy"
    :primary-disabled="!ready"
    :close-disabled="busy"
    @primary="submit"
  >
    <form class="new-channel" data-testid="new-channel" @submit.prevent="submit">
      <v-text-field
        v-model="title"
        :label="t('work.projectSettings.channels.nameLabel')"
        :maxlength="TOPIC_TITLE_MAX_LENGTH"
        prefix="#"
        variant="outlined"
        density="comfortable"
        hide-details
        autocomplete="off"
        autofocus
      />
      <v-textarea
        v-model="description"
        :label="t('work.projectSettings.channels.descriptionLabel')"
        :maxlength="DESCRIPTION_MAX_LENGTH"
        variant="outlined"
        density="comfortable"
        rows="2"
        auto-grow
        hide-details
        autocomplete="off"
      />
      <v-checkbox
        v-model="membersOnly"
        data-testid="new-channel-private"
        :label="t('work.projectSettings.channels.privateLabel')"
        :hint="t('work.projectSettings.channels.privateHint')"
        persistent-hint
        density="compact"
      />
    </form>
  </AdaptiveDialog>
</template>

<style scoped>
.new-channel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
