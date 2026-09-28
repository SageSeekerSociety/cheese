<script setup lang="ts">
// 「转让项目」的确认框。所有者自己退不掉（后端 `DELETE /projects/{id}/membership`
// 会拒他：一走项目就没人管得了），所以「离开」对他就不是一颗按钮的事，而是先把手
// 交出去。这个弹窗只做交手这一件事：选一个人 → `PUT /projects/{id}/owner` → 刷新
// 项目行（`owner_handle` 换人，团队也可能跟着换）。
//
// 和 LeaveProjectDialog 同一套语义：确认、调接口、被拒不关窗——那句理由原样留在弹
// 窗里；重开时错误清掉。
//
// 两条交出项目的路，差别是项目跟不跟着走（后端 `set_project_owner` 的三段规则）：
//
// 1. **接手人是项目所属团队的成员** → 只换 owner，团队不动。转让人还在这个团队里，
//    所以他还看得见这个项目 —— 这是「换个名字」那一档。
// 2. **接手人不在团队里** → 项目整个搬到接手人的个人团队去，转让人从此不在项目里，
//    也看不到它的任何话题。这是**有去无回**的动作（项目属于某个共享团队时后端会拒：
//    那项目留在原团队里，转让人照样读得到，还能把 owner 拿回来，那不是转让是借），
//    所以这一档单独走一次二次确认，文案也把「项目跟着 TA 走」说在明处。
// 3. 团队里没有可交的人时（个人项目就一个成员），输入完整的用户名或邮箱，用
//    `GET /users/lookup` 精确找到那一个人。故意不支持模糊匹配 —— 后端不泄露名册，
//    界面也不该搜出一串相似的名字让人挑。
import type { LookedUpUser } from '@/api'
import type { ProjectMemberRow } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import { listProjectMembers, lookupUser, setProjectOwner } from '@/api'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ projectId: string }>()
const open = defineModel<boolean>({ required: true })

const store = useWorkspaceStore()

const transferring = ref(false)
const error = ref<string | null>(null)
const rows = ref<ProjectMemberRow[]>([])

/** Who the project is being handed to, and whether that means it moves. */
interface Picked {
  handle: string
  name: string
  avatar: string
  /** Not on the project's team → the project goes with them and the giver
   * leaves it for good. */
  outside: boolean
}

const picked = ref<Picked | null>(null)
/** The 有去无回 step: asked once more, with the consequence spelled out. */
const confirming = ref(false)

// 名册上的每一行都带 `source`（owner / team / external，队友是 agent），所以这里
// 挑的是**某一种**，不是「没有标记的那种」——以前的 `!m.source` 在 #1639 给每行补上
// `source` 之后恒为假，候选人永远是空，「暂无可以接手的成员」于是成了这块界面的常态。
const candidates = computed(() =>
  rows.value.filter((m) => m.source === 'team' && !m.agent && m.user_handle !== myHandle())
)

const movingOut = computed(() => picked.value?.outside === true)

// ---- 直接找一个人（用户名或邮箱，精确匹配）--------------------------------
const query = ref('')
const found = ref<LookedUpUser | null>(null)
const lookingUp = ref(false)
const lookupError = ref<string | null>(null)
let lookupTimer: ReturnType<typeof setTimeout> | null = null
let lookupSeq = 0

async function runLookup(raw: string) {
  const q = raw.trim()
  found.value = null
  lookupError.value = null
  if (!q) return
  const seq = ++lookupSeq
  lookingUp.value = true
  try {
    const user = await lookupUser(q)
    // 打字比请求快：只认最后一次发出去的那个，否则先回来的旧结果会盖掉新的。
    if (seq !== lookupSeq) return
    found.value = user
  } catch (e) {
    if (seq !== lookupSeq) return
    lookupError.value = e instanceof Error ? e.message : '没有找到这个账号'
  } finally {
    if (seq === lookupSeq) lookingUp.value = false
  }
}

watch(query, (raw) => {
  if (lookupTimer) clearTimeout(lookupTimer)
  lookupTimer = setTimeout(() => void runLookup(raw), 350)
})

const foundIsMe = computed(() => !!found.value && found.value.handle === myHandle())

function foundFace(u: LookedUpUser): string {
  return u.avatar_id == null ? '' : getAvatarUrl(u.avatar_id)
}

watch(open, (v) => {
  if (!v) return
  error.value = null
  picked.value = null
  confirming.value = false
  query.value = ''
  found.value = null
  lookupError.value = null
  void load()
})

async function load() {
  const pid = props.projectId
  try {
    const payload = await listProjectMembers(pid)
    if (props.projectId === pid && open.value) rows.value = payload.data
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载成员失败'
  }
}

function faceUrl(m: ProjectMemberRow): string {
  return m.avatar_id == null ? '' : getAvatarUrl(m.avatar_id)
}

function pickRow(m: ProjectMemberRow) {
  confirming.value = false
  picked.value = {
    handle: m.user_handle,
    name: m.name || m.user_handle,
    avatar: faceUrl(m),
    outside: false,
  }
}

function pickFound() {
  const user = found.value
  if (!user || foundIsMe.value) return
  // 名册上已经有他这个团队成员时，这就是第一档：只换 owner，项目不动。
  const row = candidates.value.find((m) => m.user_handle === user.handle)
  confirming.value = false
  picked.value = row
    ? { handle: user.handle, name: user.name || user.handle, avatar: faceUrl(row), outside: false }
    : {
        handle: user.handle,
        name: user.name || user.handle,
        avatar: foundFace(user),
        outside: true,
      }
}

function submit() {
  if (!picked.value) return
  if (movingOut.value) {
    // Ask once more: this one cannot be undone by asking for the project back.
    confirming.value = true
    return
  }
  void doTransfer()
}

async function doTransfer() {
  const handle = picked.value?.handle
  if (!handle) return
  transferring.value = true
  error.value = null
  try {
    await setProjectOwner(props.projectId, handle)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '转让失败'
    transferring.value = false
    confirming.value = false
    return
  }
  open.value = false
  // 转让一成立，项目行必须重新拉一遍（`owner_handle` 换了人，团队也可能跟着换了），
  // 名册也跟着刷，界面上的「退出 / 转让」两颗按钮才是按新身份长的。和退出一样用
  // allSettled——转让已经做成了，刷不成功不该把它变成失败。
  await Promise.allSettled([store.refreshProjects(), store.refreshMembers()])
  transferring.value = false
}
</script>

<template>
  <v-dialog v-model="open" max-width="480">
    <v-card>
      <v-card-title class="t-dialog-title pt-4">转让项目</v-card-title>
      <v-card-text class="t-body c-muted">
        项目所有者不能直接退出。把项目转让给另一个人后，你就可以退出
        <div v-if="candidates.length === 0" class="t-meta mt-3">暂无可以接手的成员</div>
        <v-list v-else density="compact" nav class="mt-2 transfer-list">
          <v-list-item
            v-for="m in candidates"
            :key="m.user_handle"
            :active="picked?.handle === m.user_handle"
            rounded="lg"
            @click="pickRow(m)"
          >
            <template #prepend>
              <UserAvatar :name="m.name || m.user_handle" :avatar="faceUrl(m)" :size="28" class="me-3" />
            </template>
            <v-list-item-title class="t-body">{{ m.name || m.user_handle }}</v-list-item-title>
            <v-list-item-subtitle class="t-meta">@{{ m.user_handle }}</v-list-item-subtitle>
          </v-list-item>
        </v-list>

        <div class="t-meta mt-4">团队里没人可交？直接找一个人：</div>
        <v-text-field
          v-model="query"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          clearable
          class="mt-2"
          label="用户名或邮箱（要写完整）"
          :loading="lookingUp"
        />
        <v-alert v-if="lookupError" type="warning" density="comfortable" class="mt-2">
          {{ lookupError }}
        </v-alert>
        <v-list v-if="found" density="compact" nav class="mt-2">
          <v-list-item
            :active="picked?.handle === found.handle"
            rounded="lg"
            :disabled="foundIsMe"
            @click="pickFound()"
          >
            <template #prepend>
              <UserAvatar :name="found.name || found.handle" :avatar="foundFace(found)" :size="28" class="me-3" />
            </template>
            <v-list-item-title class="t-body">{{ found.name || found.handle }}</v-list-item-title>
            <v-list-item-subtitle class="t-meta">@{{ found.handle }}</v-list-item-subtitle>
          </v-list-item>
        </v-list>

        <!-- 不在团队里 = 项目跟着 TA 走。这句话必须在按下按钮之前就说清楚，不能让人
             以为只是换个人挂名字。 -->
        <v-alert v-if="movingOut" type="warning" density="comfortable" class="mt-4">
          @{{ picked?.handle }} 不在这个项目的团队里：转让会把项目整个搬到 TA 名下。转完你就不再是项目成员，
          也看不到它的任何话题。（项目属于某个共享团队时只能转给团队里的人，后端会拒。）
        </v-alert>

        <v-alert v-if="error" type="error" density="comfortable" class="mt-4">
          {{ error }}
        </v-alert>
      </v-card-text>
      <v-card-actions v-if="confirming">
        <v-spacer />
        <v-btn variant="text" @click="confirming = false">返回</v-btn>
        <v-btn color="error" variant="flat" :loading="transferring" @click="doTransfer">确认转让</v-btn>
      </v-card-actions>
      <v-card-actions v-else>
        <v-spacer />
        <v-btn variant="text" @click="open = false">取消</v-btn>
        <v-btn color="primary" variant="flat" :loading="transferring" :disabled="!picked" @click="submit"> 转让 </v-btn>
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
