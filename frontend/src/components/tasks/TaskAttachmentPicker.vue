<template>
  <v-card flat rounded="lg" class="form-card">
    <v-card-item>
      <template #prepend>
        <v-avatar color="primary-lighten-5" size="44" class="elevation-0">
          <v-icon color="primary" size="24">mdi-paperclip</v-icon>
        </v-avatar>
      </template>
      <v-card-title class="text-h6 ps-0">{{ t('tasks.attachmentPicker.title') }}</v-card-title>
      <v-card-subtitle class="ps-0">{{ t('tasks.attachmentPicker.subtitle') }}</v-card-subtitle>
    </v-card-item>

    <v-card-text class="pt-2">
      <v-file-input
        :model-value="picked"
        multiple
        :label="t('tasks.attachmentPicker.label')"
        variant="outlined"
        density="comfortable"
        clearable
        prepend-icon=""
        hide-details="auto"
        :disabled="uploading"
        @update:model-value="onPicked"
      >
        <template #prepend>
          <v-icon color="primary" class="mr-2">mdi-upload</v-icon>
        </template>
      </v-file-input>

      <!-- 上限是**接口报的**那个数（`GET /attachments/limits`），不是这一页写死的：
           传超了会被后端按同一个数拒掉，所以这句话与真正发生的事不会走散。 -->
      <p v-if="maxFileBytes" class="text-caption text-medium-emphasis mt-2" data-testid="attachment-limit">
        {{ t('tasks.attachmentPicker.limit', { size: formatFileSize(maxFileBytes) }) }}
      </p>

      <v-progress-linear v-if="uploading" indeterminate color="primary" class="mt-3" />

      <v-list v-if="uploaded.length > 0" density="compact" class="mt-3 bg-surface-light rounded-lg">
        <v-list-item v-for="file in uploaded" :key="file.id" data-testid="attached-file">
          <template #prepend>
            <v-icon size="small" class="mr-2">mdi-file-outline</v-icon>
          </template>
          <v-list-item-title class="text-body-2">{{ file.name }}</v-list-item-title>
          <v-list-item-subtitle class="text-caption">{{ formatFileSize(file.size) }}</v-list-item-subtitle>
          <template #append>
            <BaseButton
              icon="mdi-close"
              size="sm"
              :disabled="uploading"
              :aria-label="t('tasks.attachmentPicker.remove', { name: file.name })"
              @click="drop(file.id)"
            />
          </template>
        </v-list-item>
      </v-list>
    </v-card-text>
  </v-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { formatFileSize } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import { AttachmentsApi } from '@/network/api/attachments'

const { t } = useI18n()

/**
 * 发题时带的材料。
 *
 * **选中即上传，而不是等提交时再传**：文件先要在服务端有个 id，建题那条请求才能
 * 把 `attachmentIds` 一起带上（见后端 `TaskAttachmentService.attach_uploaded` 里
 * 对这条顺序的说明）。选中即传的代价是用户中途放弃会留下一个没人引用的对象 ——
 * 素材库、PDF 导入抽图今天是同一个处境；换来的是大文件的上传早于提交发生，用户在
 * 表单上就能看见它失败了，而不是点了发布才发现。
 *
 * 正在上传时 `update:uploading` 会举起来，交给发题页挡一下提交：宁可让人等一秒，
 * 也不能把「已经选好的材料」静默丢掉。
 */
// 名单由这个组件自己拿着，父组件只收结果：每加一份、每撤一份都整份报上去，
// 发题请求带的就是最后一次报出去的那串 id。
const emit = defineEmits<{
  (e: 'update:attachmentIds', value: number[]): void
  (e: 'update:uploading', value: boolean): void
}>()

type UploadedFile = { id: number; name: string; size: number }

const picked = ref<File[]>([])
const uploaded = ref<UploadedFile[]>([])
const uploading = ref(false)

/** 单份文件的上限，`null` = 还没问到（问不到就不写这句话，不猜一个数出来）。 */
const maxFileBytes = ref<number | null>(null)

// 这句话是**建议**，不是闸门：拿不到就少说一句，选择与上传照旧。读数失败不弹错 ——
// 用户到这一步并没有要求做任何事，凭空一条报错只会让人以为出了问题。
onMounted(async () => {
  try {
    const { data } = await AttachmentsApi.limits()
    maxFileBytes.value = data.maxFileBytes
  } catch {
    maxFileBytes.value = null
  }
})

/** 这份材料已经在服务端了，从这道题上撤下来（建题之前只是不再带上它）。 */
const drop = (id: number) => {
  uploaded.value = uploaded.value.filter((file) => file.id !== id)
  emit(
    'update:attachmentIds',
    uploaded.value.map((file) => file.id)
  )
}

const setUploading = (value: boolean) => {
  uploading.value = value
  emit('update:uploading', value)
}

const onPicked = async (value: File[] | File | null) => {
  // Vuetify 的 v-file-input 单文件/多文件两种返回形状都出现过，这里统一成数组；
  // 选完立刻清空输入框本身，列表由下面那份 uploaded 负责显示。
  const files = (Array.isArray(value) ? value : value ? [value] : []).filter(Boolean)
  picked.value = []
  if (files.length === 0) return

  setUploading(true)
  try {
    for (const file of files) {
      try {
        const { data } = await AttachmentsApi.upload({ type: 'file', file })
        uploaded.value.push({ id: data.id, name: file.name, size: file.size })
        emit(
          'update:attachmentIds',
          uploaded.value.map((item) => item.id)
        )
      } catch (error) {
        toast.error(
          t('tasks.attachmentPicker.uploadFailed', {
            name: file.name,
            error: error instanceof Error ? error.message : t('tasks.submit.unknownError'),
          })
        )
      }
    }
  } finally {
    setUploading(false)
  }
}

defineExpose({ uploaded, uploading })
</script>

<style scoped>
.form-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: rgb(var(--v-theme-surface));
  transition:
    border-color 0.2s ease,
    transform 0.2s ease,
    box-shadow 0.2s ease;
}

.form-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.15);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(var(--v-theme-primary), 0.05);
}
</style>
