<script setup lang="ts">
// 「转让项目」的确认框。所有者自己退不掉（后端 `DELETE /projects/{id}/membership`
// 会拒他：一走项目就没人管得了），所以「离开」对他就不是一颗按钮的事，而是先把手
// 交出去。这个弹窗只做交手这一件事：选一个名册上的人 → `PUT /projects/{id}/owner`
// → 刷新项目行（`owner_handle` 变了，他现在是普通成员，退出的按钮随之出现）。
//
// 和 LeaveProjectDialog 同一套语义：确认、调接口、被拒不关窗——那句理由（不在名册
// 上、得先加进来）就是用户要的下一步，原样留在弹窗里；重开时错误清掉。
import type { ProjectMemberRow } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import { listProjectMembers, setProjectOwner } from '@/api'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
import { getAvatarUrl } from '@/utils/materials'

const props = defineProps<{ projectId: string }>()
const open = defineModel<boolean>({ required: true })

const store = useWorkspaceStore()

const transferring = ref(false)
const error = ref<string | null>(null)
const rows = ref<ProjectMemberRow[]>([])
const target = ref<string | null>(null)

// 只有「名册上真有他一行」的人接得住：后端要求新所有者已经是成员，而小队带进来
// 的人、所有者那一行补出来的行背后都没有成员表记录，AI 队友也不是能把项目扛走的
// 人。自己也不列——转让是把手交出去，不是左手倒右手。
const candidates = computed(() => rows.value.filter((m) => !m.source && !m.agent && m.user_handle !== myHandle()))

watch(open, (v) => {
  if (!v) return
  error.value = null
  target.value = null
  void load()
})

async function load() {
  const pid = props.projectId
  try {
    const payload = await listProjectMembers(pid)
    if (props.projectId === pid && open.value) rows.value = payload.data
  } catch (e) {
    error.value = e instanceof Error ? e.message : '拿不到成员名单'
  }
}

async function confirmTransfer() {
  const handle = target.value
  if (!handle) return
  transferring.value = true
  error.value = null
  try {
    await setProjectOwner(props.projectId, handle)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '转让失败'
    transferring.value = false
    return
  }
  open.value = false
  // 转让一成立，他就是普通成员了：项目行必须重新拉一遍（`owner_handle` 换人），
  // 名册也跟着刷，界面上的「退出 / 转让」两颗按钮才是按新身份长的。和退出一样用
  // allSettled——转让已经做成了，刷不成功不该把它变成失败。
  await Promise.allSettled([store.refreshProjects(), store.refreshMembers()])
  transferring.value = false
}

function faceUrl(m: ProjectMemberRow): string {
  return m.avatar_id == null ? '' : getAvatarUrl(m.avatar_id)
}
</script>

<template>
  <v-dialog v-model="open" max-width="440">
    <v-card>
      <v-card-title class="t-title pt-4">转让项目</v-card-title>
      <v-card-text class="t-body c-muted">
        项目所有者不能直接退出——一走这个项目就没人管得了。先把它交给名册上的另一个人，你变成普通成员之后就可以退出了
        <div v-if="candidates.length === 0" class="t-meta mt-3">
          名册上还没有可以接手的人——先把另一个人加进项目成员，再来转让
        </div>
        <v-list v-else density="compact" nav class="mt-2 transfer-list">
          <v-list-item
            v-for="m in candidates"
            :key="m.user_handle"
            :active="target === m.user_handle"
            rounded="lg"
            @click="target = m.user_handle"
          >
            <template #prepend>
              <UserAvatar :name="m.name || m.user_handle" :avatar="faceUrl(m)" :size="28" class="me-3" />
            </template>
            <v-list-item-title class="t-body">{{ m.name || m.user_handle }}</v-list-item-title>
            <v-list-item-subtitle class="t-meta">@{{ m.user_handle }}</v-list-item-subtitle>
          </v-list-item>
        </v-list>
        <v-alert v-if="error" type="error" density="comfortable" class="mt-4">
          {{ error }}
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="open = false">取消</v-btn>
        <v-btn color="primary" variant="flat" :loading="transferring" :disabled="!target" @click="confirmTransfer">
          转让
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.transfer-list {
  max-height: 240px;
  overflow-y: auto;
}
</style>
