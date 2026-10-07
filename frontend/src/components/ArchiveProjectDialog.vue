<script setup lang="ts">
// 「归档项目」的确认框。归档不删任何东西、随时能取消，但它一下子把项目从每个成员
// 眼前拿走、停掉正在跑的任务，所以要把项目名打一遍才放行：点错一颗按钮不该让一整
// 个团队找不到自己的项目。
//
// 和 TransferProjectDialog 同一套语义：被拒不关窗，那句理由原样留在弹窗里；重开时
// 清掉。
//
// 这一只是哑的：发请求、刷清单、离开项目都在 `composables/useProjectArchive.ts`，由
// 设置页面接线（`src/components` 下的组件不许碰 API 层，见 guards 的
// import-boundary）。它只收「正在归档」和「被拒的理由」，名字打对了就往外 emit
// `archive`；一次归档结束（`archiving` 落回 false）而没有理由，就是成了，自己关窗。
import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = withDefaults(defineProps<{ projectName: string; archiving?: boolean; error?: string }>(), {
  archiving: false,
  error: '',
})
const emit = defineEmits<{ (e: 'archive'): void }>()
const open = defineModel<boolean>({ required: true })

const typed = ref('')
// 弹窗里画的理由：跟着 props.error 走，但重开时清掉——上一次被拒的那句不该留到下一次。
const shownError = ref('')

const confirmed = computed(() => typed.value.trim() === props.projectName.trim())

watch(open, (v) => {
  if (!v) return
  typed.value = ''
  shownError.value = ''
})

watch(
  () => props.error,
  (e) => {
    shownError.value = e
  }
)

watch(
  () => props.archiving,
  (now, before) => {
    if (before && !now && !props.error) open.value = false
  }
)

function submit() {
  if (!confirmed.value || props.archiving) return
  emit('archive')
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.projectSettings.archive.dialog.title')"
    :primary-label="t('work.projectSettings.archive.dialog.submit')"
    primary-danger
    :primary-loading="archiving"
    :primary-disabled="!confirmed"
    :max-width="480"
    @primary="submit"
  >
    <div class="t-body c-muted">
      {{ t('work.projectSettings.archive.dialog.body') }}
    </div>
    <v-text-field
      v-model="typed"
      autocomplete="off"
      density="compact"
      variant="outlined"
      hide-details
      class="mt-4"
      :label="t('work.projectSettings.archive.dialog.confirmLabel', { name: projectName })"
      @keydown.enter.prevent="submit"
    />
    <v-alert v-if="shownError" type="error" density="comfortable" class="mt-4">
      {{ shownError }}
    </v-alert>
  </AdaptiveDialog>
</template>
