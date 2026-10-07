<script setup lang="ts">
// 「退出项目」的确认框，从成员页打开。「点了之后发生什么」只有这一份：
// `useLeaveProject`（确认、DELETE /projects/{id}/membership、刷新名册和项目列表、
// 离开这个项目），名册读在 `useProjectMembers`。
import { computed, watch } from 'vue'

import { useLeaveProject } from '@/composables/useLeaveProject'
import { useProjectMembers } from '@/composables/useProjectMembers'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()
const open = defineModel<boolean>({ required: true })

const store = useWorkspaceStore()

// 「退出的是项目不是团队」只对随团队进来的人成立：被邀请进来的外部成员、自己名下
// 项目里的人，本来就不在什么团队里。
//
// 读的是**要退的那个项目**的名册：从 rail 右键退的可能不是正开着的这个，store 里那份
// 名册是正开着那个的。不是同一个就打开时读一次；读到之前、读不到，都按不提团队说。
const { rows: otherRoster, load: loadOtherRoster, reset: resetOtherRoster } = useProjectMembers(() => props.projectId)
const roster = computed(() => (props.projectId === store.projectId ? store.members : otherRoster.value))
const viaTeam = computed(() => roster.value.find((member) => member.user_handle === myHandle())?.source === 'team')
watch(
  [open, () => props.projectId],
  async ([isOpen, projectId]) => {
    if (!isOpen || projectId === store.projectId) return
    // 打开时先退回「还不知道」：上一次那一份措辞不该顶到这一次读回来。
    resetOtherRoster()
    try {
      // 回来时项目已经换了，这一份就不写进去（在 composable 里判）。
      await loadOtherRoster()
    } catch {
      // 只是确认框里的一句措辞，读不到就用不提团队的那一句。
    }
  },
  { immediate: true }
)

const { leaving, error, leave, clearError } = useLeaveProject(() => props.projectId)
watch(open, (v) => {
  if (v) clearError()
})

async function confirmLeave() {
  // 成了就收掉确认框（刷新和跳转在背后做）；被拒就不关，理由留在框里。
  if (await leave()) open.value = false
}
</script>

<template>
  <ConfirmDialog
    v-model="open"
    :title="t('project.leave.title')"
    :confirm-label="t('project.leave.confirm')"
    danger
    :loading="leaving"
    @confirm="confirmLeave"
  >
    {{ t(viaTeam ? 'project.leave.bodyTeam' : 'project.leave.body') }}
    <v-alert v-if="error" type="error" density="comfortable" class="mt-4">
      {{ error }}
    </v-alert>
  </ConfirmDialog>
</template>
