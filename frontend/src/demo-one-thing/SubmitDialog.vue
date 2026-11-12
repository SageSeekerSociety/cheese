<script setup lang="ts">
// 提交 to a 题目, as the product's submission form asks for it
// (TaskSubmissionSchemaEntry): one field per 提交项. A FILE item freezes a
// version of the project file, with its version id: by default the file as it
// reads now in 项目文件, or an earlier one 历史 can name. A TEXT item is typed
// here. A
// team 题目 is submitted as the team that claimed it, one record for all of
// them. The dialog also says how the 题目 is graded, so 评测机评 coming back at
// once and 老师评 waiting are both expected. 提交 records it on the 题目.
import { computed, reactive, ref, watch } from 'vue'

import { GRADING_NOTE, GRADING_TEXT, SET, submitItems, topicById, UNITS } from './course'
import { NAMES, projectTeam } from './data'
import { whenText } from './shapes'
import { type FileVersion, latestOf, passed, S, submitTopic, versionsOf } from './state'

import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { monthDayTime } from '@/lib/dateText'

const topic = computed(() => (S.submitAsk ? topicById(S.submitAsk.topic) ?? null : null))
const isSet = computed(() => topic.value?.category === '题单')
/** For a 题单: which problem this submission is for. */
const unit = ref('')
const items = computed(() => (topic.value ? submitItems(topic.value, unit.value) : []))
const fileItem = computed(() => items.value.find((x) => x.type === 'FILE') ?? null)
const versions = computed<FileVersion[]>(() =>
  topic.value?.project && fileItem.value ? versionsOf(topic.value.project, fileItem.value.path!) : []
)
const pick = ref('')
const texts = reactive<Record<string, string>>({})
const team = computed(() => (topic.value?.project ? projectTeam(topic.value.project) : null))
const n = computed(() =>
  topic.value ? (S.subs[topic.value.id] ?? []).filter((x) => x.unit === unit.value).length + 1 : 1
)

watch(
  () => S.submitAsk,
  (ask) => {
    if (!ask) return
    unit.value = ask.unit
    for (const it of items.value) if (it.type === 'TEXT') texts[it.prompt] = it.draft ?? ''
  }
)
// Each file starts on the first version offered: the file as it reads now.
watch(versions, (v) => (pick.value = v[0]?.id ?? ''), { immediate: true })

const label = (v: FileVersion) =>
  v.current
    ? `现在的版本 · ${v.id}`
    : v.start
      ? `历史 · 领取时的起始代码 · ${v.id}`
      : `历史 · ${whenText(v.at)} ${NAMES[v.who]}改的 · ${v.id}`

const ready = computed(() => items.value.every((it) => it.type === 'FILE' || (texts[it.prompt] ?? '').trim()))

function submit() {
  const t = topic.value
  const v = versions.value.find((x) => x.id === pick.value)
  if (!t || !v || !ready.value) return
  submitTopic(
    t.id,
    unit.value,
    v,
    items.value.filter((it) => it.type === 'TEXT').map((it) => ({ prompt: it.prompt, text: texts[it.prompt]!.trim() }))
  )
}
</script>

<template>
  <div v-if="topic" class="ot-sub" data-zone="submit">
    <div class="ot-sub__card" role="dialog" aria-modal="true" :aria-label="`提交「${topic.title}」`">
      <h3 class="t-title ot-sub__title">提交「{{ topic.title }}」</h3>
      <div class="ot-sub__meta">
        <span class="ot-sub__mode">{{ GRADING_TEXT[topic.grading] }}</span>
        <span>{{ GRADING_NOTE[topic.grading] }} · 截止 {{ monthDayTime(Date.parse(topic.due)) }}</span>
      </div>

      <template v-if="isSet">
        <div class="ot-sub__sec">交哪一题</div>
        <div class="ot-sub__probs">
          <button
            v-for="p in SET"
            :key="p.id"
            type="button"
            class="ot-sub__prob"
            :class="{ 'is-on': unit === p.id }"
            :title="UNITS[p.id]!.title"
            :data-prob="p.id"
            @click="unit = p.id"
          >
            <v-icon v-if="passed(latestOf(topic.id, p.id))" size="13" icon="mdi-check" />{{ p.id }}
          </button>
        </div>
      </template>

      <div class="ot-sub__sec">提交项</div>
      <div v-for="it in items" :key="it.prompt" class="ot-sub__item" :data-item="it.prompt">
        <div class="ot-sub__itemhead">
          <span class="ot-sub__kind">{{ it.type === 'FILE' ? '文件' : '文字' }}</span>
          <b :class="{ 'ot-sub__path': it.type === 'FILE' }">{{ it.prompt }}</b>
        </div>
        <template v-if="it.type === 'FILE'">
          <select v-model="pick" class="ot-sub__select" :aria-label="`${it.prompt} 用哪一版`" data-version>
            <option v-for="v in versions" :key="v.id" :value="v.id">{{ label(v) }}</option>
          </select>
          <span class="ot-sub__hint"
            >项目文件里这个文件的一个版本：现在的，或者历史里的。提交后固定不变，之后再改也不影响这次提交</span
          >
        </template>
        <textarea
          v-else
          v-model="texts[it.prompt]"
          class="ot-sub__text"
          autocomplete="off"
          rows="3"
          :aria-label="it.prompt"
          :placeholder="`写${it.prompt}`"
        />
      </div>

      <div class="ot-sub__who">
        <template v-if="team && !team.personal">
          <span
            >以团队 <b>{{ team.name }}</b> 提交 · 全组一条记录</span
          >
          <span class="ot-sub__faces">
            <UserAvatar v-for="h in team.members" :key="h" :size="20" :name="NAMES[h]" :seed="h" />
          </span>
        </template>
        <span v-else
          >以 <b>{{ NAMES['linxiao'] }}</b> 个人提交</span
        >
      </div>

      <div class="ot-sub__foot">
        <span class="ot-sub__n">第 {{ n }} 次提交 · 以截止前最后一次为准</span>
        <BaseButton kind="ghost" size="sm" @click="S.submitAsk = null">取消</BaseButton>
        <BaseButton id="b-submit-confirm" kind="primary" size="sm" :disabled="!ready" @click="submit">提交</BaseButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Over the whole workspace, like the product's dialogs. */
.ot-sub {
  display: grid;
  position: absolute;
  inset: 0;
  z-index: var(--z-panel);
  place-items: center;
  background: var(--scrim, rgb(0 0 0 / 28%));
}

.ot-sub__card {
  width: 500px;
  padding: 20px;
  background: var(--surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}

.ot-sub__title {
  margin: 0 0 6px;
}

.ot-sub__meta {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-sub__mode {
  flex: none;
  padding: 0 6px;
  line-height: 18px;
  color: var(--ink);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-sub__sec {
  margin: 16px 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--faint);
}

.ot-sub__probs {
  display: flex;
  gap: 6px;
}

.ot-sub__prob {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  height: 28px;
  padding: 0 10px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
  cursor: pointer;
  background: none;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.ot-sub__prob .v-icon {
  color: var(--ok-ink);
}

.ot-sub__prob.is-on {
  color: var(--ink);
  border-color: var(--ink);
}

.ot-sub__item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.ot-sub__item + .ot-sub__item {
  margin-top: 8px;
}

.ot-sub__itemhead {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 14px;
  color: var(--ink);
}

.ot-sub__kind {
  padding: 0 6px;
  font-size: 12px;
  line-height: 18px;
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
}

.ot-sub__path {
  font-family: var(--font-mono);
  font-size: 13px;
}

.ot-sub__select,
.ot-sub__text {
  width: 100%;
  padding: 6px 8px;
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.ot-sub__select {
  height: 32px;
  font-family: var(--font-mono);
  font-size: 12px;
}

.ot-sub__text {
  resize: none;
}

.ot-sub__hint {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.ot-sub__who {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  font-size: 13px;
  color: var(--text);
}

.ot-sub__who b {
  color: var(--ink);
}

.ot-sub__faces {
  display: flex;
  gap: 2px;
}

.ot-sub__foot {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 16px;
}

.ot-sub__n {
  flex: 1;
  font-size: 12px;
  color: var(--muted);
}
</style>
