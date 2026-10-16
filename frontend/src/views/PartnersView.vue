<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, Sparkles, WandSparkles } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'

import PartnerAvatar from '@/components/PartnerAvatar.vue'
import PartnerTrainingPanel from '@/components/PartnerTrainingPanel.vue'
import ModalSheet from '@/components/ModalSheet.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import RarityBadge from '@/components/RarityBadge.vue'
import RarityFrame from '@/components/RarityFrame.vue'
import StateBlock from '@/components/StateBlock.vue'
import ViewHeader from '@/components/ViewHeader.vue'
import { partnerAssignmentLabel } from '@/lib/partners'
import { useGameStore } from '@/stores/game'
import type { OwnedPartner } from '@/types'

const game = useGameStore()
const route = useRoute()
const router = useRouter()
const trainingOpen = ref(false)

const selected = computed(() => {
  const requested = String(route.params.partnerId || '')
  return game.state?.partners.find((partner) => partner.partner_id === requested) || game.state?.partners[0] || null
})
watch(() => selected.value?.partner_id, () => { trainingOpen.value = false })
const artworkChoices = computed(() => selected.value?.available_artworks || [])
const artworkSaving = computed(() => game.pending.has(`partner:${selected.value?.partner_id}:artwork`))
const artworkIndex = computed(() => artworkChoices.value.findIndex((artwork) => artwork.breakthrough === selected.value?.artwork_stage))
// 限定皮肤以伪阶段（100+）并入同一条画廊，条目自带 name（皮肤名，如「盛夏鳞光」）
const currentArtwork = computed(() => artworkChoices.value[artworkIndex.value] || null)
const artworkLabel = computed(() => currentArtwork.value?.name || (selected.value?.artwork_stage ? `${selected.value.artwork_stage} 次突破立绘` : '初始立绘'))
let swipeStart: { x: number; y: number; partnerId: string; pointerId: number } | null = null

function switchArtwork(direction: number) {
  if (!selected.value || artworkSaving.value || artworkChoices.value.length < 2) return
  const index = (artworkIndex.value + direction + artworkChoices.value.length) % artworkChoices.value.length
  const artwork = artworkChoices.value[index]
  if (artwork) void game.selectPartnerArtwork(selected.value.partner_id, artwork.breakthrough)
}

function startSwipe(event: PointerEvent) {
  if (!selected.value || !event.isPrimary || event.button !== 0 || artworkSaving.value) return
  swipeStart = { x: event.clientX, y: event.clientY, partnerId: selected.value.partner_id, pointerId: event.pointerId }
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}

function endSwipe(event: PointerEvent) {
  const start = swipeStart
  swipeStart = null
  if (!start || start.pointerId !== event.pointerId || start.partnerId !== selected.value?.partner_id) return
  const dx = event.clientX - start.x
  const dy = event.clientY - start.y
  if (Math.abs(dx) >= 45 && Math.abs(dx) > Math.abs(dy) * 1.3) switchArtwork(dx < 0 ? 1 : -1)
}

const experienceProgress = computed(() => {
  if (!selected.value?.experience_to_next_level) return 100
  return Math.min(100, selected.value.experience / selected.value.experience_to_next_level * 100)
})

watch(
  () => [route.params.partnerId, game.state?.partners.length] as const,
  () => {
    if (!route.params.partnerId && game.state?.partners[0]) {
      router.replace({ name: 'partners', params: { partnerId: game.state.partners[0].partner_id } })
    }
  },
  { immediate: true },
)

function acquiredDate(timestamp: number) {
  return new Date(timestamp * 1000).toLocaleDateString('zh-CN')
}

function breakthroughName(stage: number) {
  return stage === 0 ? '未突破' : `${stage} 次突破`
}

function assignmentLabel(partner: OwnedPartner) {
  return partnerAssignmentLabel(partner, game.state || null) || '未派驻'
}

function rosterSubtitle(partner: OwnedPartner) {
  const assignment = partnerAssignmentLabel(partner, game.state || null)
  return `Lv.${partner.level} · ${assignment || breakthroughName(partner.breakthrough)}`
}
</script>

<template>
  <section v-if="game.state" class="view-section partner-view">
    <ViewHeader eyebrow="PARTNER ARCHIVE" title="伙伴仓库">
      <template #chip><Sparkles :size="18" /> {{ game.state.partner_count }} 位伙伴</template>
    </ViewHeader>

    <StateBlock
      v-if="!game.state.partners.length"
      :icon="WandSparkles"
      title="还没有伙伴来到这里"
    />

    <div v-else class="partner-warehouse">
      <aside class="partner-roster">
        <p>我的伙伴</p>
        <RouterLink
          v-for="partner in game.state.partners"
          :key="partner.partner_id"
          :to="{ name: 'partners', params: { partnerId: partner.partner_id } }"
          :class="{ active: selected?.partner_id === partner.partner_id }"
        >
          <PartnerAvatar :artwork="partner.artwork" :crop="partner.avatar_crop" :name="partner.name" :size="43" />
          <span><strong>{{ partner.name }}</strong><small>{{ rosterSubtitle(partner) }}</small></span>
          <RarityBadge :rarity="partner.stars || partner.rarity" size="xs" />
        </RouterLink>
      </aside>

      <article v-if="selected" class="partner-detail">
        <div class="partner-artwork-picker">
          <RarityFrame class="partner-illustration" :rarity="selected.stars || selected.rarity" aspect="9 / 16" badge-size="md"
            @pointerdown="startSwipe" @pointerup="endSwipe" @pointercancel="swipeStart = null" @lostpointercapture="swipeStart = null">
            <img v-if="selected.artwork?.url" :src="selected.artwork.url" :alt="`${selected.name} · ${artworkLabel}`" :draggable="false" />
            <div v-else class="partner-illustration-empty"><Sparkles :size="46" /><span>暂无当前形态插画</span></div>
            <div v-if="currentArtwork?.name" class="artwork-skin-title">{{ currentArtwork.name }}</div>
          </RarityFrame>
          <div v-if="artworkChoices.length > 1" class="artwork-controls" :aria-busy="artworkSaving">
            <button class="secondary-button" aria-label="上一张立绘" :disabled="artworkSaving" @click="switchArtwork(-1)"><ChevronLeft :size="20" /></button>
            <span aria-live="polite"><strong>{{ artworkLabel }}</strong><small>{{ artworkSaving ? '正在保存…' : '左右滑动切换 · 自动保存' }}</small></span>
            <button class="secondary-button" aria-label="下一张立绘" :disabled="artworkSaving" @click="switchArtwork(1)"><ChevronRight :size="20" /></button>
          </div>
        </div>

        <div class="partner-profile">
          <p class="eyebrow">{{ selected.partner_id }}</p>
          <div class="profile-title"><h2>{{ selected.name }}</h2><span>Lv.{{ selected.level }} / {{ selected.level_cap || 20 }}</span><button class="primary-button training-trigger" aria-haspopup="dialog" @click="trainingOpen = true"><WandSparkles :size="17" />培养</button></div>
          <p class="partner-description">{{ selected.description || '这位伙伴的故事尚未记录。' }}</p>

          <div class="partner-xp">
            <span><strong>伙伴经验 {{ selected.experience }}{{ selected.experience_to_next_level ? ` / ${selected.experience_to_next_level}` : '' }}</strong><small>{{ selected.experience_to_next_level ? '距离下一级' : '当前突破阶段已满级，溢出经验会保留' }}</small></span>
            <ProgressBar :value="experienceProgress" :height="7" />
          </div>

          <div class="profile-meta">
            <span><small>突破阶段</small><strong>{{ breakthroughName(selected.breakthrough) }}</strong></span>
            <span><small>成长曲线</small><strong>{{ selected.growth_curve_name || '—' }}</strong></span>
            <span><small>结缘日期</small><strong>{{ acquiredDate(selected.acquired_at) }}</strong></span>
            <span><small>当前派驻</small><strong>{{ assignmentLabel(selected) }}</strong></span>
          </div>

          <section class="profile-section">
            <h3>产业倾向</h3>
            <div class="tendency-list">
              <div v-for="tendency in selected.tendencies || []" :key="tendency.industry">
                <span>{{ tendency.name }}</span><strong>{{ tendency.current_ability }}</strong><small>当前能力</small>
              </div>
            </div>
          </section>

          <section class="profile-section">
            <h3>特性</h3>
            <div v-if="selected.traits?.length" class="owned-traits">
              <div v-for="trait in selected.traits" :key="trait.code"><strong>{{ trait.name }}</strong><span>{{ trait.description }}</span></div>
            </div>
            <p v-else class="section-placeholder">当前没有已配置特性。</p>
          </section>

          <ModalSheet :open="trainingOpen" :title="`${selected.name} · 培养`" presentation="dialog" @close="trainingOpen = false">
            <PartnerTrainingPanel v-if="trainingOpen" :key="selected.partner_id" :partner="selected" />
          </ModalSheet>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.partner-warehouse {
  /* 顶栏 72 + 等级条 34；未滚动时列表上方还有页面内边距与标题块，滚动条高度按最紧的那一刻算 */
  --roster-top: 106px;
  --roster-lead: 152px;
  display: grid; grid-template-columns: 245px minmax(0, 1fr); gap: 16px; align-items: start;
}
.partner-roster { min-width: 0; position: sticky; top: var(--roster-top); max-height: calc(100vh - var(--roster-top) - var(--roster-lead)); overflow-y: auto; overscroll-behavior: contain; padding: 13px; border: 1px solid var(--line); border-radius: 19px 6px; background: var(--surface); }.partner-roster > p { position: sticky; top: -13px; z-index: 1; margin: 0 7px 13px; padding: 4px 0 6px; color: #77837a; font-size: 12px; font-weight: 800; letter-spacing: .12em; background: var(--surface); }.partner-roster a { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 9px; padding: 9px; margin-top: 4px; border: 1px solid transparent; border-radius: 11px; }.partner-roster a.active { border-color: #9abb7833; background: #9abb7811; }.partner-roster strong,.partner-roster small { display: block; }.partner-roster strong { font-size: 12px; }.partner-roster small { margin-top: 3px; color: #768178; font-size: 12px; }.partner-roster :deep(.rarity-badge-group) { flex: 0 0 auto; }
.partner-detail { min-width: 0; display: grid; grid-template-columns: minmax(230px, 330px) minmax(0, 1fr); gap: clamp(24px, 4vw, 48px); padding: clamp(18px, 3vw, 34px); border: 1px solid var(--line); border-radius: 25px 8px; background: linear-gradient(145deg, #1b271f, #151e19); }.partner-illustration { width: 100%; box-shadow: 0 22px 60px #0005; }.partner-illustration-empty { display: grid; place-items: center; gap: 9px; width: 100%; height: 100%; color: #708078; font-size: 12px; }
.partner-artwork-picker { min-width: 0; }
.partner-illustration { touch-action: pan-y; user-select: none; }
/* 限定皮肤立绘的标题条：只有皮肤条目带 name，突破立绘不会出现 */
.artwork-skin-title {
  position: absolute;
  left: 50%;
  top: 12px;
  transform: translateX(-50%);
  z-index: 2;
  padding: 5px 15px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: .08em;
  white-space: nowrap;
  color: #ffeecd;
  background: rgba(14, 22, 17, .62);
  border: 1px solid rgba(232, 195, 122, .48);
  box-shadow: 0 6px 18px rgba(0, 0, 0, .35);
  backdrop-filter: blur(6px);
  pointer-events: none;
}
.artwork-controls { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 14px; }
.artwork-controls button { display: grid; place-items: center; min-width: 44px; min-height: 44px; padding: 8px; }
.artwork-controls span { text-align: center; }
.artwork-controls strong, .artwork-controls small { display: block; font-size: 12px; }
.artwork-controls small { margin-top: 5px; color: var(--text-muted); }
.training-trigger { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 44px; margin-left: auto; flex-shrink: 0; }
.partner-profile { min-width: 0; padding: 10px 0; }.partner-profile > .eyebrow { margin: 0; }.profile-title { display: flex; flex-wrap: wrap; align-items: baseline; gap: 13px; }.profile-title h2 { margin: 5px 0 0; font: 700 clamp(2rem, 4vw, 3rem) Georgia, 'Noto Serif SC', serif; }.profile-title > span { color: var(--leaf-bright); font-weight: 700; }.partner-description { max-width: 620px; margin: 16px 0 22px; color: #9ba69c; font-size: 13px; line-height: 1.8; }.profile-meta { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }.profile-meta > span { padding: 11px; border: 1px solid var(--line); border-radius: 10px; background: #ffffff04; }.profile-meta small,.profile-meta strong { display: block; }.profile-meta small { color: #6f7b72; font-size: 12px; }.profile-meta strong { margin-top: 4px; font-size: 12px; }
.profile-section { margin-top: 25px; }.profile-section h3 { margin: 0 0 10px; font-size: 13px; }.tendency-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); gap: 8px; }.tendency-list > div { display: grid; grid-template-columns: 1fr auto; align-items: end; padding: 11px; border: 1px solid #87a96b24; border-radius: 10px; background: #87a96b0b; }.tendency-list span { font-size: 12px; }.tendency-list strong { color: var(--leaf-bright); font-size: 17px; }.tendency-list small { grid-column: 1 / -1; margin-top: 3px; color: #6e7a71; font-size: 12px; }.owned-traits { display: grid; gap: 7px; }.owned-traits > div { padding: 10px 12px; border-left: 2px solid var(--gold); background: #ffffff04; }.owned-traits strong,.owned-traits span { display: block; }.owned-traits strong { font-size: 12px; }.owned-traits span { margin-top: 4px; color: #7e8a81; font-size: 12px; }.section-placeholder { color: #707c73; font-size: 12px; }
.partner-xp { display: grid; gap: 8px; margin: 18px 0; }.partner-xp > span { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; font-size: 12px; }.partner-xp small { color: #7e8a81; }
@media (max-width: 900px) { .partner-warehouse { grid-template-columns: minmax(0, 1fr); }.partner-roster { min-width: 0; position: static; max-height: none; display: flex; overflow-x: auto; }.partner-roster > p { display: none; }.partner-roster a { min-width: 205px; }.partner-detail { grid-template-columns: minmax(190px, 270px) 1fr; } }
@media (min-width: 761px) and (max-width: 1050px) { .partner-detail { grid-template-columns: minmax(0, 1fr); }.partner-illustration { width: min(260px, 100%); margin: auto; }.profile-meta { grid-template-columns: 1fr 1fr; } }
@media (max-width: 650px) { .partner-detail { grid-template-columns: 1fr; }.partner-illustration { width: min(280px, 100%); margin: auto; }.profile-meta { grid-template-columns: 1fr 1fr; }.tendency-list { grid-template-columns: 1fr 1fr; } }
</style>
