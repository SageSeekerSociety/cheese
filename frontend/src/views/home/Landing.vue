<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import LandingSpark from '@/assets/landing-spark.svg?component'
import logo from '@/assets/logo-plain.svg?url'
import LanguageToggle from '@/components/common/LanguageToggle.vue'
import HomepageMessage from '@/components/home/HomepageMessage.vue'
import i18n, { t } from '@/i18n'
import AccountService from '@/services/account'

const router = useRouter()
const route = useRoute()
const loggedIn = computed(() => AccountService.loggedIn)
const entryHref = computed(() => (loggedIn.value ? '/' : '/account/signin'))
const entryLabel = computed(() => (loggedIn.value ? t('website.openWorkspace') : t('website.getStarted')))
const stages = computed(() => [
  { id: 'discuss', label: t('website.discuss') },
  { id: 'build', label: t('website.build') },
  { id: 'verify', label: t('website.test') },
  { id: 'review', label: t('website.deliver') },
])
const stage = ref('discuss')
const expanded = ref(false)
const sampleAnswer = ref(false)
const audiences = computed(() => [
  {
    id: 'enterprise',
    label: t('website.forBusinesses'),
    title: t('website.bringAiIntoYourTeamsProjects'),
    description: t('website.equipYourTeamWithModelsAndCloud'),
    tags: [
      t('website.modelsAndCloudResources'),
      t('website.workTogetherFromStartToFinish'),
      t('website.resourcesOnDemand'),
    ],
    steps: [
      t('website.defineTheBusinessProblem'),
      t('website.workOnItWithYourTeamAnd'),
      t('website.evaluateTheResultsTogether'),
    ],
  },
  {
    id: 'institution',
    label: t('website.universitiesAndInstitutions'),
    title: t('website.learnThroughRealProjects'),
    description: t('website.weAimToSupportFutureLearningCenters'),
    tags: [t('website.projectbasedLearning'), t('website.aiAssistance'), t('website.collaborationAcrossTeams')],
    steps: [
      t('website.organizeAProject'),
      t('website.bringStudentsEducatorsAndAiTogether'),
      t('website.keepARecordOfTheWorkAnd'),
    ],
  },
  {
    id: 'research',
    label: t('website.researchTeams'),
    title: t('website.keepTheDiscussionGoingAndTheResearch'),
    description: t('website.discussResearchQuestionsReviewSourcesAndDevelop'),
    tags: [t('website.researchDiscussions'), t('website.literatureAndCode'), t('website.reviewResults')],
    steps: [
      t('website.startWithAQuestion'),
      t('website.exploreApproachesWithYourTeamAndAi'),
      t('website.reviewWhatYouHaveBuilt'),
    ],
  },
])
const audienceId = ref('enterprise')
const audience = computed(() => audiences.value.find((item) => item.id === audienceId.value)!)
function selectStage(id: string) {
  stage.value = id
  expanded.value = false
  sampleAnswer.value = false
}
function moveTab(event: KeyboardEvent, group: 'stage' | 'audience') {
  const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End']
  if (!keys.includes(event.key)) return
  const buttons = Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="tab"]'))
  const index = buttons.indexOf(event.target as HTMLButtonElement)
  if (index === -1) return
  event.preventDefault()
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? buttons.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
  if (group === 'stage') selectStage(stages.value[next].id)
  else audienceId.value = audiences.value[next].id
  buttons[next].focus()
}
// Session restoration redirects only the root; /about stays public after sign-in.
watch([loggedIn, () => route.name], ([isLoggedIn, routeName]) => {
  if (isLoggedIn && routeName === 'HomeDefault') void router.replace({ name: 'HomeSpaces' })
})
</script>

<template>
  <main id="top" class="landing-page" :lang="i18n.global.locale.value">
    <a class="skip-link" href="#experience"> {{ t('website.skipToProductDemo') }} </a>
    <header class="site-header wrap">
      <a class="brand" href="#top" :aria-label="t('website.cheeseHome')">
        <span class="cheese-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span>
          cheese<span v-if="i18n.global.locale.value === 'zh-CN'" class="brand-cn">{{ t('website.cheese') }}</span>
        </span>
      </a>
      <nav :aria-label="t('website.mainNavigation')">
        <a href="#experience">{{ t('website.productDemo') }}</a>
        <a href="#content">{{ t('website.createTogether') }}</a>
        <a href="#teams">{{ t('website.forBusinesses') }}</a>
        <a href="#vision">{{ t('website.ourVision') }}</a>
      </nav>
      <div class="header-actions">
        <LanguageToggle />
        <a class="nav-entry" :href="entryHref">
          {{ entryLabel }} <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="17" />
        </a>
      </div>
    </header>
    <section class="hero wrap">
      <div class="hero-copy">
        <p class="eyebrow"><span class="amber-line" /> {{ t('website.workTogetherFromIdeaToDelivery') }}</p>
        <h1>
          {{ t('website.takeYourIdea') }} <br />
          {{ t('website.makeIt') }}
          <span class="highlight-word">
            {{ t('website.happen')
            }}<LandingSpark class="landing-spark highlight-spark" aria-hidden="true" focusable="false" />
          </span>
          {{ t('website.sentenceEnd') }}
        </h1>
        <p class="hero-description">
          {{ t('website.discussCreateTestAndDeliverWithYour') }} <br class="desktop-break" />
          {{ t('website.yourModelsCloudEnvironmentsAndCollaborationIn') }} <br class="desktop-break" />
          {{ t('website.soYouCanFocusOnTheProject') }}
        </p>
        <div class="hero-actions">
          <a class="button button-dark" :href="entryHref">
            {{ entryLabel }}
            <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="18" />
          </a>
          <a class="watch-link" href="#teams">
            {{ t('website.forBusinessesAndUniversities') }}
            <v-icon class="landing-icon" icon="mdi-arrow-right" size="16" />
          </a>
        </div>
        <p class="free-entry">{{ t('website.startCollaboratingWithASelectionOfFree') }}</p>
        <div class="hero-audience">
          {{ t('website.universitiesAndInstitutions') }} <span>·</span> {{ t('website.businessPartnerships') }}
          <span>·</span> {{ t('website.researchAndContentTeams') }}
        </div>
      </div>
      <figure class="collaboration-map" :aria-label="t('website.peopleAiTeammatesKnowledgeAndResultsConnected')">
        <div class="map-grid" />
        <span class="map-coordinate">A SHARED SPACE FOR GREAT IDEAS</span>
        <svg viewBox="0 0 560 450" class="map-lines" aria-hidden="true">
          <circle class="orbit" cx="280" cy="224" r="155" />
          <circle class="orbit orbit-inner" cx="280" cy="224" r="105" />
          <path
            d="M100 115 C190 115 170 224 280 224 S420 320 480 320 M420 80 C350 80 380 224 280 224 S150 360 110 360"
          />
          <path class="signal-line" d="M100 115 C190 115 170 224 280 224 S420 320 480 320" />
          <path class="signal-line signal-second" d="M420 80 C350 80 380 224 280 224 S150 360 110 360" />
        </svg>
        <div class="map-core">
          <span class="cheese-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
          <strong>{{ t('website.oneProject') }}</strong>
          <span>{{ t('website.sharedContext') }}</span>
        </div>
        <div class="map-node node-team">
          <v-icon class="landing-icon" icon="mdi-account-group-outline" size="20" />
          <div>
            <strong>{{ t('website.yourTeam') }}</strong>
            <span>{{ t('website.askQuestionsMakeDecisions') }}</span>
          </div>
        </div>
        <div class="map-node node-ai">
          <v-icon class="landing-icon" icon="mdi-creation" size="21" />
          <div>
            <strong>{{ t('website.aiTeammate') }}</strong>
            <span>{{ t('website.discussIdeasMoveWorkForward') }}</span>
          </div>
          <span class="live-dot" />
        </div>
        <div class="map-node node-knowledge">
          <v-icon class="landing-icon" icon="mdi-file-document-outline" size="20" />
          <div>
            <strong>{{ t('website.projectKnowledge') }}</strong>
            <span>{{ t('website.keepDiscussionsAndDecisions') }}</span>
          </div>
        </div>
        <div class="map-node node-output">
          <v-icon class="landing-icon" icon="mdi-code-tags" size="20" />
          <div>
            <strong>{{ t('website.sharedResults') }}</strong>
            <span>{{ t('website.traceIdeasThroughToResults') }}</span>
          </div>
        </div>
        <div class="map-foot"><span>+</span> {{ t('website.humanJudgmentAiExecution') }} <span>+</span></div>
      </figure>
    </section>
    <section id="experience" class="demo-section wrap">
      <div class="section-intro">
        <div>
          <p class="eyebrow">{{ t('website.workingTogether') }}</p>
          <h2>
            {{ t('website.workTogether') }} <br />
            {{ t('website.deliverSomethingReal') }}
          </h2>
          <p class="demo-tagline">{{ t('website.yourTeamWithAnAiTeammateThat') }}</p>
        </div>
        <p>
          {{ t('website.connectPlanningTasksTestingAndResultsIn') }} <br />
          <span class="muted">{{ t('website.interactiveDemoSampleContent') }}</span>
        </p>
      </div>
      <div class="demo-tabs">
        <div
          class="stage-list"
          role="tablist"
          :aria-label="t('website.projectStages')"
          @keydown="moveTab($event, 'stage')"
        >
          <button
            v-for="(item, index) in stages"
            :id="`stage-${item.id}`"
            :key="item.id"
            type="button"
            role="tab"
            :aria-selected="stage === item.id"
            :tabindex="stage === item.id ? 0 : -1"
            :aria-controls="`panel-${item.id}`"
            :data-active="stage === item.id || undefined"
            @click="selectStage(item.id)"
          >
            <span>0{{ index + 1 }}</span
            >{{ item.label }}
          </button>
        </div>
        <div class="workspace">
          <aside class="workspace-sidebar">
            <div class="workspace-name">
              <v-icon class="landing-icon" icon="mdi-layers-outline" size="19" /> {{ t('website.knowledgeSearch') }}
            </div>
            <div class="sidebar-link">
              <v-icon class="landing-icon" icon="mdi-compass-outline" size="16" /> {{ t('website.overview') }}
            </div>
            <div class="sidebar-link active">
              <v-icon class="landing-icon" icon="mdi-message-outline" size="16" />
              {{ t('website.planningAndPrototype') }}
            </div>
            <div class="sidebar-link">
              <v-icon class="landing-icon" icon="mdi-file-document-outline" size="16" /> {{ t('website.projectNotes') }}
            </div>
            <div class="sidebar-caption">{{ t('website.members') }}</div>
            <div class="member">
              <span class="avatar">{{ t('website.b') }}</span> {{ t('website.businessPartner') }}
            </div>
            <div class="member">
              <span class="avatar">{{ t('website.r') }}</span> {{ t('website.researcher') }}
            </div>
            <div class="member">
              <span class="avatar ai-avatar">
                <v-icon class="landing-icon" icon="mdi-creation" size="16" />
              </span>
              {{ t('website.cheese') }} <small>{{ t('website.aiTeammate') }}</small>
            </div>
            <div class="sidebar-bottom">
              <v-icon class="landing-icon" icon="mdi-source-branch" size="15" />
              {{ t('website.keepTheDiscussionWithTheWork') }}
            </div>
          </aside>
          <div class="workspace-main">
            <div class="workspace-bar">
              <span>
                <span class="hash">#</span> {{ t('website.planningAndPrototype') }}
                <v-icon class="landing-icon" icon="mdi-chevron-right" size="14" />
              </span>
              <span class="member-dots">
                <span>{{ t('website.b') }}</span>
                <span>{{ t('website.r') }}</span>
                <span><LandingSpark class="landing-spark" aria-hidden="true" focusable="false" /></span>
              </span>
            </div>
            <div
              v-if="stage === 'discuss'"
              id="panel-discuss"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-discuss"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage :who="t('website.businessPartner')">
                  <p>{{ t('website.canAKnowledgeBaseHelpNewTeam') }}</p>
                </HomepageMessage>
                <HomepageMessage :who="t('website.researcher')">
                  <p>{{ t('website.letsStartWithASmallPrototypeAnswers') }}</p>
                </HomepageMessage>
                <HomepageMessage :who="t('website.cheese')" ai>
                  <p>{{ t('website.illOutlineTheEvaluationMethodThenBuild') }}</p>
                  <div class="inline-note">
                    <v-icon class="landing-icon" icon="mdi-file-document-outline" size="15" />
                    {{ t('website.projectNotesFollowTheWork') }}
                  </div>
                </HomepageMessage>
                <div class="demo-composer">
                  <span>{{ t('website.continueTheConversationInYourProject') }}</span>
                  <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="18" />
                </div>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">
                  <v-icon class="landing-icon" icon="mdi-file-document-outline" size="15" />
                  {{ t('website.projectNotes') }}
                </div>
                <h3>{{ t('website.knowledgeSearchPrototype') }}</h3>
                <p class="doc-label">{{ t('website.currentGoal') }}</p>
                <p>{{ t('website.testASearchPrototypeThatCitesIts') }}</p>
                <p class="doc-label">{{ t('website.agreedSoFar') }}</p>
                <p>
                  <v-icon class="landing-icon" icon="mdi-check" size="14" />
                  {{ t('website.testTheApproachBeforeExpandingIt') }}
                </p>
                <p class="doc-label">{{ t('website.nextStep') }}</p>
                <p>{{ t('website.agreeOnEvaluationExamples') }}</p>
                <div class="doc-bottom">{{ t('website.keepTrackOfWhatTheTeamAgrees') }}</div>
              </aside>
            </div>
            <div
              v-if="stage === 'build'"
              id="panel-build"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-build"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage :who="t('website.cheese')" ai>
                  <p>{{ t('website.theSearchPrototypeAndEvaluationPlanAre') }}</p>
                </HomepageMessage>
                <div class="task-rows">
                  <div>
                    <v-icon class="landing-icon" icon="mdi-code-tags" size="19" />
                    <span>{{ t('website.searchPrototype') }}</span>
                    <small>{{ t('website.inProgress') }}</small>
                  </div>
                  <div>
                    <v-icon class="landing-icon" icon="mdi-message-outline" size="19" />
                    <span>{{ t('website.evaluationPlan') }}</span>
                    <small>{{ t('website.toDiscuss') }}</small>
                  </div>
                  <div>
                    <v-icon class="landing-icon" icon="mdi-file-document-outline" size="19" />
                    <span>{{ t('website.sourceReview') }}</span>
                    <small>
                      <v-icon class="landing-icon" icon="mdi-check" size="13" /> {{ t('website.complete') }}
                    </small>
                  </div>
                </div>
                <p class="demo-note">{{ t('website.theseAreSampleTasksTheyDoNot') }}</p>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">
                  <v-icon class="landing-icon" icon="mdi-source-branch" size="15" /> {{ t('website.taskProgress') }}
                </div>
                <h3>{{ t('website.separateTasksSharedProgress') }}</h3>
                <p>{{ t('website.discussThePrototypeAndEvaluationSeparatelyThen') }}</p>
                <p class="doc-label">{{ t('website.nextForTheTeam') }}</p>
                <p>{{ t('website.agreeOnWhatThePrototypeShouldTest') }}</p>
              </aside>
            </div>
            <div
              v-if="stage === 'verify'"
              id="panel-verify"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-verify"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage :who="t('website.cheese')" ai
                  ><p>{{ t('website.thePrototypeIsReadyReviewTheResults') }}</p></HomepageMessage
                >
                <div class="prototype-preview">
                  <span class="eyebrow">{{ t('website.searchPrototypeInteractiveExample') }}</span>
                  <h3>{{ t('website.whereShouldANewTeamMemberStart') }}</h3>
                  <button
                    type="button"
                    class="button button-dark"
                    :aria-expanded="sampleAnswer"
                    @click="sampleAnswer = !sampleAnswer"
                  >
                    {{ sampleAnswer ? t('website.hideSampleAnswer') : t('website.showSampleAnswer')
                    }}<v-icon class="landing-icon" icon="mdi-arrow-top-right" size="16" />
                  </button>
                  <p v-if="sampleAnswer">
                    {{ t('website.readTheProjectOverviewThenCheckThe')
                    }}<span class="sample-citation">{{
                      t('website.sampleSourceProjectGettingStartedGuideSection')
                    }}</span>
                  </p>
                </div>
                <p class="demo-note">{{ t('website.thisSamplePrototypeDoesNotAccessReal') }}</p>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">{{ t('website.runAndPreviewInTheCloud') }}</div>
                <h3>{{ t('website.reviewTheSameResultTogether') }}</h3>
                <p>{{ t('website.theTeamReviewsThePrototypeAiUpdates') }}</p>
                <p class="doc-label">{{ t('website.nextStep') }}</p>
                <p>{{ t('website.confirmTheResultsAndSubmitThemFor') }}</p>
              </aside>
            </div>
            <div
              v-if="stage === 'review'"
              id="panel-review"
              class="demo-content"
              role="tabpanel"
              aria-labelledby="stage-review"
              tabindex="0"
            >
              <div class="chat-column">
                <HomepageMessage :who="t('website.cheese')" ai>
                  <p>{{ t('website.thePrototypeIsReadyForReviewThe') }}</p>
                </HomepageMessage>
                <div class="review-card">
                  <span class="eyebrow">{{ t('website.readyForTeamReview') }}</span>
                  <h3>{{ t('website.searchPrototypeAndEvaluationNotes') }}</h3>
                  <p>{{ t('website.inCollaborationModePeopleReviewAndAccept') }}</p>
                  <button class="button button-dark" :aria-expanded="expanded" @click="expanded = !expanded">
                    {{ expanded ? t('website.hideSampleResult') : t('website.showSampleResult') }}
                    <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="16" />
                  </button>

                  <p v-if="expanded" class="expanded-result">
                    {{ t('website.thePrototypeShowsSourceCitationsTheEvaluation') }}
                  </p>
                </div>
              </div>
              <aside class="project-doc">
                <div class="doc-caption">
                  <v-icon class="landing-icon" icon="mdi-file-document-outline" size="15" />
                  {{ t('website.deliveryNotes') }}
                </div>
                <h3>{{ t('website.seeTheReasoningBehindTheWork') }}</h3>
                <p>{{ t('website.seeTheResultAlongsideTheDiscussionsAnd') }}</p>
                <p class="doc-label">{{ t('website.nextStep') }}</p>
                <p>{{ t('website.reviewTogetherThenPlanTheNextRound') }}</p>
              </aside>
            </div>
          </div>
        </div>
      </div>
      <div class="capabilities">
        <article>
          <v-icon class="landing-icon" icon="mdi-account-group-outline" />
          <h3>{{ t('website.keepContextInOnePlace') }}</h3>
          <p>{{ t('website.keepDiscussionsDocumentsAndResultsTogetherNew') }}</p>
        </article>
        <article>
          <v-icon class="landing-icon" icon="mdi-creation" />
          <h3>{{ t('website.putTheDiscussionIntoAction') }}</h3>
          <p>{{ t('website.reviewSourcesWriteCodeAndReportsAnd') }}</p>
        </article>
        <article>
          <v-icon class="landing-icon" icon="mdi-source-branch" />
          <h3>{{ t('website.reviewTheWorkBeforeDeciding') }}</h3>
          <p>{{ t('website.inCollaborationModeAiSubmitsWorkFor') }}</p>
        </article>
      </div>
    </section>
    <section id="resources" class="resources-section wrap">
      <div>
        <p class="eyebrow">{{ t('website.modelsAndCloudEnvironments') }}</p>
        <h2>{{ t('website.getTheResourcesReady') }}<br />{{ t('website.soYourTeamCanStartWorkingTogether') }}</h2>
        <p>{{ t('website.aiTeammatesCanUseThePlatformsModels') }}</p>
      </div>
      <div class="resource-options">
        <article>
          <v-icon class="landing-icon" icon="mdi-creation" size="24" />
          <h3>{{ t('website.modelAccess') }}</h3>
          <p>{{ t('website.useTheModelsAvailableToYourTeam') }}</p>
        </article>
        <article>
          <v-icon class="landing-icon" icon="mdi-server-outline" size="24" />
          <h3>{{ t('website.cloudEnvironments') }}</h3>
          <p>{{ t('website.runCodeAndPrototypesInAShared') }}</p>
        </article>
        <p class="resource-terms">{{ t('website.aSelectionOfResourcesIsFreeTo') }}</p>
      </div>
    </section>
    <section id="teams" class="teams-section">
      <div class="wrap">
        <div class="section-intro">
          <div>
            <p class="eyebrow">{{ t('website.forBusinessesAndUniversities2') }}</p>
            <h2>
              {{ t('website.startWithAProject') }} <br />
              {{ t('website.supportTheWholeTeam') }}
            </h2>
          </div>
          <p>
            {{ t('website.chooseTheServicesAndResourcesYouNeed') }} <br />
            {{ t('website.forResearchContentCreationAndHandsonProjects') }}
          </p>
        </div>
        <div class="audience-tabs">
          <div
            class="audience-list"
            role="tablist"
            :aria-label="t('website.whoItIsFor')"
            @keydown="moveTab($event, 'audience')"
          >
            <button
              v-for="item in audiences"
              :id="`audience-${item.id}`"
              :key="item.id"
              type="button"
              role="tab"
              :aria-selected="audience.id === item.id"
              :tabindex="audience.id === item.id ? 0 : -1"
              aria-controls="audience-panel"
              :data-active="audience.id === item.id || undefined"
              @click="audienceId = item.id"
            >
              {{ item.label }}<v-icon class="landing-icon" icon="mdi-arrow-top-right" size="16" />
            </button>
          </div>
          <div id="audience-panel" role="tabpanel" :aria-labelledby="`audience-${audience.id}`" tabindex="0">
            <div class="audience-content">
              <div>
                <h3>{{ audience.title }}</h3>
                <p>{{ audience.description }}</p>
                <div class="audience-tags">
                  <span v-for="tag in audience.tags" :key="tag">{{ tag }}</span>
                </div>
              </div>
              <div class="collaboration-flow">
                <div v-for="(step, index) in audience.steps" :key="step">
                  <span>{{ [t('website.start'), t('website.collaborate'), t('website.deliver2')][index] }}</span
                  ><strong>{{ step }}</strong
                  ><v-icon v-if="index < 2" class="landing-icon" icon="mdi-arrow-right" />
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="organization-direction">
          <span class="eyebrow">{{ t('website.anApproachForInstitutions') }}</span>
          <p>{{ t('website.aPlatformAiTeammatesProjectContentAnd') }}</p>
          <span> {{ t('website.weAimToConnectProjectWorkContent') }} </span>
        </div>
      </div>
    </section>

    <section id="content" class="content-section wrap">
      <div class="content-layout">
        <div>
          <p class="eyebrow">{{ t('website.createContentTogether') }}</p>
          <h2>
            {{ t('website.turnSharedThinking') }} <br />
            {{ t('website.intoWorkWorthSharing') }}
          </h2>
          <p>{{ t('website.workWithYourTeamAndAiTo') }}</p>
          <p>
            {{ t('website.turnProjectExperienceIntoLearningMaterials') }} <br />
            {{ t('website.thenRefineThemThroughTeamDiscussionAnd') }}
          </p>
        </div>
        <div class="content-artifacts" :aria-label="t('website.examplesOfContentCreatedTogether')">
          <div class="content-sheet">
            <small>{{ t('website.contentCollaborationExamples') }}</small>
            <h3>{{ t('website.oneProjectManyWaysToShare') }}</h3>
            <div>
              <v-icon class="landing-icon" icon="mdi-file-document-outline" /> {{ t('website.researchReports') }}
              <span>{{ t('website.explainQuestionsAndFindings') }}</span>
            </div>
            <div>
              <v-icon class="landing-icon" icon="mdi-layers-outline" /> {{ t('website.caseStudies') }}
              <span>{{ t('website.showTheProcessAndMethods') }}</span>
            </div>
            <div>
              <v-icon class="landing-icon" icon="mdi-compass-outline" /> {{ t('website.learningMaterials') }}
              <span>{{ t('website.shareWhatOthersCanUse') }}</span>
            </div>
          </div>
          <div class="content-caption">
            <v-icon class="landing-icon" icon="mdi-creation" size="15" />
            {{ t('website.createdWithAiReviewedByTheTeam') }}
          </div>
        </div>
      </div>
    </section>
    <section id="vision" class="vision-section wrap">
      <p class="eyebrow">{{ t('website.ourVisionFutureLearningCenters') }}</p>
      <div class="vision-layout">
        <h2>
          {{ t('website.eachCollaboration') }} <br />
          {{ t('website.becomesAStartingPoint') }} <br />
          <span>{{ t('website.forTheNextDiscovery') }}</span>
        </h2>
        <div>
          <LandingSpark class="landing-spark vision-symbol" aria-hidden="true" focusable="false" />
          <p>{{ t('website.withAiAsAnOngoingTeammateTeams') }}</p>
          <p>{{ t('website.cheeseAimsToTurnThatThinkingInto') }}</p>
          <a
            class="policy-link"
            href="https://hudong.moe.gov.cn/srcsite/A16/s3342/202604/t20260410_1433240.html"
            target="_blank"
            rel="noreferrer"
          >
            {{ t('website.readChinasPolicyOnFutureLearningCenters') }}
            <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="12" />
          </a>
          <span class="vision-english">
            THINK TOGETHER.
            <br />
            BUILD TOGETHER.
            <br />
            GROW TOGETHER.
          </span>
        </div>
      </div>
    </section>
    <section class="closing">
      <div class="wrap">
        <p>
          {{ t('website.bringYourQuestion') }} <br />
          {{ t('website.startWithYourTeam') }}
        </p>
        <a class="button button-light" :href="entryHref">
          {{ entryLabel }}
          <v-icon class="landing-icon" icon="mdi-arrow-top-right" size="19" />
        </a>
      </div>
    </section>
    <footer class="wrap">
      <a class="brand" href="#top" :aria-label="t('website.cheeseHome')">
        <span class="cheese-mark" :style="{ maskImage: `url(${logo})` }" aria-hidden="true" />
        <span>
          cheese<span v-if="i18n.global.locale.value === 'zh-CN'" class="brand-cn">{{ t('website.cheese') }}</span>
        </span>
      </a>
      <p>{{ t('website.peopleAndAiGettingWorkDoneTogether') }}</p>
      <span> {{ t('website.2026Cheese') }} </span>
    </footer>
  </main>
</template>
<style scoped src="./landing.css"></style>
<style>
/* The workspace locks document scrolling; the public homepage scrolls as a document. */
html:has(.landing-page) {
  overflow-y: auto !important;
}
body:has(.landing-page),
#app:has(.landing-page) {
  height: auto;
  overflow: visible;
}
</style>
