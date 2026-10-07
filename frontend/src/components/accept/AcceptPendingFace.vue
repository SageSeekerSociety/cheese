<script setup lang="ts">
// 待采纳那张脸 —— 整个框里最重的一块：这次交的是什么、卡在等谁、为什么还不能
// 采纳，以及人在这里能做的每一件事。
//
// 它只画，不知道卡是从哪来的：状态词和依据是算好的，谁能改派是从名册里挑过的，
// 点下去的每一颗按钮都只往外报一个意图（`accept` / `reject` / `void` / ...），
// 打哪个端点由 composable 决定。三个「先展开、再确认」的小表单（人工放行、退回、
// 作废）开在那一个，状态归调用方，因为重新读卡会把它们收起来。
import type { AcceptCard, MergeReason, PrChecks, ProjectMemberRow } from '@/cx_types'
import type { MergeBadge } from '@/lib/mergeState'

import AcceptNoteLine from './AcceptNoteLine.vue'
import AcceptPrChecks from './AcceptPrChecks.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { memberName } from '@/lib/agentNames'
import { columnDotStyle } from '@/lib/board'

defineProps<{
  card: AcceptCard
  /** 合并态那一行的词 + 圈；冲突卡没有（标题已经说了「芝士处理中」）。 */
  badge: MergeBadge | null
  /** 念给采纳按钮上方看的那几条依据。 */
  reasons: MergeReason[]
  forgeDeclaration: string
  note: { text: string; tone: 'error' | 'info' } | null
  reviewerChoices: ProjectMemberRow[]
  busy: boolean
  /** 按钮为什么灰（就是卡上会写的那句），空表示可以采纳。 */
  blockedTitle: string | null
  needsPr: boolean
  autoMergeVisible: boolean
  autoMergeArmedBy: string | null
  prChecks: PrChecks | null
  /** 贴在输入框上方时这一块的「审阅」在横条上，这里不再放第二颗。 */
  docked: boolean
  /** 我是谁：批准名单和「你已批准」按它算。 */
  myHandle: string
  agentName: string
  agentHandle: string | null
  deliverableBusy: boolean
  deliverableError: string
}>()

const emit = defineEmits<{
  (e: 'accept'): void
  (e: 'review'): void
  (e: 'approve'): void
  (e: 'reassign', handle: string): void
  (e: 'download'): void
  (e: 'reject'): void
  (e: 'void'): void
  (e: 'force-merge'): void
  (e: 'toggle-auto-merge', enabled: unknown): void
}>()

const showRejectInput = defineModel<boolean>('showRejectInput', { required: true })
const rejectNote = defineModel<string>('rejectNote', { required: true })
const showVoidInput = defineModel<boolean>('showVoidInput', { required: true })
const voidNote = defineModel<string>('voidNote', { required: true })
const showForceMergeInput = defineModel<boolean>('showForceMergeInput', { required: true })
const forceMergeReason = defineModel<string>('forceMergeReason', { required: true })
</script>

<template>
  <v-card variant="outlined" class="merge-box">
    <div class="pa-3">
      <div v-if="card.status === 'conflict'" class="text-caption text-medium-emphasis mb-2">
        {{ card.note || t('work.room.accept.conflictFallback') }}
        <i18n-t scope="global" keypath="work.room.accept.conflictWorking" tag="span">
          <template #agent><UserRef :handle="agentHandle" :name="agentName" /></template>
        </i18n-t>
      </div>
      <!-- 合并态 (#718): 状态词 + 「谁的活」的圈。词和 who 都是后端算好下发的，
       圈用看板「该谁动」的点语言（同一个问题在整套界面里只有一种颜色）。
       clean 画绿勾不画圈 —— 绿勾本身就是记号。 -->
      <div v-if="badge" class="d-flex align-center ga-2 text-body-2 mb-1">
        <v-icon v-if="card.merge_state.state === 'clean'" color="success" size="16">mdi-check-circle</v-icon>
        <span v-else class="board-dot" :style="columnDotStyle(badge.column)" aria-hidden="true" />
        <span>{{ badge.label }}</span>
      </div>
      <!-- 结论的依据：红了哪个检查要能看见。 -->
      <div
        v-for="(r, i) in reasons"
        :key="i"
        class="d-flex align-center flex-wrap ga-1 text-caption text-medium-emphasis mb-1"
      >
        <span>{{ r.detail }}</span>
        <code v-for="chk in r.checks" :key="chk" class="text-caption">{{ chk }}</code>
      </div>
      <!-- 托管方自己的一句话，在人点采纳之前就在卡上（I23）：这次采纳会落到
       哪里、有没有外部检查。不是采纳之后补写的一条 note。 -->
      <div v-if="forgeDeclaration" class="text-caption text-medium-emphasis mb-2">
        {{ forgeDeclaration }}
      </div>
      <!-- 后端写在卡上的 note（比如「PR 有新提交，之前看到的版本已过时」）。 -->
      <AcceptNoteLine v-if="note" :text="note.text" :tone="note.tone" />
      <div class="d-flex align-center flex-wrap ga-1 text-body-2 mb-1">
        <i18n-t scope="global" keypath="work.room.accept.waitingOnWho" tag="span">
          <template #who><UserRef :handle="card.reviewer_handle" /></template>
        </i18n-t>
        <!-- 改验收人 (spec §4.4): 任何成员都可以改推荐/加人 -->
        <v-menu>
          <template #activator="{ props: menuProps }">
            <BaseButton v-bind="menuProps" kind="ghost" size="sm" density="comfortable" :disabled="busy">
              {{ t('work.room.accept.reassign') }}
            </BaseButton>
          </template>
          <v-list density="compact">
            <v-list-subheader>{{ t('work.room.accept.reassignTitle') }}</v-list-subheader>
            <v-list-item
              v-for="mbr in reviewerChoices"
              :key="mbr.user_handle"
              :active="mbr.user_handle === card.reviewer_handle"
              @click="emit('reassign', mbr.user_handle)"
            >
              <v-list-item-title class="text-body-2">
                {{ memberName(mbr) || mbr.user_handle }}
                <span v-if="memberName(mbr)" class="text-medium-emphasis">{{ mbr.user_handle }}</span>
              </v-list-item-title>
              <v-list-item-subtitle class="text-caption">
                {{ mbr.role }}
              </v-list-item-subtitle>
            </v-list-item>
            <v-list-item v-if="reviewerChoices.length === 0">
              <v-list-item-title class="text-caption text-medium-emphasis">
                {{ t('work.room.accept.noMembers') }}
              </v-list-item-title>
            </v-list-item>
          </v-list>
        </v-menu>
      </div>
      <div v-if="card.routing_reason" class="text-caption text-medium-emphasis mb-3">
        {{ t('work.room.accept.routingReason', { reason: card.routing_reason }) }}
      </div>
      <!--
        这次交付定的是哪一项产物的哪一版，以及交出去的那一份东西 (#1085 结论
        三/五)。它在提交标题上面，因为点采纳定的首先是这件事：这一版要不要成为
        《报告》的当前版本、交出去的是不是这一份文件。提交标题是它被记进历史时
        的写法，不是它本身。
        三种交法各有各的落点：文件能当场拿走（快照在递卡那一刻就落好了），地址
        能当场打开，而一次合并没有可拿的东西——那时候只写产物和版本，不补一句
        「交出去的是这次合并」凑格式。
        版本号是后端按卡的状态算的，这里一个都不推。落地之前递的那些卡两样都没
        有，整块就不出现。
      -->
      <div v-if="card.artifact || card.deliverable" class="mb-3">
        <div class="text-caption text-medium-emphasis">{{ t('work.room.accept.delivery') }}</div>
        <div class="d-flex align-center flex-wrap ga-2">
          <span v-if="card.artifact" class="text-body-2">
            {{ t('work.room.accept.artifact', { name: card.artifact.name, version: card.artifact.version }) }}
          </span>
          <template v-if="card.deliverable?.kind === 'file' && card.deliverable.filename">
            <span class="text-medium-emphasis">·</span>
            <code class="text-caption">{{ card.deliverable.filename }}</code>
            <BaseButton
              kind="secondary"
              size="sm"
              density="comfortable"
              prepend-icon="mdi-tray-arrow-down"
              :loading="deliverableBusy"
              @click="emit('download')"
            >
              {{ t('work.room.accept.download') }}
            </BaseButton>
          </template>
          <template v-else-if="card.deliverable?.kind === 'link' && card.deliverable.url">
            <span class="text-medium-emphasis">·</span>
            <a class="text-caption" :href="card.deliverable.url" target="_blank" rel="noopener">
              {{ card.deliverable.url }}
            </a>
          </template>
        </div>
        <div v-if="deliverableError" role="alert" class="text-caption text-error mt-1">
          {{ deliverableError }}
        </div>
      </div>
      <!--
        提交与 PR 规范: 采纳会把整个分支压成一个提交，标题就是这一行。
        采纳前是最后一次能反对它的机会，所以它必须在按钮上方可见，而不是
        等它进了 git 历史才有人发现写的是话题标题。
      -->
      <div v-if="card.change_subject" class="mb-3">
        <div class="text-caption text-medium-emphasis">{{ t('work.room.accept.changeSubject') }}</div>
        <code class="text-caption">{{ card.change_subject }}</code>
      </div>
      <!--
        机器闸门 (eval C2, 已退役) 的历史读数。`gate_passed_at` 只由
        `AcceptService.finish_gate` 写，而 采纳即合并 (#296, stage 1) 之后再没有
        任何东西调用它——所以今天递的卡这一格永远是空的，它出现就意味着这张卡是
        退役之前递的。留着，是因为那次检查当年真的跑过：抹掉等于把「这张卡当年
        过了平台检查」这个事实从界面上删掉。
        绝不画成绿勾：当年跑的是项目自己配的 check_command，不是完整 CI，让一个
        绿勾替它背书正是这一格要避免的事。今天的检查是 PR 上的 GitHub Actions，
        平台不会先替你跑一遍。
      -->
      <div v-if="card.gate_passed_at" class="d-flex align-center ga-1 text-caption text-medium-emphasis mb-2">
        <v-icon size="15">mdi-timer-sand</v-icon>
        {{ t('work.room.accept.gatePassed') }}
      </div>
      <!-- 采纳 PR 化 (#188 §5.1): the real PR + its CI, live. -->
      <AcceptPrChecks v-if="card.pr_url" :card="card" :checks="prChecks" class="mb-2" />
      <!-- 主分支保护 (spec §4.4): N 人批准后采纳才会真正合入。 -->
      <div v-if="card.approvals_required > 1" class="d-flex align-center flex-wrap ga-2 mb-3">
        <v-chip
          size="small"
          variant="tonal"
          :color="card.approvals.length >= card.approvals_required ? 'success' : undefined"
          prepend-icon="mdi-account-check-outline"
        >
          {{ t('work.room.accept.approvedCount', { done: card.approvals.length, total: card.approvals_required }) }}
        </v-chip>
        <span v-if="card.approvals.length" class="text-caption text-medium-emphasis">
          <template v-for="(h, i) in card.approvals" :key="h"
            >{{ i ? t('work.room.roster.listSeparator') : '' }}<UserRef :handle="h"
          /></template>
        </span>
        <BaseButton
          v-if="!card.approvals.includes(myHandle)"
          kind="secondary"
          size="sm"
          :disabled="busy"
          prepend-icon="mdi-thumb-up-outline"
          @click="emit('approve')"
        >
          {{ t('work.room.accept.approve') }}
        </BaseButton>
        <span v-else class="d-inline-flex align-center ga-1 text-caption text-medium-emphasis">
          <v-icon size="14">mdi-check</v-icon>{{ t('work.room.accept.youApproved') }}
        </span>
      </div>
      <div class="d-flex align-center flex-wrap ga-2">
        <!-- 决策在聊天，审查在面板。贴底的时候这一颗在横条上，这里不再放一颗。 -->
        <BaseButton v-if="!docked" kind="secondary" prepend-icon="mdi-file-search-outline" @click="emit('review')">
          {{ t('work.room.accept.review') }}
        </BaseButton>
        <!-- 采纳 = 当场合并 (#718)：GitHub lane 亮在后端会合的那两档（clean /
          unstable），为什么灰写在 title 里；平台 lane 的采纳纯是人的判断，
          从不按状态灰。 -->
        <span :title="blockedTitle ?? undefined">
          <BaseButton
            kind="primary"
            :loading="busy"
            :disabled="busy || !!blockedTitle"
            prepend-icon="mdi-check"
            @click="emit('accept')"
          >
            {{
              needsPr
                ? t('work.room.accept.createPr')
                : card.status === 'conflict'
                  ? t('work.room.accept.retryAccept')
                  : card.completes_task === false
                    ? t('work.room.accept.acceptAction')
                    : t('work.room.accept.acceptAndComplete')
            }}
          </BaseButton>
        </span>
        <BaseButton kind="ghost" :disabled="busy" prepend-icon="mdi-undo" @click="showRejectInput = !showRejectInput">
          {{ t('work.room.accept.sendBack') }}
        </BaseButton>
        <BaseButton
          kind="ghost"
          :disabled="busy"
          prepend-icon="mdi-close-circle-outline"
          @click="showVoidInput = !showVoidInput"
        >
          {{ t('work.room.accept.void') }}
        </BaseButton>
      </div>
      <!-- 绿了自动合 (#718)：项目允许、规则还没满足时才有；布防人由后端认定。 -->
      <div v-if="autoMergeVisible" class="d-flex align-center flex-wrap ga-2 mt-2">
        <v-switch
          :model-value="!!autoMergeArmedBy"
          color="success"
          density="compact"
          hide-details
          :disabled="busy"
          :label="t('work.room.accept.autoMerge')"
          @update:model-value="emit('toggle-auto-merge', $event)"
        />
        <span v-if="autoMergeArmedBy" class="text-caption text-medium-emphasis">
          <i18n-t scope="global" keypath="work.room.accept.autoMergeArmedBy" tag="span">
            <template #who><UserRef :handle="autoMergeArmedBy" /></template>
          </i18n-t>
        </span>
      </div>
      <!--
        人工放行 (#718)：明知合并态不是 clean 仍合并。平台自己永远不走这条路——
        红着合有时候是对的（CI 抽风、与本次改动无关的既有失败），不能接受的是
        没有人做过这个决定。所以它默认收起、要填理由，点下去在卡上留名。
      -->
      <div v-if="card.pr_number && blockedTitle && !reasons.some((r) => r.kind === 'dependency')" class="mt-2">
        <BaseButton
          v-if="!showForceMergeInput"
          kind="ghost"
          size="sm"
          prepend-icon="mdi-alert-decagram-outline"
          @click="showForceMergeInput = true"
        >
          {{ t('work.room.accept.forceMerge') }}
        </BaseButton>
        <template v-else>
          <div class="text-caption text-medium-emphasis mb-1">
            {{ t('work.room.accept.forceMergeWarning') }}
          </div>
          <v-textarea
            v-model="forceMergeReason"
            autocomplete="off"
            :label="t('work.room.accept.forceMergeReason')"
            rows="2"
            auto-grow
            density="compact"
            variant="outlined"
            hide-details
            class="mb-2"
          />
          <div class="d-flex ga-2">
            <BaseButton kind="danger" solid size="sm" :loading="busy" :disabled="busy" @click="emit('force-merge')">
              {{ t('work.room.accept.confirmForceMerge') }}
            </BaseButton>
            <BaseButton kind="ghost" size="sm" :disabled="busy" @click="showForceMergeInput = false">
              {{ t('work.room.accept.cancel') }}
            </BaseButton>
          </div>
        </template>
      </div>
      <div v-if="showRejectInput" class="d-flex align-end ga-2 mt-3">
        <v-text-field
          v-model="rejectNote"
          autocomplete="off"
          variant="outlined"
          density="compact"
          hide-details
          :placeholder="t('work.room.accept.sendBackReason')"
          class="flex-grow-1"
        />
        <BaseButton kind="secondary" :loading="busy" @click="emit('reject')">
          {{ t('work.room.accept.confirmSendBack') }}
        </BaseButton>
      </div>
      <div v-if="showVoidInput" class="mt-3">
        <div class="text-caption text-medium-emphasis mb-1">
          {{ t('work.room.accept.voidHint') }}
        </div>
        <div class="d-flex align-end ga-2">
          <v-text-field
            v-model="voidNote"
            autocomplete="off"
            variant="outlined"
            density="compact"
            hide-details
            :placeholder="t('work.room.accept.voidReason')"
            class="flex-grow-1"
          />
          <BaseButton kind="danger" solid :loading="busy" @click="emit('void')">
            {{ t('work.room.accept.confirmVoid') }}
          </BaseButton>
        </div>
      </div>
    </div>
  </v-card>
</template>

<style scoped>
/* 「谁的活」的圈。形状和颜色都由 `lib/board.ts` 一处给出（内联样式），这里只管
   尺寸 —— scoped 样式进不了别的组件，颜色写在这儿就意味着卡和看板各有一份。 */
.board-dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid var(--faint);
}
</style>

<style scoped src="./accept-card.css"></style>
