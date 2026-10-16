<script setup lang="ts">
// 「转让项目」（#3830）：所有者把项目交给项目里的一个人。对方接受之后项目才归对方，
// 原所有者变成管理员；个人项目会移到对方名下。只能交给项目里的管理员或成员，导师和
// AI 队友不在可选之列。不经对方同意把项目塞给别人的路没有了。
//
// 被拒不关窗：那句理由原样留在弹窗里；重开时错误清掉。
import type { ProjectMemberRow } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { useProjectMembers } from '@/composables/useProjectMembers'
import { useProjectTransfer } from '@/composables/useProjectTransfer'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()
const open = defineModel<boolean>({ required: true })

const store = useWorkspaceStore()

const { rows, load: loadRoster } = useProjectMembers(() => props.projectId)
const { transferring, error, transfer, clearError } = useProjectTransfer(() => props.projectId)

// The roster row carries where each person sits (`role`); the list endpoint adds it.
type Seated = ProjectMemberRow & { role?: string }
const candidates = computed(() =>
  (rows.value as Seated[]).filter(
    (m) => !m.agent && m.user_handle !== myHandle() && (m.role === 'admin' || m.role === 'member')
  )
)

const picked = ref<string | null>(null)

// 项目在所有者自己名下：团队的 handle 就是他的用户名。转让后项目会移到对方名下。
const ownProject = computed(() => {
  const project = store.projects.find((p) => p.id === props.projectId)
  return !!project?.team_handle && project.team_handle === project.owner_handle
})

watch(open, (v) => {
  if (!v) return
  clearError()
  picked.value = null
  void load()
})

async function load() {
  try {
    await loadRoster(() => open.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.projectTransfer.loadFailed')
  }
}

function faceUrl(m: ProjectMemberRow): string {
  return m.avatar_id == null ? '' : getAvatarUrl(m.avatar_id)
}

async function submit() {
  if (!picked.value) return
  if (await transfer(picked.value)) open.value = false
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.projectTransfer.title')"
    :primary-label="t('work.projectTransfer.submit')"
    :primary-loading="transferring"
    :primary-disabled="!picked"
    :cancel-label="t('work.projectTransfer.cancel')"
    :max-width="480"
    @primary="submit"
  >
    <div class="t-body c-muted">
      {{ t('work.projectTransfer.intro') }}
      <p v-if="ownProject" class="t-meta mt-3 mb-0">{{ t('work.projectTransfer.ownMoves') }}</p>
      <div v-if="candidates.length === 0" class="t-meta mt-3">{{ t('work.projectTransfer.noCandidates') }}</div>
      <v-list v-else density="compact" nav class="mt-2 transfer-list">
        <v-list-item
          v-for="m in candidates"
          :key="m.user_handle"
          :active="picked === m.user_handle"
          rounded="lg"
          @click="picked = m.user_handle"
        >
          <template #prepend>
            <UserAvatar :name="m.name || m.user_handle" :avatar="faceUrl(m)" :size="28" class="me-3" />
          </template>
          <v-list-item-title class="t-body">{{ m.name || m.user_handle }}</v-list-item-title>
          <v-list-item-subtitle class="t-meta">{{ m.user_handle }}</v-list-item-subtitle>
        </v-list-item>
      </v-list>
      <v-alert v-if="error" type="error" density="comfortable" class="mt-4">
        {{ error }}
      </v-alert>
    </div>
  </AdaptiveDialog>
</template>

<style scoped>
.transfer-list {
  max-height: 240px;
  overflow-y: auto;
}
</style>
