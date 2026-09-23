<script setup lang="ts">
// 发题。这一页最大的变化是**它现在对所有人都开着** —— 今天后端在
// `may_publish_in_space` 里只放管理员过，普通用户点了会吃 403。
//
// 「领取人数」和「小队限制」两组字段原样保留（`participant_limit` / `min_team_size`
// / `max_team_size` 是真列名），只是重排成「先说要几个人，再说什么时候截止」。
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import PanelCard from '../components/PanelCard.vue'
import { CATEGORIES } from '../fixtures'
import { isManager, publishTask } from '../store'

const router = useRouter()

const title = ref('')
const summary = ref('')
const category = ref(CATEGORIES[0])
const tagText = ref('')
const limitUnlimited = ref(false)
const limit = ref(20)
const minTeam = ref(1)
const maxTeam = ref(1)
const deadlineDays = ref(14)

const tags = computed(() =>
  tagText.value
    .split(/[,，\s]+/)
    .map((t) => t.trim())
    .filter(Boolean)
    .slice(0, 5)
)

const errors = computed(() => {
  const out: string[] = []
  if (title.value.trim().length < 4) out.push('标题至少 4 个字')
  if (summary.value.trim().length < 10) out.push('题干至少 10 个字')
  if (maxTeam.value < minTeam.value) out.push('小队上限不能小于下限')
  if (!limitUnlimited.value && limit.value < 1) out.push('领取人数上限至少 1')
  return out
})

function submit() {
  if (errors.value.length) return
  const task = publishTask({
    title: title.value.trim(),
    summary: summary.value.trim(),
    category: category.value,
    tags: tags.value,
    participantLimit: limitUnlimited.value ? null : limit.value,
    minTeamSize: minTeam.value,
    maxTeamSize: maxTeam.value,
    deadlineDays: deadlineDays.value,
  })
  router.push(`/task/${task.id}`)
}
</script>

<template>
  <div class="pub">
    <div class="pub__head">
      <h1>出一道题</h1>
      <p v-if="isManager" class="pub__note">
        你是所有者/管理员，发出来的题同样先进审核队列 —— 但<b>你自己就能审</b>，不必等别人。
      </p>
      <p v-else class="pub__note">发出来的题会先进审核队列，由所有者或管理员看过之后上板。</p>
    </div>

    <div class="pub__grid">
      <div class="pub__main">
        <PanelCard title="题目内容">
          <v-text-field
            v-model="title"
            autocomplete="off"
            label="标题"
            variant="outlined"
            density="comfortable"
            counter="80"
            maxlength="80"
          />
          <v-textarea
            v-model="summary"
            autocomplete="off"
            label="题干"
            variant="outlined"
            density="comfortable"
            rows="6"
            placeholder="说清楚要做什么、交付什么、怎么算完成。"
          />
          <div class="pub__row">
            <v-select
              v-model="category"
              autocomplete="off"
              :items="CATEGORIES"
              label="分类"
              variant="outlined"
              density="comfortable"
              hide-details
            />
            <v-text-field
              v-model="tagText"
              autocomplete="off"
              label="标签"
              variant="outlined"
              density="comfortable"
              hide-details
              placeholder="逗号分隔，最多 5 个"
            />
          </div>
        </PanelCard>

        <PanelCard title="领取与小队" subtitle="这两组限制原样保留，只是排在了一起">
          <div class="pub__field">
            <div class="pub__field-label">
              <span>领取人数上限</span>
              <span class="pub__field-help">到上限后别人就点不动「领取」了。不限也可以。</span>
            </div>
            <div class="pub__field-control">
              <v-text-field
                v-model.number="limit"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                :disabled="limitUnlimited"
                style="max-width: 120px"
              />
              <v-checkbox v-model="limitUnlimited" label="不限" density="compact" hide-details />
            </div>
          </div>

          <div class="pub__field">
            <div class="pub__field-label">
              <span>小队规模</span>
              <span class="pub__field-help">都填 1 就是单人题；否则领取时要凑够下限才成组。</span>
            </div>
            <div class="pub__field-control">
              <v-text-field
                v-model.number="minTeam"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                label="下限"
                style="max-width: 110px"
              />
              <span class="pub__dash">–</span>
              <v-text-field
                v-model.number="maxTeam"
                type="number"
                variant="outlined"
                density="compact"
                hide-details
                label="上限"
                style="max-width: 110px"
              />
            </div>
          </div>

          <div class="pub__field">
            <div class="pub__field-label">
              <span>截止时间</span>
              <span class="pub__field-help">从今天算起。</span>
            </div>
            <div class="pub__field-control">
              <v-text-field
                v-model.number="deadlineDays"
                type="number"
                suffix="天后"
                variant="outlined"
                density="compact"
                hide-details
                style="max-width: 150px"
              />
            </div>
          </div>
        </PanelCard>
      </div>

      <aside class="pub__side">
        <PanelCard title="发出去之后">
          <ol class="pub__steps">
            <li><b>待审核</b> —— 题目只有你自己和管理员看得到。</li>
            <li>
              <b>有人审了</b> ——
              <template v-if="isManager">你可以直接通过（自己发的题自己审）。</template>
              <template v-else>所有者或管理员通过后就上板。</template>
            </li>
            <li><b>上板</b> —— 所有人可见可领，领取进度开始计。</li>
            <li><b>你能看到</b> —— 「我的 → 我发布的」里有这道题的领取走势、领取者名单和完成情况。</li>
          </ol>
          <p class="pub__side-note">被驳回会带原因退回，改完可以重新提交，不用重写一遍。</p>
        </PanelCard>

        <PanelCard title="提交前">
          <ul v-if="errors.length" class="pub__errors">
            <li v-for="e in errors" :key="e">{{ e }}</li>
          </ul>
          <p v-else class="pub__ok">看起来没问题。</p>
          <v-btn block color="primary" variant="flat" :disabled="errors.length > 0" @click="submit">提交审核</v-btn>
        </PanelCard>
      </aside>
    </div>
  </div>
</template>

<style scoped lang="scss">
.pub__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.pub__note {
  margin: 6px 0 18px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.84rem;
}

.pub__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

@media (max-width: 900px) {
  .pub__grid {
    grid-template-columns: 1fr;
  }
}

.pub__main,
.pub__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pub__row {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: 12px;
  margin-top: 4px;
}

.pub__field {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.pub__field-label {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.pub__field-label > span:first-child {
  font-size: 0.86rem;
}

.pub__field-help {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
}

.pub__field-control {
  display: flex;
  gap: 12px;
  align-items: center;
}

.pub__dash {
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.pub__steps {
  padding-left: 18px;
  margin: 0;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 0.83rem;
  line-height: 1.9;
}

.pub__side-note {
  margin: 12px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.76rem;
  line-height: 1.6;
}

.pub__errors {
  padding-left: 18px;
  margin: 0 0 12px;
  color: rgb(var(--v-theme-error));
  font-size: 0.8rem;
  line-height: 1.8;
}

.pub__ok {
  margin: 0 0 12px;
  color: rgb(var(--v-theme-success));
  font-size: 0.8rem;
}
</style>
