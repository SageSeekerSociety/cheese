<script setup lang="ts">
// 成员与角色。这一屏是要求 1 的落点：全篇只有三种角色，没有第四种，也别处
// 一样 —— i18n 两个语言包、源码注释、后端注释里都不再有按「教 / 学」分人的称谓。
//
// 今天界面上把管理员叫「管理员」、把成员叫「成员」，但**库里根本没有这两个值**——
// 它是 i18n 文案层套在 OWNER/ADMIN 上的一个隐喻（`auth/space_access.py` 把这件事
// 写成了设计决策）。去掉隐喻之后，这一页要能回答三个问题：谁能管理、谁只是成员、
// 以及**一个人怎么从成员变成管理员**。
//
// 「加入方式」那一列（是靠哪个邀请码进来的）现在有了：接口的成员行带 `inviteCode`，
// 来源是 `space_member.invite_code_id`，只在核销那一刻写下。**它是「有记录」的证据，
// 不是「没用过码」的证据** —— 加这一格之前进来的成员没有记录，所有者直接加进来的人
// 也没有，两者在行上长得一模一样，所以一律显示「未知」，不拿板上现有的某张码顶上：
// 那会把一条没记过的事实说成一条很确定的事实。
import type { SpaceMember } from '@/types'

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'

import PanelCard from '../components/PanelCard.vue'
import { type Person, type Role, ROLE_LABEL } from '../model'
import { currentCode, isOwner, loadBoard, loadCodes, me, space } from '../store'

import { SpacesApi } from '@/network/api/spaces'

const route = useRoute()
const spaceId = Number(route.params.spaceId)

interface Row {
  person: Person
  role: Role
  userId: number
  /** 加入方式：这张码的串，或 `null` = 没有记录（界面写「未知」）。 */
  viaCode: string | null
}

const members = ref<SpaceMember[]>([])
const keyword = ref('')
const busy = ref(false)

async function refresh() {
  const res = await SpacesApi.listMembers(spaceId)
  members.value = res.data.members ?? []
}

refresh()
loadCodes()

const rows = computed<Row[]>(() => {
  /** 角色只有一处来源：`space.admins`（含所有者）。名单里没有的人就是成员 ——
   *  这正是「不在管理员名单里」作为隐式成员的那件事，在这里被显式说成 MEMBER。 */
  const roleByHandle = new Map<string, Role>((space.value?.admins ?? []).map((p) => [p.handle, 'ADMIN']))
  const owner = space.value?.owner
  if (owner) roleByHandle.set(owner.handle, 'OWNER')

  return members.value.map((m) => {
    const user = m.user
    const handle = user?.username ?? String(m.userId)
    return {
      person: { handle, name: user?.nickname || handle },
      role: roleByHandle.get(handle) ?? 'MEMBER',
      userId: m.userId,
      // 老接口不带这一格、库里没记过也是 `null` —— 两种都读成「没有记录」。
      viaCode: m.inviteCode?.code ?? null,
    }
  })
})

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const list = kw
    ? rows.value.filter((r) => r.person.name.toLowerCase().includes(kw) || r.person.handle.includes(kw))
    : rows.value
  const order: Record<Role, number> = { OWNER: 0, ADMIN: 1, MEMBER: 2 }
  return [...list].sort((a, b) => order[a.role] - order[b.role])
})

const managerCount = computed(() => rows.value.filter((r) => r.role !== 'MEMBER').length)

/** 改角色是**只有所有者**能做的事（`Action.ADMIN` 只挂在 OWNER 上）。 */
async function setRole(row: Row, next: Role) {
  if (!isOwner.value || next === 'OWNER') return
  busy.value = true
  try {
    if (next === 'ADMIN') await SpacesApi.addAdmin(spaceId, { userId: row.userId, role: 'ADMIN' })
    else await SpacesApi.removeAdmin(spaceId, row.userId)
    toast.success('已更新')
  } catch {
    toast.error('更新失败')
  } finally {
    busy.value = false
    // 角色是从 `space.admins` 算出来的，所以名单动完要把空间那份一起刷了。
    await Promise.all([refresh(), loadBoard(spaceId, true)])
  }
}
</script>

<template>
  <div class="mem">
    <div class="mem__head">
      <div>
        <h1>成员与角色</h1>
        <p>三种角色：所有者、管理员、成员。没有第四种。</p>
      </div>
      <v-chip label variant="tonal">{{ rows.length }} 人 · {{ managerCount }} 个管理位</v-chip>
    </div>

    <div class="mem__legend">
      <div class="legend">
        <b>所有者</b>
        <span>建板的人，只有一位。能改别人的角色、能删板、能进所有管理界面。</span>
      </div>
      <div class="legend">
        <b>管理员</b>
        <span>由所有者授予。能审题、能看整板看板、能管邀请码与分类；不能改成员角色。</span>
      </div>
      <div class="legend">
        <b>成员</b>
        <span>能出题、能领题、能看自己的数据。看不到任何整板汇总。</span>
      </div>
    </div>

    <PanelCard>
      <template #actions>
        <v-text-field
          v-model="keyword"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          prepend-inner-icon="mdi-magnify"
          placeholder="搜名字或 handle"
          style="max-width: 220px"
        />
      </template>

      <v-table density="comfortable" class="mem__table">
        <thead>
          <tr>
            <th>成员</th>
            <th>角色</th>
            <th>加入方式</th>
            <th class="num">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filtered" :key="row.person.handle">
            <td>
              <div class="mem__who">
                <v-avatar size="26" class="mem__avatar">{{ row.person.name.slice(0, 1) }}</v-avatar>
                <div>
                  <div class="mem__name">
                    {{ row.person.name }}
                    <span v-if="row.person.handle === me.handle" class="mem__you">（你）</span>
                  </div>
                  <div class="mem__handle">{{ row.person.handle }}</div>
                </div>
              </div>
            </td>
            <td>
              <v-chip
                size="small"
                label
                variant="tonal"
                :class="row.role === 'OWNER' ? 'role-owner' : row.role === 'ADMIN' ? 'role-admin' : 'role-member'"
              >
                {{ ROLE_LABEL[row.role] }}
              </v-chip>
            </td>
            <td>
              <!-- 有记录就写那张码；没有就写「未知」——**不写空白，也不写「没用码」**：
                   空白读起来像「还没查」，而「没记过」和「没用过」是两句话。 -->
              <code v-if="row.viaCode" class="mem__code">{{ row.viaCode }}</code>
              <span v-else class="mem__unknown">未知</span>
            </td>
            <td class="num">
              <v-btn
                v-if="isOwner && row.role !== 'OWNER'"
                size="small"
                variant="text"
                :disabled="busy"
                @click="setRole(row, row.role === 'ADMIN' ? 'MEMBER' : 'ADMIN')"
              >
                {{ row.role === 'ADMIN' ? '降为成员' : '设为管理员' }}
              </v-btn>
              <span v-else-if="!isOwner" class="mem__note">只有所有者能改</span>
            </td>
          </tr>
        </tbody>
      </v-table>
    </PanelCard>

    <PanelCard title="让人进来" subtitle="发邀请码，对方用码加入">
      <div class="join">
        <code v-if="currentCode" class="join__code">{{ currentCode.code }}</code>
        <span v-else class="join__none">没有可用的码</span>
        <span class="join__hint">
          <template v-if="currentCode">
            当前码：{{ currentCode.useCount }} / {{ currentCode.maxUses ?? '不限' }} 人已用。
          </template>
          <template v-else>用尽或过期的码不会顶上来 —— 去下拉窗口里新建一个，否则没人能加入。</template>
          可用人数与有效期在下拉窗口里随时可调。
        </span>
      </div>
      <p class="mem__foot">
        可用人数与有效期在头部那块下拉的「邀请码」里随时可调，改完立刻生效；不需要的码在那里撤销。
      </p>
    </PanelCard>
  </div>
</template>

<style scoped lang="scss">
.mem__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.mem__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.mem__head p {
  max-width: 640px;
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.83rem;
  line-height: 1.7;
}

.mem__legend {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.legend {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 12px;
}

.legend b {
  font-size: 0.84rem;
}

.legend span {
  color: rgba(var(--v-theme-on-surface), 0.58);
  font-size: 0.76rem;
  line-height: 1.6;
}

.mem__table {
  background: transparent;
}

.mem__table :deep(th) {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
  font-weight: 500;
}

.mem__who {
  display: flex;
  gap: 10px;
  align-items: center;
}

.mem__avatar {
  color: rgba(var(--v-theme-on-surface), 0.8);
  font-size: 0.72rem;
  background: rgba(var(--v-theme-on-surface), 0.1);
}

.mem__name {
  font-size: 0.86rem;
}

.mem__you {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.74rem;
}

.mem__handle {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.72rem;
}

.mem__note {
  margin-left: 8px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.74rem;
}

.mem__code {
  padding: 3px 8px;
  font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 0.78rem;
  letter-spacing: 0.05em;
  background: rgba(var(--v-theme-on-surface), 0.05);
  border-radius: 6px;
}

.mem__unknown {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.78rem;
}

.role-owner {
  color: rgb(var(--v-theme-primary));
}

.role-admin {
  color: rgb(var(--v-theme-success));
}

.role-member {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.num {
  text-align: right;
}

.join {
  display: flex;
  gap: 14px;
  align-items: center;
  flex-wrap: wrap;
}

.join__code {
  padding: 6px 12px;
  font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 0.95rem;
  letter-spacing: 0.06em;
  background: rgba(var(--v-theme-on-surface), 0.05);
  border-radius: 8px;
}

.join__none {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.85rem;
}

.join__hint {
  color: rgba(var(--v-theme-on-surface), 0.58);
  font-size: 0.78rem;
}

.mem__foot {
  margin: 14px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.75rem;
}
</style>
