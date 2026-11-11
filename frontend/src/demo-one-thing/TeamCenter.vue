<script setup lang="ts">
// A team's 项目 on 首页: the projects the team owns. My own team holds the
// projects I claimed alone; a group team holds the projects it claimed
// together, with every member in them. A project claimed from a 题目 says
// which one (来自 空间 · 题目). Only the demo's own projects open; the others
// are there to show where they live.
import { computed } from 'vue'

import { topicOf } from './course'
import { CLAIMED, type ClaimKey, NAMES, OTHER_PROJECTS, PROJECT, SPACE, teamOf } from './data'
import { openSpace, S, showTopic, switchProject, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'

const team = computed(() => teamOf(S.teamView))

interface Row {
  id: string
  name: string
  origin?: { id: string; title: string }
  open?: () => void
}

const rows = computed<Row[]>(() => {
  const out: Row[] = []
  if (team.value.handle === 'vl-group')
    out.push({ id: 'research', name: PROJECT.name, open: () => switchProject('research') })
  for (const k of S.claimed as ClaimKey[])
    if (CLAIMED[k].team === team.value.handle) {
      const t = topicOf(k)
      out.push({ id: k, name: CLAIMED[k].name, origin: { id: t.id, title: t.title }, open: () => switchProject(k) })
    }
  for (const p of OTHER_PROJECTS) if (p.team === team.value.handle) out.push({ id: p.id, name: p.name })
  return out
})
</script>

<template>
  <section class="ot-team" data-zone="center">
    <header class="ot-head">
      <UserAvatar :size="22" :name="team.name" :seed="team.handle" :kind="team.personal ? 'person' : 'org'" />
      <h2 class="ot-head__title">{{ team.name }}</h2>
      <span class="ot-head__sub">{{ team.personal ? '你自己的团队：只有你' : `${team.members.length} 位成员` }}</span>
      <span class="ot-head__gap" />
      <span class="ot-team__faces">
        <UserAvatar v-for="h in team.members" :key="h" :size="22" :name="NAMES[h]" :seed="h" :title="NAMES[h]" />
      </span>
    </header>
    <div class="ot-team__body">
      <h3 class="t-eyebrow-read ot-team__label">项目 · {{ rows.length }}</h3>
      <div v-if="rows.length" class="ot-team__list">
        <div v-for="r in rows" :key="r.id" class="ot-team__row" :data-team-project="r.id">
          <v-icon size="18" icon="mdi-folder-outline" class="ot-team__icon" />
          <div class="ot-team__main">
            <span class="ot-team__name">{{ r.name }}</span>
            <button v-if="r.origin" type="button" class="ot-team__origin" @click="showTopic(r.origin.id)">
              来自 {{ SPACE.name }} · {{ r.origin.title }}
            </button>
          </div>
          <BaseButton
            kind="secondary"
            size="sm"
            @click="r.open ? r.open() : toast('演示里能进的是论文复现和从空间领取的项目')"
            >进入</BaseButton
          >
        </div>
      </div>
      <div v-else class="ot-team__empty">
        <p>这个团队还没有项目。</p>
        <p>
          在空间里领一道组队题目、选「和团队一起领」，项目就建在这里，组里每个人都在里面。
          <BaseButton kind="ghost" size="sm" @click="openSpace('tasks')">去「{{ SPACE.name }}」看题目</BaseButton>
        </p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ot-team {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}

.ot-team__faces {
  display: flex;
  gap: 2px;
}

.ot-team__body {
  flex: 1;
  min-height: 0;
  padding: 16px 24px 32px;
  overflow: auto;
}

.ot-team__label {
  margin: 0 0 8px;
}

.ot-team__list {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ot-team__row {
  display: flex;
  gap: 12px;
  align-items: center;
  min-height: 56px;
  padding: 8px 12px 8px 16px;
}

.ot-team__row + .ot-team__row {
  border-top: 1px solid var(--line);
}

.ot-team__icon {
  color: var(--muted);
}

.ot-team__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
  min-width: 0;
}

.ot-team__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.ot-team__origin {
  padding: 0;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
}

.ot-team__origin:hover {
  color: var(--ink);
  text-decoration: underline;
}

.ot-team__empty {
  padding: 24px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-lg);
}

.ot-team__empty p {
  margin: 0 0 6px;
}
</style>
