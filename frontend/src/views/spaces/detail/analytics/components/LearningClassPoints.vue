<template>
  <div class="lc-page">
    <div class="an-card lc-sliders">
      <div class="lc-slider">
        <span>{{ t('spaces.analytics.learning.class.points.askedRatio', { pct: pct(asked), n: threshold }) }}</span>
        <v-slider
          v-model="asked"
          :min="0"
          :max="1"
          :step="0.05"
          density="compact"
          hide-details
          color="primary"
          :aria-label="t('spaces.analytics.learning.class.points.askedRatio', { pct: pct(asked), n: threshold })"
          @end="commit"
        />
      </div>
      <div class="lc-slider">
        <span>{{ t('spaces.analytics.learning.class.points.stuckRatio', { pct: pct(stuck) }) }}</span>
        <v-slider
          v-model="stuck"
          :min="0"
          :max="1"
          :step="0.05"
          density="compact"
          hide-details
          color="primary"
          :aria-label="t('spaces.analytics.learning.class.points.stuckRatio', { pct: pct(stuck) })"
          @end="commit"
        />
      </div>
    </div>

    <p v-if="!data.points.length" class="an-note">{{ t('spaces.analytics.learning.class.points.empty') }}</p>

    <div v-else class="an-grid">
      <section class="an-card">
        <svg
          class="lc-quad"
          :viewBox="`0 0 ${W} ${H}`"
          role="img"
          :aria-label="t('spaces.analytics.learning.class.points.tableTitle')"
        >
          <line class="lc-quad__axis" :x1="L" :y1="H - B" :x2="W - R" :y2="H - B" />
          <line class="lc-quad__axis" :x1="L" :y1="T" :x2="L" :y2="H - B" />
          <line class="lc-quad__cut" :x1="x(threshold)" :y1="T" :x2="x(threshold)" :y2="H - B" />
          <line class="lc-quad__cut" :x1="L" :y1="y(stuck)" :x2="W - R" :y2="y(stuck)" />
          <text class="lc-quad__label" :x="(L + W - R) / 2" :y="H - 8" text-anchor="middle">
            {{ t('spaces.analytics.learning.class.points.chartX', { n: data.members }) }}
          </text>
          <text class="lc-quad__label" :x="L - 8" :y="T + 10" text-anchor="end">100%</text>
          <text class="lc-quad__label" :x="L - 8" :y="H - B" text-anchor="end">0</text>
          <g v-for="(point, index) in analysed" :key="point.id">
            <circle
              :cx="x(point.asked ?? 0)"
              :cy="y(point.stuckRate ?? 0)"
              r="6"
              :style="{ fill: `var(--chart-${(index % 8) + 1})` }"
            />
            <text class="lc-quad__name" :x="x(point.asked ?? 0) + 10" :y="y(point.stuckRate ?? 0) + 4">
              {{ point.name }}
            </text>
          </g>
        </svg>
      </section>

      <section class="an-card">
        <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.points.tableTitle') }}</h3>
        <table class="lc-table">
          <thead>
            <tr>
              <th>{{ t('spaces.analytics.learning.class.points.col.name') }}</th>
              <th>{{ t('spaces.analytics.learning.class.points.col.asked') }}</th>
              <th>{{ t('spaces.analytics.learning.class.points.col.stuck') }}</th>
              <th>{{ t('spaces.analytics.learning.class.points.col.rate') }}</th>
              <th>{{ t('spaces.analytics.learning.class.points.col.quadrant') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="point in data.points" :key="point.id">
              <td>
                <button type="button" class="lc-link" @click="emit('openPoint', point.id)">{{ point.name }}</button>
              </td>
              <template v-if="point.analysed">
                <td>{{ point.asked }}</td>
                <td>{{ point.stuck }}</td>
                <td>{{ pct(point.stuckRate) }}</td>
                <td>
                  <span class="lc-chip" :class="QUADRANT_TONE[point.quadrant ?? 'normal']">{{
                    t(`spaces.analytics.learning.class.points.quadrant.${point.quadrant}`)
                  }}</span>
                </td>
              </template>
              <td v-else colspan="4" class="lc-muted">{{ t('spaces.analytics.learning.class.notAnalysed') }}</td>
            </tr>
          </tbody>
        </table>
        <p class="an-note lc-gap">{{ t('spaces.analytics.learning.class.points.note') }}</p>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
// 重难点：四象限图 + 按知识点的表；两条分界在页面上调（拖完才交上去重新取数），不存。
import type { LearningClassPoints } from '@/types/apiSpaces'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ data: LearningClassPoints }>()
const emit = defineEmits<{ openPoint: [pointId: number]; ratios: [asked: number, stuck: number] }>()
const { t } = useI18n()

const asked = ref(props.data.askedRatio)
const stuck = ref(props.data.stuckRatio)
watch(
  () => [props.data.askedRatio, props.data.stuckRatio],
  ([a, s]) => {
    asked.value = a
    stuck.value = s
  }
)

const commit = () => emit('ratios', asked.value, stuck.value)

const threshold = computed(() => Math.ceil(asked.value * props.data.members))
const analysed = computed(() => props.data.points.filter((point) => point.analysed))
const QUADRANT_TONE: Record<string, string> = {
  both: 'lc-chip--danger',
  hard: 'lc-chip--warn',
  focus: 'lc-chip--ok',
  normal: '',
}
const pct = (value: number | null) => (value == null ? '—' : `${Math.round(value * 100)}%`)

const W = 520
const H = 320
const L = 48
const R = 120
const T = 12
const B = 36
const maxPeople = computed(() => Math.max(props.data.members, 1))
const x = (people: number) => L + (Math.min(people, maxPeople.value) / maxPeople.value) * (W - L - R)
const y = (rate: number) => T + (1 - rate) * (H - T - B)
</script>

<style scoped src="../analytics.css"></style>
<style scoped src="./learningClass.css"></style>

<style scoped>
.lc-sliders {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
}

.lc-slider {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.lc-quad {
  width: 100%;
  height: auto;
}

.lc-quad__axis {
  stroke: var(--line-2);
}

.lc-quad__cut {
  stroke: var(--faint);
  stroke-dasharray: 4 3;
}

.lc-quad__label {
  fill: var(--muted);
  font-size: 13px;
}

.lc-quad__name {
  fill: var(--text);
  font-size: 13px;
}
</style>
