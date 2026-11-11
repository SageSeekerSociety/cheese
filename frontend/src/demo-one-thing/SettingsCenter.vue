<script setup lang="ts">
// 设置: the one place. A table of contents on the left and the section on the
// right, drawn with the product's settings rows. A project claimed from a
// 题目 says which 题目 and space it came from and which team owns it, and
// shows the rules the 题目 brought: 芝士 only hints, the deadline, the code
// rule (a team's project is edited together; anything tried apart is a
// 一件事, and 正式版受保护 makes every change one, as in the research project).
// Every project can be opened in a local editor.
import { computed, ref } from 'vue'

import { topicOf } from './course'
import { channelOf, gitUrl, isClaim, isShared, ME, NAMES, PEOPLE, peopleOf, projectMeta, SPACE } from './data'
import { S, toast } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import SettingsRow from '@/components/base/SettingsRow.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { monthDayTime } from '@/lib/dateText'

const claimed = computed(() => isClaim(S.project))
const topic = computed(() => (isClaim(S.project) ? topicOf(S.project) : null))
const url = computed(() => gitUrl(S.project))
function copyUrl() {
  void navigator.clipboard?.writeText(`git clone ${url.value}`).catch(() => undefined)
  toast('已复制 git clone 命令')
}
const chanTitle = computed(() => channelOf(S.chan)?.title ?? '')
const SECTIONS = computed(() => [
  { key: 'channel', label: `频道 · ${chanTitle.value}`, icon: 'mdi-pound' },
  { key: 'project', label: '项目', icon: 'mdi-folder-cog-outline' },
  { key: 'members', label: '成员', icon: 'mdi-account-group-outline' },
  { key: 'cheese', label: '芝士', icon: 'mdi-robot-outline' },
  { key: 'env', label: '环境', icon: 'mdi-server-outline' },
  { key: 'notify', label: '通知', icon: 'mdi-bell-outline' },
  { key: 'me', label: '我的账号', icon: 'mdi-account-outline' },
  { key: 'usage', label: '用量', icon: 'mdi-chart-box-outline' },
])
const title = computed(() => SECTIONS.value.find((s) => s.key === S.settingsSection)?.label ?? '')
const people = computed(() => [...peopleOf(S.project), ...PEOPLE.filter((p) => p.agent)])
const shared = computed(() => isShared(S.project))
const protect = computed({
  get: () => !!S.protectMain[S.project],
  set: (v: boolean) => (S.protectMain[S.project] = v),
})

const chanName = ref(chanTitle.value)
const projName = ref(projectMeta(S.project).name)
const model = ref('sonnet')
const theme = ref(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')
const ROLES: Record<string, string> = {
  linxiao: '所有者',
  wanglei: '成员',
  chensiyuan: '成员',
  zhaoyutong: '成员',
  zhouke: '成员',
  cheese: 'AI 队友',
}
const USAGE = [
  { who: '芝士 · 统一评测脚本', n: '1.8 万 token', at: '今天' },
  { who: '芝士 · 复现 BLIP 基线', n: '12.4 万 token', at: '今天' },
  { who: '芝士 · 统计 COCO 描述长度分布', n: '3.1 万 token', at: '今天' },
  { who: '芝士 · 整理组会汇报幻灯片', n: '5.6 万 token', at: '昨天' },
]
</script>

<template>
  <section class="ot-set" data-zone="center">
    <header class="ot-head">
      <h2 class="ot-head__title">设置</h2>
    </header>
    <div class="ot-set__split">
      <nav class="ot-set__toc">
        <button
          v-for="s in SECTIONS"
          :key="s.key"
          type="button"
          class="ot-set__toc-row"
          :class="{ 'is-on': S.settingsSection === s.key }"
          @click="S.settingsSection = s.key"
        >
          <v-icon size="16" :icon="s.icon" />{{ s.label }}
        </button>
      </nav>
      <div class="ot-set__pane">
        <h3 class="t-title ot-set__title">{{ title }}</h3>

        <template v-if="S.settingsSection === 'channel'">
          <SettingsRow label="频道名称" for="ot-chan-name">
            <v-text-field
              id="ot-chan-name"
              v-model="chanName"
              density="compact"
              variant="outlined"
              autocomplete="off"
              hide-details
            />
          </SettingsRow>
          <SettingsRow label="谁能进" description="项目成员都能看见、都能加入" width="select-wide">
            <SegmentedControl
              :model-value="'open'"
              :options="[
                { value: 'open', label: '项目里的人' },
                { value: 'private', label: '仅受邀的人' },
              ]"
              label="谁能进"
              @update:model-value="toast('改成仅受邀后，新加入项目的人看不到这个频道')"
            />
          </SettingsRow>
          <SettingsRow label="归档频道" description="归档后只读，消息和文件都留着" width="select">
            <BaseButton kind="secondary" size="sm" @click="toast('演示里不归档频道')">归档</BaseButton>
          </SettingsRow>
        </template>

        <template v-else-if="S.settingsSection === 'project' && claimed && topic">
          <SettingsRow label="项目名称" :description="`来自 ${SPACE.name} · ${topic.title}`" width="select">
            <span class="ot-set__value">{{ projectMeta(S.project).name }}</span>
          </SettingsRow>
          <SettingsRow
            label="所属团队"
            :description="shared ? `一起领取这道题目的团队，成员都在项目里` : '你自己的团队：个人题目领取后建在这里'"
            width="select"
          >
            <span class="ot-set__value" data-rule="team">{{ projectMeta(S.project).team }}</span>
          </SettingsRow>
          <p class="ot-set__fixed"><v-icon size="14" icon="mdi-lock-outline" />下面三条是题目带来的规则，由老师设置</p>
          <SettingsRow label="芝士在这个项目里" description="可以问思路，它不会写出答案" width="select">
            <span class="ot-set__value" data-rule="cheese">只给提示</span>
          </SettingsRow>
          <SettingsRow label="截止" description="提交在空间里的这道题目上，以截止前最后一次为准" width="select">
            <span class="ot-set__value" data-rule="due">{{ monthDayTime(Date.parse(topic.due)) }}</span>
          </SettingsRow>
          <SettingsRow label="代码" description="每次改动自动记进历史，可以撤回" width="select-wide">
            <span class="ot-set__value" data-rule="code">{{
              shared
                ? `${people.length - 1} 个人一起改正式版，改动直接生效；想单独试就开一件事`
                : '只有你一个人，改动直接生效'
            }}</span>
          </SettingsRow>
          <SettingsRow v-if="shared" label="正式版受保护" description="改动都要走一件事，正式版只读" width="select">
            <v-switch
              v-model="protect"
              data-setting="protect-main"
              color="primary"
              density="compact"
              hide-details
              inset
            />
          </SettingsRow>
          <SettingsRow
            label="用本地编辑器打开"
            description="从你自己的编辑器推上来的改动，也一样记进历史"
            width="select-wide"
          >
            <span class="ot-set__clone">
              <code>{{ url }}</code>
              <BaseButton
                kind="ghost"
                size="sm"
                icon="mdi-content-copy"
                title="复制"
                aria-label="复制"
                @click="copyUrl"
              />
            </span>
          </SettingsRow>
        </template>

        <template v-else-if="S.settingsSection === 'project'">
          <SettingsRow label="项目名称" for="ot-proj-name">
            <v-text-field
              id="ot-proj-name"
              v-model="projName"
              density="compact"
              variant="outlined"
              autocomplete="off"
              hide-details
            />
          </SettingsRow>
          <SettingsRow
            label="芝士的改动"
            description="默认在一件事里等你采纳；改成直接生效后随时可撤回"
            width="select-wide"
          >
            <SegmentedControl
              :model-value="S.settingsAgentDirect ? 'direct' : 'pending'"
              :options="[
                { value: 'pending', label: '等采纳' },
                { value: 'direct', label: '直接生效' },
              ]"
              label="芝士的改动"
              @update:model-value="S.settingsAgentDirect = $event === 'direct'"
            />
          </SettingsRow>
          <SettingsRow label="正式版受保护" description="改动都要走一件事，正式版只读" width="select">
            <v-switch
              v-model="protect"
              data-setting="protect-main"
              color="primary"
              density="compact"
              hide-details
              inset
            />
          </SettingsRow>
          <SettingsRow
            label="用本地编辑器打开"
            description="从你自己的编辑器推上来的改动，也一样记进历史"
            width="select-wide"
          >
            <span class="ot-set__clone">
              <code>{{ url }}</code>
              <BaseButton
                kind="ghost"
                size="sm"
                icon="mdi-content-copy"
                title="复制"
                aria-label="复制"
                @click="copyUrl"
              />
            </span>
          </SettingsRow>
          <SettingsRow label="所属团队" :description="projectMeta(S.project).team" width="select">
            <BaseButton kind="secondary" size="sm" @click="toast('转到另一个团队：成员和文件一起走')">转移</BaseButton>
          </SettingsRow>
        </template>

        <div v-else-if="S.settingsSection === 'members'" class="ot-set__members">
          <div v-for="p in people" :key="p.handle" class="ot-set__member">
            <CheeseAvatar v-if="p.agent" :size="28" name="芝士" handle="cheese" />
            <UserAvatar v-else :size="28" :name="p.name" :seed="p.handle" />
            <span class="ot-set__mname"
              >{{ p.name }}<span v-if="p.handle === ME" class="ot-set__you">（你）</span></span
            >
            <span class="ot-set__handle">@{{ p.handle }}</span>
            <span class="ot-head__gap" />
            <span class="ot-set__role">{{ ROLES[p.handle] }}</span>
          </div>
          <BaseButton
            kind="secondary"
            size="sm"
            prepend-icon="mdi-account-plus-outline"
            @click="toast('邀请链接已复制，7 天内有效')"
          >
            邀请成员
          </BaseButton>
        </div>

        <template v-else-if="S.settingsSection === 'cheese'">
          <SettingsRow label="默认模型" description="新开的一件事用这个模型" width="select-wide">
            <SegmentedControl
              v-model="model"
              :options="[
                { value: 'sonnet', label: '均衡' },
                { value: 'opus', label: '最强' },
                { value: 'haiku', label: '最快' },
              ]"
              label="默认模型"
            />
          </SettingsRow>
          <SettingsRow
            label="项目说明"
            description="芝士每次开工前都会读：项目的约定、数据在哪、不要动什么"
            width="select"
          >
            <BaseButton kind="secondary" size="sm" @click="toast('项目说明是 README.md 的第一节')">编辑</BaseButton>
          </SettingsRow>
        </template>

        <template v-else-if="S.settingsSection === 'env'">
          <SettingsRow label="运行环境" description="芝士跑代码的机器；数据在 /data 下" width="select-wide">
            <SegmentedControl
              v-model="S.settingsRuntime"
              :options="[
                { value: 'a100', label: 'A100 × 1' },
                { value: 'cpu', label: 'CPU' },
              ]"
              label="运行环境"
            />
          </SettingsRow>
          <SettingsRow label="Python" description="3.11 · torch 2.4 · transformers 4.44" width="select">
            <BaseButton kind="secondary" size="sm" @click="toast('依赖写在 requirements.txt，开工时自动装')"
              >查看</BaseButton
            >
          </SettingsRow>
        </template>

        <template v-else-if="S.settingsSection === 'notify'">
          <SettingsRow label="项目通知" description="频道里的消息什么时候提醒你" width="select-wide">
            <SegmentedControl
              v-model="S.settingsNotify"
              :options="[
                { value: 'all', label: '每条' },
                { value: 'mentions', label: '只在 @ 我' },
                { value: 'none', label: '不提醒' },
              ]"
              label="项目通知"
            />
          </SettingsRow>
          <SettingsRow label="等你采纳" description="一件事做完等你时，总会提醒" width="select">
            <v-switch :model-value="true" color="primary" density="compact" hide-details inset disabled />
          </SettingsRow>
        </template>

        <template v-else-if="S.settingsSection === 'me'">
          <SettingsRow label="名字" :description="`@${ME}`" width="select">
            <span class="ot-set__value"><UserAvatar :size="24" :name="NAMES[ME]" :seed="ME" />{{ NAMES[ME] }}</span>
          </SettingsRow>
          <SettingsRow label="外观" width="select-wide">
            <SegmentedControl
              v-model="theme"
              :options="[
                { value: 'light', label: '浅色' },
                { value: 'dark', label: '深色' },
              ]"
              label="外观"
              @update:model-value="toast('用页面右上角的月亮按钮切换')"
            />
          </SettingsRow>
        </template>

        <div v-else class="ot-set__usage">
          <p class="ot-set__big">本月 <b>23.7 万</b> token</p>
          <div v-for="u in USAGE" :key="u.who" class="ot-set__urow">
            <span>{{ u.who }}</span>
            <span class="ot-head__gap" />
            <span class="ot-set__handle">{{ u.at }}</span>
            <span class="ot-set__n">{{ u.n }}</span>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ot-set {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.ot-set__split {
  display: flex;
  flex: 1;
  min-height: 0;
}

.ot-set__toc {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 2px;
  width: 200px;
  padding: 12px 8px;
  border-right: 1px solid var(--line);
}

.ot-set__toc-row {
  display: flex;
  gap: 8px;
  align-items: center;
  height: 32px;
  padding: 0 12px;
  font-size: 14px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  border-radius: var(--radius-md);
}

.ot-set__toc-row .v-icon {
  color: var(--muted);
}

.ot-set__toc-row:hover {
  background: var(--fill);
}

.ot-set__toc-row.is-on {
  font-weight: 600;
  color: var(--ink);
  background: var(--fill-2);
}

.ot-set__pane {
  flex: 1;
  min-width: 0;
  padding: 24px 32px;
  overflow: auto;
}

.ot-set__title {
  margin: 0 0 12px;
  color: var(--ink);
}

.ot-set__members {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}

.ot-set__member,
.ot-set__urow {
  display: flex;
  gap: 10px;
  align-items: center;
  width: 100%;
  padding: 8px 0;
  font-size: 14px;
  color: var(--ink);
  border-bottom: 1px solid var(--line);
}

.ot-set__member:last-of-type {
  margin-bottom: 12px;
}

.ot-set__you,
.ot-set__handle,
.ot-set__role {
  font-size: 13px;
  color: var(--muted);
}

.ot-set__value {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  font-size: 14px;
  color: var(--ink);
}

.ot-set__big {
  margin: 0 0 12px;
  font-size: 14px;
  color: var(--text);
}

.ot-set__big b {
  font-size: 20px;
  color: var(--ink);
}

.ot-set__n {
  width: 96px;
  font-family: var(--font-mono);
  font-size: 13px;
  text-align: right;
}
.ot-set__fixed {
  display: flex;
  gap: 6px;
  align-items: center;
  margin: 12px 0 4px;
  font-size: 12px;
  color: var(--muted);
}

.ot-set__clone {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  min-width: 0;
}

.ot-set__clone code {
  min-width: 0;
  padding: 2px 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink);
  overflow-wrap: anywhere;
  background: var(--fill);
  border-radius: var(--radius-sm);
}
</style>
