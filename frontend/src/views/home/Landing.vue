<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import LandingFilm from './LandingFilm.vue'
import LandingRoom from './LandingRoom.vue'
import LandingShell from './LandingShell.vue'

import BrandScene from '@/components/account/brandScene/BrandScene.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import i18n, { t } from '@/i18n'

// The manifesto lights up clause by clause as it scrolls through the viewport.
const manifesto = computed(() => [t('publicSite.manifesto1'), t('publicSite.manifesto2'), t('publicSite.manifesto3')])

const steps = computed(() => [
  { title: t('publicSite.memoryTitle'), body: t('publicSite.memoryBody') },
  { title: t('publicSite.agentsTitle'), body: t('publicSite.agentsBody') },
  { title: t('publicSite.previewTitle'), body: t('publicSite.previewBody') },
  { title: t('publicSite.reviewTitle'), body: t('publicSite.reviewBody') },
])

const resources = computed(() => [
  { icon: 'mdi-creation-outline', title: t('publicSite.modelsTitle'), body: t('publicSite.modelsBody') },
  { icon: 'mdi-cloud-outline', title: t('publicSite.cloudTitle'), body: t('publicSite.cloudBody') },
  { icon: 'mdi-laptop', title: t('publicSite.deviceTitle'), body: t('publicSite.deviceBody') },
])

// The visitor's own system first; the files are served by this site (lib/desktop.ts).

// The line under the slogan keeps its one sentence, and shows what 真项目 covers:
// the word lifts into a small label over its own place, the kinds of project the
// team's vision and product brief name (research, courses, company briefs,
// competitions, startups) are typed under it one after another, a character at a time behind a
// caret the way dot.net's hero did, and then the label settles back as the word.
// Every frame reads as the whole sentence, and it rests on the original. It stays
// on the original under reduced motion, and waits out a background tab.
const examples = computed(() => [
  t('publicSite.heroProject1'),
  t('publicSite.heroProject2'),
  t('publicSite.heroProject3'),
  t('publicSite.heroProject4'),
  t('publicSite.heroProject5'),
])
const TYPE_MS = 140
const ERASE_MS = 70
const HOLD_MS = 1800
const HOME_HOLD_MS = 3500
// Matches the label's transition in landing.css.
const FOLD_MS = 450
const branched = ref(false)
const typed = ref('')
// A caret blinks only while it waits; while it types or erases it stays lit.
const idle = ref(true)
const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
let typingTimer: ReturnType<typeof setTimeout> | undefined

function after(ms: number, next: () => void) {
  typingTimer = setTimeout(next, ms)
}

function rest() {
  after(HOME_HOLD_MS, branchOut)
}

function branchOut() {
  if (document.hidden) return rest()
  branched.value = true
  after(FOLD_MS, () => type(0, 1))
}

function type(index: number, length: number) {
  const example = examples.value[index]
  typed.value = example.slice(0, length)
  idle.value = length === example.length
  if (!idle.value) after(TYPE_MS, () => type(index, length + 1))
  else after(HOLD_MS, () => erase(index))
}

function erase(index: number) {
  if (document.hidden) return after(HOLD_MS, () => erase(index))
  idle.value = false
  if (typed.value.length > 1) {
    typed.value = typed.value.slice(0, -1)
    after(ERASE_MS, () => erase(index))
    return
  }
  // The last character gives way straight to the next example's first, so the
  // slot is never empty between them and the rest of the sentence holds still.
  if (index + 1 < examples.value.length) {
    after(ERASE_MS, () => type(index + 1, 1))
  } else {
    typed.value = ''
    branched.value = false
    after(FOLD_MS, rest)
  }
}

// Which step of the story is in the middle of the screen drives the room.
const step = ref(0)
const stepEls = ref<HTMLElement[]>([])
let observer: IntersectionObserver | null = null

onMounted(() => {
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) step.value = Number((entry.target as HTMLElement).dataset.step)
      }
    },
    { rootMargin: '-45% 0px -45% 0px' }
  )
  for (const el of stepEls.value) observer.observe(el)
  if (!reducedMotion) rest()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  clearTimeout(typingTimer)
})
</script>

<template>
  <LandingShell v-slot="{ entryHref, entryLabel }" page="home">
    <section class="hero">
      <div class="hero-scene">
        <BrandScene scene="neuro" />
      </div>
      <div class="hero-copy">
        <h1 class="hero-title">{{ t('publicSite.slogan') }}</h1>
        <p class="hero-position">
          <span class="visually-hidden">{{ t('publicSite.positioning') }}</span>
          <!-- The slot keeps the width of whatever stands in the sentence: 真项目 at
               rest, the typed example while branched. The label is laid over it. -->
          <span aria-hidden="true"
            >{{ t('publicSite.heroBefore')
            }}<span class="hero-slot" :class="{ 'hero-slot-branched': branched }"
              ><span class="hero-slot-label">{{ t('publicSite.heroHome') }}</span
              ><span v-if="branched && typed" class="hero-example"
                >{{ typed }}<span class="hero-caret" :class="{ 'hero-caret-idle': idle }" /></span
              ><span v-else class="hero-slot-space" :data-text="t('publicSite.heroHome')" /></span
            >{{ t('publicSite.heroAfter') }}</span
          >
        </p>
        <div class="hero-actions">
          <BaseButton :to="entryHref" kind="primary" size="lg" append-icon="mdi-arrow-top-right">
            {{ entryLabel }}
          </BaseButton>
          <router-link class="text-link" to="/solutions">
            {{ t('publicSite.solutionsLink') }}
            <v-icon icon="mdi-arrow-right" size="16" />
          </router-link>
        </div>
      </div>
    </section>

    <!-- The film is in Chinese, with no subtitles yet. -->
    <LandingFilm v-if="i18n.global.locale.value === 'zh-CN'" />

    <section class="manifesto" :aria-label="t('publicSite.manifestoLabel')">
      <p class="manifesto-text">
        <span v-for="(clause, i) in manifesto" :key="i" class="manifesto-clause">{{ clause }}</span>
      </p>
    </section>

    <section id="story" class="story">
      <div class="story-steps">
        <div
          v-for="(item, i) in steps"
          :key="i"
          ref="stepEls"
          class="story-step"
          :class="{ 'story-step-active': step === i }"
          :data-step="i"
        >
          <p class="story-index">{{ String(i + 1).padStart(2, '0') }}</p>
          <h2 class="story-title">{{ item.title }}</h2>
          <p class="story-body">{{ item.body }}</p>
        </div>
      </div>
      <div class="story-stage">
        <div class="story-room">
          <LandingRoom :step="step" />
        </div>
      </div>
    </section>

    <section class="resources">
      <div v-for="item in resources" :key="item.title" class="resources-item">
        <v-icon class="resources-icon" :icon="item.icon" size="24" />
        <h3>{{ item.title }}</h3>
        <p>{{ item.body }}</p>
      </div>
      <p class="resources-terms">{{ t('publicSite.resourceTerms') }}</p>
    </section>

    <section class="cta">
      <div class="cta-scene">
        <BrandScene scene="neuro" />
      </div>
      <div class="cta-copy">
        <h2 class="cta-title">{{ t('publicSite.ctaTitle') }}</h2>
        <p class="cta-body">{{ t('publicSite.ctaBody') }}</p>
        <div class="hero-actions">
          <BaseButton :to="entryHref" kind="primary" size="lg" append-icon="mdi-arrow-top-right">
            {{ entryLabel }}
          </BaseButton>
          <router-link class="text-link" to="/solutions">
            {{ t('publicSite.solutionsLink') }}
            <v-icon icon="mdi-arrow-right" size="16" />
          </router-link>
        </div>
      </div>
    </section>
  </LandingShell>
</template>

<style scoped src="./landing.css"></style>
