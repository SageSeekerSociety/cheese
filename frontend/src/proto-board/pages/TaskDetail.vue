<script setup lang="ts">
// 题目详情。领取按钮**有状态**：没领 / 进行中 / 已提交 / 已通过，四种状态说四种话。
//
// 右侧那一栏是「这道题现在什么情况」，但只对出题人和管理员展开明细；普通领取者
// 只看得到自己的状态和别人领取的进度条 —— 谁领了是板内公开信息，谁做到哪一步不是。
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PanelCard from '../components/PanelCard.vue'
import TrendChart from '../components/TrendChart.vue'
import {
  bilibiliBvid,
  CLAIM_LABEL,
  DAY_LABELS,
  deadlineText,
  FILE_ICON,
  fileSize,
  isOpen,
  STATE_LABEL,
  type TaskFile,
  videoEmbedUrl,
} from '../fixtures'
import { alreadyClaimed, canManageTask, claimTask, myClaim, reviewWork, submitWork, tasks } from '../store'

const route = useRoute()
const router = useRouter()

const task = computed(() => tasks.value.find((t) => t.id === String(route.params.id)))

const claimed = computed(() => (task.value ? alreadyClaimed(task.value) : false))
const mine = computed(() => (task.value ? myClaim(task.value) : undefined))
const full = computed(
  () => task.value?.participantLimit !== null && (task.value?.claims.length ?? 0) >= (task.value?.participantLimit ?? 0)
)
const open = computed(() => (task.value ? isOpen(task.value) : false))
const canSeeRoster = computed(() => (task.value ? canManageTask(task.value) : false))

const claimBlocked = ref(false)

const fillPct = computed(() => {
  const t = task.value
  if (!t || t.participantLimit === null) return 0
  return Math.min(100, Math.round((t.claims.length / t.participantLimit) * 100))
})

function doClaim() {
  if (!task.value) return
  if (!claimTask(task.value.id)) claimBlocked.value = true
}

/** 交作业：领取之后才出现。真平台是往这道题上递一份提交（交什么由题目的
 *  submissionSchema 定），这里把状态从「进行中」推到「已提交」。 */
function doSubmit() {
  if (task.value) submitWork(task.value.id)
}

/** 判作业：出题人本人或管理员都能判（真平台 `may_teach_task`）。 */
function doReview(handle: string, accepted: boolean) {
  if (task.value) reviewWork(task.value.id, handle, accepted)
}

/** 附件能不能下：出题人、管理员、以及**已经领了这道题的人**。
 *  没领的人看得到文件名和大小（不然没法判断要不要领），但下载按钮是灰的。 */
const canDownload = computed(() => !!task.value && (canManageTask(task.value) || claimed.value))

const justDownloaded = ref<string | null>(null)

function download(f: TaskFile) {
  if (!canDownload.value) return
  f.downloads += 1
  justDownloaded.value = f.name
  window.setTimeout(() => {
    if (justDownloaded.value === f.name) justDownloaded.value = null
  }, 2200)
}

/** 只有 B 站链接能在详情页里播；其它域名存得下、播不了，这里要说清是哪一种。 */
const video = computed(() => {
  const url = task.value?.videoUrl
  if (!url) return null
  return { url, bvid: bilibiliBvid(url), embed: videoEmbedUrl(url) }
})

const claimLabel = computed(() => {
  const t = task.value
  if (!t) return ''
  if (t.state === 'PENDING') return '待审核'
  if (t.state === 'REJECTED') return '已驳回'
  if (!open.value) return '已截止'
  if (claimed.value) return '你已经领取'
  if (full.value) return '人数已满'
  return '领取这道题'
})
</script>

<template>
  <div v-if="task" class="td">
    <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" class="td__back" @click="router.back()"
      >返回</v-btn
    >

    <div class="td__grid">
      <div class="td__main">
        <PanelCard>
          <div class="td__top">
            <v-chip size="x-small" label variant="tonal" :class="`tone-${task.state.toLowerCase()}`">
              {{ task.state === 'PUBLISHED' && !open ? '已截止' : STATE_LABEL[task.state] }}
            </v-chip>
            <v-chip size="x-small" label variant="text">{{ task.category }}</v-chip>
            <!-- 从 PDF 生成的那批题会带着出处，方便出题人回头对原文件。 -->
            <v-chip v-if="task.origin" size="x-small" label variant="tonal" color="info">
              {{ task.origin }}
            </v-chip>
            <v-spacer />
            <span class="td__by">{{ task.publisher.name }} 出题</span>
          </div>
          <h1 class="td__title">{{ task.title }}</h1>
          <p class="td__summary">{{ task.summary }}</p>
          <div class="td__tags">
            <v-chip v-for="tag in task.tags" :key="tag" size="x-small" label variant="text">#{{ tag }}</v-chip>
          </div>

          <div v-if="task.state === 'REJECTED' && task.rejectReason" class="td__reject">
            <v-icon icon="mdi-alert-circle-outline" size="18" />
            <div>
              <b>被驳回：{{ task.rejectReason }}</b>
              <span v-if="canManageTask(task)" class="td__reject-hint">改完可以重新提交。</span>
            </div>
          </div>

          <!-- 讲解视频。只把 B 站链接嵌成播放器（真平台 `videoEmbedUrl` 就认这一个域名），
               其它链接存得下、播不了 —— 那就把这件事直接说出来，而不是给一个空框。 -->
          <div v-if="video" class="td__video">
            <div v-if="video.embed" class="td__player">
              <span class="td__player-badge">Bilibili 播放器</span>
              <v-icon icon="mdi-play-circle" size="54" />
              <span class="td__player-bv">{{ video.bvid }}</span>
              <span class="td__player-note">真平台这里嵌的是 player.bilibili.com 的播放器</span>
            </div>
            <div v-else class="td__video-plain">
              <v-icon icon="mdi-link-variant" size="16" />
              这道题挂了视频链接，但<b>不是 B 站链接</b>，所以放不出来：
              <span class="td__video-url">{{ video.url }}</span>
            </div>
          </div>

          <!-- 附件：拿得到材料才谈得上做。没领的人看得到清单，下载要等领取。 -->
          <div v-if="task.files?.length" class="td__files">
            <div class="td__files-head">
              <v-icon icon="mdi-paperclip" size="16" />
              <b>题目附件</b>
              <span class="td__files-count">{{ task.files.length }} 个</span>
              <v-spacer />
              <span class="td__files-rule">
                {{ canDownload ? '你可以下载' : '领取这道题之后才能下载' }}
              </span>
            </div>
            <ul class="td__files-list">
              <li v-for="f in task.files" :key="f.name">
                <v-icon :icon="FILE_ICON[f.kind]" size="18" />
                <span class="td__file-name">{{ f.name }}</span>
                <span class="td__file-size">{{ fileSize(f.size) }}</span>
                <span class="td__file-dl">{{ f.downloads }} 次下载</span>
                <v-spacer />
                <span v-if="justDownloaded === f.name" class="td__file-done">已开始下载（原型里不会真下）</span>
                <v-btn
                  size="x-small"
                  variant="tonal"
                  :disabled="!canDownload"
                  prepend-icon="mdi-download"
                  @click="download(f)"
                  >下载</v-btn
                >
              </li>
            </ul>
          </div>

          <div class="td__claim">
            <v-btn
              color="primary"
              variant="flat"
              size="large"
              :disabled="task.state !== 'PUBLISHED' || !open || claimed || full"
              :prepend-icon="claimed ? 'mdi-check' : 'mdi-hand-extended-outline'"
              @click="doClaim"
            >
              {{ claimLabel }}
            </v-btn>
            <!-- 领过之后才有「交作业」这一步；交完就等判。 -->
            <v-btn
              v-if="mine && mine.status === 'IN_PROGRESS'"
              variant="tonal"
              size="large"
              prepend-icon="mdi-upload-outline"
              @click="doSubmit"
            >
              提交作业
            </v-btn>
            <div v-if="mine" class="td__mine-state">
              你的状态：<b>{{ CLAIM_LABEL[mine.status] }}</b>
              <span v-if="mine.team"> · {{ mine.team }}</span>
              <template v-if="mine.status === 'IN_PROGRESS'"> · 做完点「提交作业」</template>
              <template v-else-if="mine.status === 'SUBMITTED'"> · 出题人判完会通知你</template>
            </div>
            <div v-else-if="full" class="td__mine-state td__mine-state--warn">
              领取人数已经到上限（{{ task.claims.length }} / {{ task.participantLimit }}），这道题不再接受新的领取。
            </div>
            <div v-else class="td__mine-state">{{ deadlineText(task) }} · 领取后你会出现在出题人的看板上。</div>
          </div>
        </PanelCard>

        <!-- 出题人/管理员才看得到名单明细。普通领取者只看到进度条。 -->
        <PanelCard v-if="canSeeRoster" title="领取者" :subtitle="`${task.claims.length} 人已领 · 判作业在这里`">
          <ul v-if="task.claims.length" class="roster">
            <li v-for="c in task.claims" :key="c.handle">
              <v-avatar size="24" class="roster__avatar">{{ c.name.slice(0, 1) }}</v-avatar>
              <span class="roster__name">{{ c.name }}</span>
              <span v-if="c.team" class="roster__team">{{ c.team }}</span>
              <v-spacer />
              <!-- 交上来的才需要判；判完的不再给按钮，免得重复点。 -->
              <template v-if="c.status === 'SUBMITTED'">
                <v-btn size="x-small" variant="text" color="error" @click="doReview(c.handle, false)">不通过</v-btn>
                <v-btn size="x-small" variant="tonal" color="success" @click="doReview(c.handle, true)">通过</v-btn>
              </template>
              <v-chip v-else size="x-small" label variant="tonal" :class="`claim-${c.status.toLowerCase()}`">
                {{ CLAIM_LABEL[c.status] }}
              </v-chip>
            </li>
          </ul>
          <v-empty-state v-else icon="mdi-account-outline" title="还没有人领取" />
        </PanelCard>
      </div>

      <aside class="td__side">
        <PanelCard title="领取进度">
          <div class="pd">
            <div class="pd__num">
              <b>{{ task.claims.length }}</b>
              <span v-if="task.participantLimit !== null"> / {{ task.participantLimit }}</span>
              <em v-else> 人（不限）</em>
            </div>
            <v-progress-linear
              v-if="task.participantLimit !== null"
              :model-value="fillPct"
              height="6"
              rounded
              class="pd__bar"
            />
          </div>
          <dl class="facts">
            <div>
              <dt>小队</dt>
              <dd>
                {{
                  task.minTeamSize === 1 && task.maxTeamSize === 1
                    ? '单人'
                    : `${task.minTeamSize}–${task.maxTeamSize} 人`
                }}
              </dd>
            </div>
            <div>
              <dt>截止</dt>
              <dd>{{ deadlineText(task) }}</dd>
            </div>
            <div>
              <dt>提交</dt>
              <dd>{{ task.submitted }} 份</dd>
            </div>
            <div>
              <dt>通过</dt>
              <dd>{{ task.passed }} 份</dd>
            </div>
          </dl>
        </PanelCard>

        <PanelCard v-if="task.claimTrend.length" title="领取走势" subtitle="最近 12 天">
          <TrendChart :labels="DAY_LABELS" :series="[{ name: '累计领取', values: task.claimTrend }]" :height="150" />
        </PanelCard>

        <PanelCard v-if="canManageTask(task)" title="出题人视角">
          <p class="side-note">这道题是你（或你可管理）的，所以你能看到上面那份领取者名单和这张走势图。</p>
          <v-btn block variant="tonal" size="small" :to="`/insights/${task.id}`" prepend-icon="mdi-chart-line">
            打开这道题的看板
          </v-btn>
        </PanelCard>
        <PanelCard v-else title="你看不到什么" dense>
          <p class="side-note">
            领取者名单、每道题的走势与完成情况，只对<b>出题人本人和管理员</b>开放。普通用户看得到人数，看不到是谁做到哪一步。
          </p>
        </PanelCard>
      </aside>
    </div>
  </div>

  <v-empty-state v-else icon="mdi-help-circle-outline" title="找不到这道题" text="它可能被删了，或者你打错了地址。" />
</template>

<style scoped lang="scss">
.td__back {
  margin-bottom: 10px;
}

.td__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

@media (max-width: 900px) {
  .td__grid {
    grid-template-columns: 1fr;
  }
}

.td__main,
.td__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.td__top {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}

.tone-published {
  color: rgb(var(--v-theme-success));
}

.tone-pending {
  color: rgb(var(--v-theme-warning));
}

.tone-rejected {
  color: rgb(var(--v-theme-error));
}

.td__by {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.78rem;
}

.td__title {
  margin: 0 0 10px;
  font-size: 1.25rem;
  font-weight: 650;
  line-height: 1.4;
}

.td__summary {
  margin: 0 0 12px;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 0.9rem;
  line-height: 1.8;
  white-space: pre-wrap;
}

.td__tags {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
}

.td__reject {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px 14px;
  margin-bottom: 16px;
  color: rgb(var(--v-theme-error));
  font-size: 0.83rem;
  line-height: 1.6;
  background: rgba(var(--v-theme-error), 0.07);
  border-radius: var(--radius-md);
}

.td__reject-hint {
  display: block;
  margin-top: 4px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.td__claim {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
  padding-top: 16px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.td__mine-state {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.79rem;
}

.td__mine-state--warn {
  color: rgb(var(--v-theme-warning));
}

.roster {
  padding: 0;
  margin: 0;
  list-style: none;
}

.roster li {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.roster li:first-child {
  border-top: none;
}

.roster__avatar {
  color: rgba(var(--v-theme-on-surface), 0.8);
  font-size: 0.72rem;
  background: rgba(var(--v-theme-on-surface), 0.1);
}

.roster__name {
  font-size: 0.85rem;
}

.roster__team {
  padding: 1px 7px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.72rem;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 999px;
}

.claim-in_progress {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.claim-submitted {
  color: rgb(var(--v-theme-warning));
}

.claim-passed {
  color: rgb(var(--v-theme-success));
}

.claim-rejected {
  color: rgb(var(--v-theme-error));
}

.pd__num {
  font-size: 1.5rem;
  font-weight: 650;
}

.pd__num b {
  font-size: 2rem;
}

.pd__num span,
.pd__num em {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.9rem;
  font-style: normal;
}

.pd__bar {
  margin-top: 10px;
}

.facts {
  margin: 16px 0 0;
}

.facts > div {
  display: flex;
  justify-content: space-between;
  padding: 7px 0;
  font-size: 0.82rem;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.facts dt {
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.facts dd {
  margin: 0;
}

.side-note {
  margin: 0 0 12px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.79rem;
  line-height: 1.7;
}

/* ---- 讲解视频 ---- */

.td__video {
  margin-top: 14px;
}

.td__player {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  justify-content: center;
  aspect-ratio: 16 / 9;
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: var(--radius-md);
}

.td__player-badge {
  position: absolute;
  top: 10px;
  left: 12px;
  padding: 2px 8px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  background: rgba(var(--v-theme-surface), 0.7);
  border-radius: var(--radius-sm);
  font-size: 0.7rem;
}

.td__player-bv {
  font-family: var(--font-mono, monospace);
  font-size: 0.78rem;
  letter-spacing: 0.02em;
}

.td__player-note {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.72rem;
}

.td__video-plain {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 10px 12px;
  color: rgba(var(--v-theme-on-surface), 0.75);
  background: rgba(var(--v-theme-warning), 0.1);
  border-radius: var(--radius-md);
  font-size: 0.78rem;
}

.td__video-url {
  overflow: hidden;
  font-family: var(--font-mono, monospace);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- 附件 ---- */

.td__files {
  padding: 12px 14px;
  margin-top: 14px;
  background: rgba(var(--v-theme-on-surface), 0.03);
  border-radius: var(--radius-md);
}

.td__files-head {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 0.84rem;
}

.td__files-count,
.td__files-rule {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.74rem;
}

.td__files-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  margin: 10px 0 0;
  list-style: none;
}

.td__files-list li {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 0;
  font-size: 0.82rem;
}

.td__file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.td__file-size,
.td__file-dl {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.74rem;
  font-variant-numeric: tabular-nums;
}

.td__file-done {
  color: rgb(var(--v-theme-success));
  font-size: 0.74rem;
}
</style>
