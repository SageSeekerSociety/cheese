<script setup lang="ts">
// 「分享…」（#3774）：给一份文档一条只读链接。拿到链接的人不用登录就能读当前版本，看不到
// 评论、修改建议和修改记录；停止分享后链接立即失效，再分享是一条新链接。
// 只吃 props、只往上发：链接的取、建、停都是页面的事。
import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

defineProps<{
  title: string
  /** 还在问这份文档有没有链接。 */
  loading: boolean
  /** 自己能不能创建、停止这条链接。 */
  mayShare: boolean
  /** 分享页的完整地址；没有分享时是 null。 */
  url: string | null
  /** 正在创建或停止。 */
  busy: boolean
  error: string
}>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ share: []; unshare: []; copy: [] }>()
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.room.doc.shareDialog.title', { name: title })"
    :primary-label="
      loading || !mayShare
        ? undefined
        : url
          ? t('work.room.doc.shareDialog.copy')
          : t('work.room.doc.shareDialog.create')
    "
    :primary-loading="busy && !url"
    :cancel-label="t('work.room.doc.shareDialog.close')"
    :max-width="480"
    @primary="url ? emit('copy') : emit('share')"
  >
    <div class="doc-share">
      <div v-if="loading" class="d-flex justify-center py-6" role="status">
        <v-progress-circular indeterminate size="24" color="primary" />
      </div>
      <p v-else-if="!mayShare" class="t-body c-muted">{{ t('work.room.doc.shareDialog.notAllowed') }}</p>
      <template v-else-if="url">
        <v-text-field
          :model-value="url"
          :label="t('work.room.doc.shareDialog.link')"
          variant="outlined"
          density="compact"
          readonly
          autocomplete="off"
          hide-details
          data-testid="doc-share-url"
          @focus="($event.target as HTMLInputElement).select()"
        />
        <p class="t-body c-muted">{{ t('work.room.doc.shareDialog.shared') }}</p>
      </template>
      <p v-else class="t-body c-muted">{{ t('work.room.doc.shareDialog.about') }}</p>
      <v-alert v-if="error" type="error" variant="tonal" density="comfortable">{{ error }}</v-alert>
    </div>
    <template v-if="url && mayShare" #actions>
      <BaseButton kind="danger" :loading="busy" @click="emit('unshare')">
        {{ t('work.room.doc.shareDialog.stop') }}
      </BaseButton>
    </template>
  </AdaptiveDialog>
</template>

<style scoped>
.doc-share {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 8px;
}
</style>
