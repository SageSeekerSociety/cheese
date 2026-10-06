<script setup lang="ts">
// 「归档项目」的确认框。归档不删任何东西、随时能取消，但它一下子把项目从每个成员
// 眼前拿走、停掉正在跑的任务，所以要把项目名打一遍才放行：点错一颗按钮不该让一整
// 个团队找不到自己的项目。
//
// 和 TransferProjectDialog 同一套语义：被拒不关窗，那句理由原样留在弹窗里；重开时
// 清掉。归档成了就离开这个项目——它已经不在任何列表里了。
import { computed, ref, watch } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

import { archiveProject } from '@/api'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string; projectName: string }>()
const open = defineModel<boolean>({ required: true })

const store = useWorkspaceStore()
const navigation = useNavigation()

const typed = ref('')
const archiving = ref(false)
const error = ref<string | null>(null)

const confirmed = computed(() => typed.value.trim() === props.projectName.trim())

watch(open, (v) => {
  if (!v) return
  typed.value = ''
  error.value = null
})

async function submit() {
  if (!confirmed.value) return
  archiving.value = true
  error.value = null
  try {
    await archiveProject(props.projectId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.projectSettings.archive.dialog.failed')
    archiving.value = false
    return
  }
  open.value = false
  archiving.value = false
  // 项目已经不在清单里了：清单刷一遍，人回到首页（落在另一个项目上，或者待办）。刷不成功不该把归档变成失败。
  await Promise.allSettled([store.refreshProjects()])
  // replace：归档完再按回退键，人不该又落回这个项目的设置页 —— 它已经不在清单里了。
  navigation?.navigate('/', { replace: true })
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
    <v-alert v-if="error" type="error" density="comfortable" class="mt-4">
      {{ error }}
    </v-alert>
  </AdaptiveDialog>
</template>
