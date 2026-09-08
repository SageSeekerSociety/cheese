<script setup lang="ts">
import type { MemberSummary, ProfileProject, UserProfile } from '../cx_types'

import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { useCachedResource } from '@/composables/useCachedResource'

import { getMemberSummary, getUserProfile } from '../api'
import { label, NOTIF_KIND, PROJECT_ROLE, TOPIC_STATUS } from '../labels'

// 个人主页 = LinkedIn / GitHub profile (spec §1). "项目过程即简历": the page is
// primarily the cross-project profile; the per-project member summary (TA 发起的
// 话题 / 等 TA 处理的事) is kept below as the in-project context.
defineOptions({ name: 'MemberView' })

const props = defineProps<{ projectId: string; handle: string }>()
const router = useRouter()

// 一个人一份缓存：key 里既有项目也有 handle，换谁都是另一份数据（useCachedResource）。
const { data, loading, error } = useCachedResource(
  () => `member:${props.projectId}:${props.handle}`,
  async (): Promise<{ profile: UserProfile | null; member: MemberSummary | null }> => {
    // Profile drives the header/skills/understanding; the member summary gives
    // the per-project lists. Fetch both; tolerate either being unavailable.
    const [prof, mem] = await Promise.all([
      getUserProfile(props.handle).catch(() => null),
      getMemberSummary(props.projectId, props.handle).catch(() => null),
    ])
    if (!prof && !mem) throw new Error('加载成员信息失败')
    return { profile: prof, member: mem }
  }
)

const profile = computed<UserProfile | null>(() => data.value?.profile ?? null)
const member = computed<MemberSummary | null>(() => data.value?.member ?? null)
const errorMessage = computed<string | null>(() => (error.value ? error.value.message || '加载成员信息失败' : null))

const initial = computed<string>(() => {
  const name = profile.value?.name || props.handle
  return name.slice(0, 1).toUpperCase()
})

const displayName = computed<string>(() => profile.value?.name || props.handle)

// Role line: prefer the role for the project we arrived from, fall back to the
// member summary role, then the first project on the profile.
const roleLine = computed<string>(() => {
  const fromProject = profile.value?.projects.find((p) => p.project_id === props.projectId)
  const raw = fromProject?.role || member.value?.role || profile.value?.projects[0]?.role || ''
  return label(PROJECT_ROLE, raw)
})

// Total contributions across projects → simple proportional bar (§10.1 spirit).
const totalContributions = computed<number>(() =>
  (profile.value?.projects ?? []).reduce((s, p) => s + p.contributions, 0)
)
const maxContributions = computed<number>(() =>
  Math.max(1, ...(profile.value?.projects ?? []).map((p) => p.contributions))
)
function contribPct(p: ProfileProject): number {
  return (p.contributions / maxContributions.value) * 100
}

// Status -> a neutral/semantic dot class (status as dot, not a colored chip).
function statusDotClass(status: string): string {
  if (status === 'active') return 'status-dot--ok'
  if (status === 'archived') return 'status-dot--muted'
  return 'status-dot--warn'
}

// Clicking a topic opens it in the project frame — this page is inside that
// frame, so it is a content-area change, not a jump somewhere else.
function openTopic(topicId: string) {
  router.push({
    name: 'workspace-topic',
    params: { projectId: props.projectId, topicId },
  })
}

// Clicking a project row goes to that project's member page for this user
// (their per-project portfolio), per the brief.
function openProject(p: ProfileProject) {
  router.push({
    name: 'member',
    params: { projectId: p.project_id, handle: props.handle },
  })
}

</script>

<template>
  <div class="member-page fill-height overflow-y-auto">
    <v-container class="py-6 page-container">
      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="errorMessage" type="error" density="comfortable">
        {{ errorMessage }}
      </v-alert>

      <template v-else>
        <!-- Profile header band (cover + large avatar) -->
        <v-card class="mb-6 overflow-hidden">
          <div class="profile-cover" />
          <div class="px-6 pb-5">
            <div class="profile-avatar">{{ initial }}</div>
            <div class="mt-3">
              <h1 class="t-page-title" style="font-size: 27px">{{ displayName }}</h1>
              <div class="t-meta mt-1">@{{ props.handle }}</div>
              <div v-if="roleLine" class="t-body c-text mt-1" style="font-weight: 500">
                {{ roleLine }}
              </div>
              <p v-if="profile?.bio" class="t-body c-muted mt-3 mb-0" style="max-width: 640px">
                {{ profile.bio }}
              </p>
            </div>
          </div>
        </v-card>

        <!-- 技能 / 兴趣 -->
        <v-card v-if="profile && (profile.skills.length || profile.interests.length)" class="mb-6">
          <v-card-text>
            <div v-if="profile.skills.length" class="mb-4">
              <div class="t-title mb-2">技能</div>
              <div class="d-flex flex-wrap ga-2">
                <span v-for="s in profile.skills" :key="s" class="chip-neutral">
                  {{ s }}
                </span>
              </div>
            </div>
            <div v-if="profile.interests.length">
              <div class="t-title mb-2">兴趣方向</div>
              <div class="d-flex flex-wrap ga-2">
                <span v-for="i in profile.interests" :key="i" class="chip-outline">
                  {{ i }}
                </span>
              </div>
            </div>
          </v-card-text>
        </v-card>

        <!-- 芝士眼中的 TA (spec §8.4: 个人记忆 / 芝士对 TA 的理解) -->
        <v-card class="mb-6">
          <v-card-title class="d-flex align-center ga-2 t-title pt-4">
            <v-icon size="19" class="c-faint">mdi-brain</v-icon>
            芝士眼中的 TA
          </v-card-title>
          <v-card-text>
            <div v-if="profile?.understanding?.length" class="d-flex flex-column ga-2">
              <div v-for="(u, idx) in profile.understanding" :key="idx" class="d-flex align-start ga-2">
                <span class="status-dot status-dot--muted" style="margin-top: 8px" />
                <span class="t-body">{{ u }}</span>
              </div>
            </div>
            <div v-else class="empty-state">
              <v-icon size="30" class="empty-state__icon">mdi-account-question-outline</v-icon>
              <span class="t-body c-muted">暂无观察记录</span>
            </div>
            <div class="t-meta mt-3" style="line-height: 1.5">
              这些理解来自芝士在协作中的持续观察，会随着一起做事不断加深
            </div>
          </v-card-text>
        </v-card>

        <!-- 参与的项目 (GitHub repo/contribution list) -->
        <v-card v-if="profile?.projects?.length" class="mb-6">
          <v-card-title class="d-flex align-center ga-2 t-title pt-4">
            <v-icon size="19" class="c-faint">mdi-folder-multiple-outline</v-icon>
            参与的项目
            <v-spacer />
            <span class="t-meta">共 {{ totalContributions }} 条贡献</span>
          </v-card-title>
          <v-list class="py-0">
            <template v-for="(p, idx) in profile.projects" :key="p.project_id">
              <v-divider v-if="idx > 0" />
              <v-list-item class="px-4 py-3" @click="openProject(p)">
                <div class="d-flex align-center ga-2 mb-1">
                  <v-icon size="16" class="c-faint">mdi-source-repository</v-icon>
                  <span class="t-body" style="font-weight: 500; color: var(--ink)">{{ p.name }}</span>
                  <span class="chip-neutral">{{ label(PROJECT_ROLE, p.role) }}</span>
                </div>
                <div class="t-meta mb-2">发起 {{ p.topics_started }} 个话题 · {{ p.contributions }} 条贡献</div>
                <!-- Contribution bar (relative to the user's most active project) -->
                <div class="contrib-track">
                  <div class="contrib-fill" :style="{ width: contribPct(p) + '%' }" />
                </div>
              </v-list-item>
            </template>
          </v-list>
        </v-card>

        <!-- Per-project context: TA 发起的话题 / 等 TA 处理的事 (still relevant
             when arriving from a project; from the member-summary endpoint). -->
        <template v-if="member">
          <div class="d-flex align-center ga-2 mb-2">
            <div class="t-eyebrow">本项目中</div>
            <v-spacer />
            <span v-if="member.weekly_contributions !== undefined" class="t-meta">
              本周贡献
              <strong style="font-family: var(--font-mono); color: var(--ink)">{{
                member.weekly_contributions
              }}</strong>
              条
            </span>
          </div>
          <v-row>
            <!-- 在忙哪些话题 (spec §7.2): active topics TA is contributing to. -->
            <v-col cols="12" md="6">
              <v-card height="100%">
                <v-card-title class="d-flex align-center ga-2 t-title pt-4">
                  <v-icon size="19" class="c-faint">mdi-progress-wrench</v-icon>
                  在忙的话题
                  <span v-if="(member.topics_active ?? []).length" class="chip-neutral">
                    {{ (member.topics_active ?? []).length }}
                  </span>
                </v-card-title>
                <v-card-text>
                  <div v-if="(member.topics_active ?? []).length === 0" class="c-faint t-body">暂无进行中的话题</div>
                  <v-list v-else density="comfortable" class="py-0">
                    <v-list-item
                      v-for="t in member.topics_active ?? []"
                      :key="t.id"
                      class="px-0"
                      @click="openTopic(t.id)"
                    >
                      <v-list-item-title>{{ t.title }}</v-list-item-title>
                      <template #append>
                        <span class="status-dot status-dot--ok" />
                      </template>
                    </v-list-item>
                  </v-list>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" md="6">
              <v-card height="100%">
                <v-card-title class="d-flex align-center ga-2 t-title pt-4">
                  <v-icon size="19" class="c-faint">mdi-account-voice</v-icon>
                  TA 发起的话题
                  <span v-if="member.topics_started.length" class="chip-neutral">
                    {{ member.topics_started.length }}
                  </span>
                </v-card-title>
                <v-card-text>
                  <div v-if="member.topics_started.length === 0" class="c-faint t-body">暂无发起的话题</div>
                  <v-list v-else density="comfortable" class="py-0">
                    <v-list-item v-for="t in member.topics_started" :key="t.id" class="px-0" @click="openTopic(t.id)">
                      <v-list-item-title>{{ t.title }}</v-list-item-title>
                      <template #append>
                        <span class="d-inline-flex align-center ga-1 c-muted" style="font-size: 12px">
                          <span class="status-dot" :class="statusDotClass(t.status)" />
                          {{ label(TOPIC_STATUS, t.status) }}
                        </span>
                      </template>
                    </v-list-item>
                  </v-list>
                </v-card-text>
              </v-card>
            </v-col>

            <v-col cols="12" md="6">
              <v-card height="100%">
                <v-card-title class="d-flex align-center ga-2 t-title pt-4">
                  <v-icon size="19" class="c-faint">mdi-bell-ring-outline</v-icon>
                  等 TA 处理的事
                  <span v-if="member.waiting_on_you.length" class="chip-neutral">
                    {{ member.waiting_on_you.length }}
                  </span>
                </v-card-title>
                <v-card-text>
                  <div v-if="member.waiting_on_you.length === 0" class="c-faint t-body">暂无待处理的事</div>
                  <v-list v-else density="comfortable" class="py-0">
                    <v-list-item v-for="t in member.waiting_on_you" :key="t.id" class="px-0">
                      <template #prepend>
                        <span class="status-dot status-dot--warn me-3" />
                      </template>
                      <v-list-item-title>{{ t.title }}</v-list-item-title>
                      <template #append>
                        <span class="chip-neutral">{{ label(NOTIF_KIND, t.kind) }}</span>
                      </template>
                    </v-list-item>
                  </v-list>
                </v-card-text>
              </v-card>
            </v-col>
          </v-row>
        </template>
      </template>
    </v-container>
  </div>
</template>

<style scoped>
.member-page {
  background: var(--canvas);
}
/* Header band — neutral inset, NOT an amber gradient. */
.profile-cover {
  height: 88px;
  background: var(--fill);
}
/* Profile avatar — large rounded-square, solid ink + reversed-out initial. */
.profile-avatar {
  width: 96px;
  height: 96px;
  margin-top: -48px;
  border-radius: var(--radius-lg);
  border: 4px solid var(--surface);
  background: var(--ink);
  /* 底色是 --ink（浅色近黑 / 深色近白），所以字必须是它的反面 —— 写死的白
     在深色下就是白底白字。--surface 正好是 --ink 的对面：17.4:1 / 15.4:1。 */
  color: var(--surface);
  font-size: 38px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
/* Neutral outline chip (interests). */
.chip-outline {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  color: var(--muted);
  border: 1px solid var(--line-2);
  padding: 1px 8px;
  border-radius: 6px;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 36px 0;
}
.empty-state__icon {
  color: var(--line-2);
}
/* Contribution bar per project — neutral ink, not amber. */
.contrib-track {
  height: 6px;
  /* 6px 高的进度条，两端本来就该是半圆 → --radius-pill（原来是 3px，不在阶梯上）。 */
  border-radius: var(--radius-pill);
  background: var(--fill-2);
  overflow: hidden;
}
.contrib-fill {
  height: 100%;
  border-radius: var(--radius-pill);
  background: var(--ink);
}
</style>
