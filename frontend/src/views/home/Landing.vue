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

// The hero finishes its sentence with one concrete job after another, so a
// visitor sees what the product is for before scrolling. It holds still under
// reduced motion and in a background tab.
const jobs = computed(() => [
  t('publicSite.heroJob1'),
  t('publicSite.heroJob2'),
  t('publicSite.heroJob3'),
  t('publicSite.heroJob4'),
  t('publicSite.heroJob5'),
  t('publicSite.heroJob6'),
])
const job = ref(0)
const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
let jobTimer: ReturnType<typeof setInterval> | undefined

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
  if (!reducedMotion) {
    jobTimer = setInterval(() => {
      if (!document.hidden) job.value = (job.value + 1) % jobs.value.length
    }, 2400)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  clearInterval(jobTimer)
})
</script>

<template>
  <LandingShell v-slot="{ entryHref, entryLabel }" page="home">
    <section class="hero">
      <div class="hero-scene">
        <BrandScene scene="neuro" />
      </div>
      <div class="hero-copy">
        <h1 class="hero-title">
          <span class="hero-lead">{{ t('publicSite.heroLead') }}</span>
          <span class="visually-hidden">{{ jobs.join(' / ') }}</span>
          <!-- Every job sits in the same grid cell, the hidden copies included, so
               the line is as wide and tall as its longest job and never jumps. -->
          <span class="hero-jobs" aria-hidden="true">
            <span v-for="item in jobs" :key="item" class="hero-job-size">{{ item }}</span>
            <Transition name="hero-job">
              <span :key="job" class="hero-job-word">{{ jobs[job] }}</span>
            </Transition>
          </span>
        </h1>
        <p class="hero-position">{{ t('publicSite.positioning') }}</p>
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
