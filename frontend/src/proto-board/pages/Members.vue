<script setup lang="ts">
// 成员与角色。这一屏是要求 1 的落点：全篇只有三种角色，没有第四种，也别处
// 一样 —— i18n 两个语言包、源码注释、后端注释里都不再有按「教 / 学」分人的称谓。
//
// 今天界面上把管理员叫「管理员」、把成员叫「成员」，但**库里根本没有这两个值**——
// 它是 i18n 文案层套在 OWNER/ADMIN 上的一个隐喻（`auth/space_access.py:1-13` 把
// 这件事写成了设计决策）。去掉隐喻之后，这一页要能回答三个问题：
// 谁能管理、谁只是成员、以及**一个人怎么从成员变成管理员**。
import { computed, ref } from 'vue'

import PanelCard from '../components/PanelCard.vue'
import { PEOPLE, type Person, ROLE_LABEL } from '../fixtures'
import { codes, isOwner, me } from '../store'

interface Row {
  person: Person
  role: 'OWNER' | 'ADMIN' | 'MEMBER'
  /** 这个人是靠哪个邀请码进来的 —— 成员管理里最常被问的一句。 */
  viaCode?: string
  notes?: string
}

const MEMBERS: Row[] = [
  { person: PEOPLE.caisongyang, role: 'OWNER', notes: '建板的人' },
  { person: PEOPLE.maxiaoyu, role: 'ADMIN', viaCode: 'BOARD-2K9F', notes: '由所有者授予' },
  { person: PEOPLE.pengwenbo, role: 'MEMBER', viaCode: 'BOARD-2K9F' },
  { person: PEOPLE.chiruotong, role: 'MEMBER', viaCode: 'BOARD-2K9F' },
  { person: PEOPLE.wangchangxin, role: 'MEMBER', viaCode: 'SPRINT-OCT' },
  { person: PEOPLE.ligan, role: 'MEMBER', viaCode: 'SPRINT-OCT' },
  { person: PEOPLE.n1ctheboy, role: 'MEMBER', viaCode: 'OPEN-DOOR' },
  { person: PEOPLE.andy, role: 'MEMBER', viaCode: 'OPEN-DOOR' },
  { person: PEOPLE.andylizf, role: 'MEMBER', viaCode: 'OPEN-DOOR' },
]

const rows = ref<Row[]>(MEMBERS.map((r) => ({ ...r })))
const keyword = ref('')

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const list = kw
    ? rows.value.filter((r) => r.person.name.toLowerCase().includes(kw) || r.person.handle.includes(kw))
    : rows.value
  const order = { OWNER: 0, ADMIN: 1, MEMBER: 2 }
  return [...list].sort((a, b) => order[a.role] - order[b.role])
})

const managerCount = computed(() => rows.value.filter((r) => r.role !== 'MEMBER').length)

const activeCode = computed(() => codes.value.find((c) => !c.revoked && !c.revoked))

function promote(row: Row) {
  row.role = row.role === 'MEMBER' ? 'ADMIN' : 'MEMBER'
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
              <span v-if="row.notes" class="mem__note">{{ row.notes }}</span>
            </td>
            <td>
              <code v-if="row.viaCode" class="mem__code">{{ row.viaCode }}</code>
              <span v-else class="mem__note">建板时就在</span>
            </td>
            <td class="num">
              <v-btn v-if="isOwner && row.role !== 'OWNER'" size="small" variant="text" @click="promote(row)">
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
        <code class="join__code">{{ activeCode?.code ?? '暂无可用码' }}</code>
        <span class="join__hint">
          当前码：{{
            activeCode ? `${activeCode.useCount} / ${activeCode.maxUses ?? '不限'} 人已用` : '去头部下拉里新建一个'
          }}。 可用人数与有效期在下拉窗口里随时可调。
        </span>
      </div>
      <p class="mem__foot">成员是「怎么进来的」这件事在这一页是可见的 —— 退群、找错码、码被撤了，都要能对到人。</p>
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
  padding: 2px 8px;
  font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 0.74rem;
  background: rgba(var(--v-theme-on-surface), 0.05);
  border-radius: 6px;
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
