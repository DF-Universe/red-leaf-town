<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { AlertTriangle, Droplet, Fish, Hourglass, Sprout } from 'lucide-vue-next'

import ActionButton from '@/components/ActionButton.vue'
import GameIcon from '@/components/GameIcon.vue'
import PartnerPicker from '@/components/PartnerPicker.vue'
import PartnerSwapNote from '@/components/PartnerSwapNote.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import { useCountdown } from '@/composables/useCountdown'
import { formatDuration } from '@/lib/format'
import { useGameStore } from '@/stores/game'
import type { AquaticState, PondState } from '@/types'

const props = defineProps<{ pond: PondState; aquatic: AquaticState }>()

const game = useGameStore()
const stockCount = ref(1)
const harvestCount = ref(1)

const accent = computed(() => props.pond.definition?.accent || '#4f8f9c')
const species = computed(() => props.aquatic.species.find((entry) => entry.unlocked && entry.id === props.pond.definition?.species_id) || null)
const stockable = computed(() => {
  const target = props.pond.species ? props.aquatic.species.find((entry) => entry.id === props.pond.species_id) : species.value
  if (!target) return null
  // 鱼苗也占位置，所以剩余空间要按成鱼加鱼苗一起算。
  return { ...target, room: props.pond.capacity - props.pond.population }
})

const { label: cycleLabel, progress: cycleProgress } = useCountdown(
  () => props.pond.last_settled_at + props.pond.next_cycle_seconds,
  () => props.pond.cycle_seconds,
)

/** 鱼苗按"还差几个周期"分组：不足一个周期的也要走完那个周期，所以向上取整。 */
const fryGroups = computed(() => {
  const groups = new Map<number, number>()
  for (const batch of props.pond.fry || []) {
    const cycles = Math.max(1, Math.ceil(batch.cycles_left - 1e-6))
    groups.set(cycles, (groups.get(cycles) || 0) + batch.count)
  }
  return [...groups.entries()]
    .sort((left, right) => right[0] - left[0])
    .map(([cycles, count]) => ({ cycles, count }))
})

const generationPercent = computed(() =>
  props.pond.generation_cap ? (props.pond.generation_score / props.pond.generation_cap) * 100 : 0,
)

// 捞多少留多少是这口塘唯一的决策，所以滑杆跟着存量走。
watch(
  () => props.pond.stock,
  (stock) => {
    harvestCount.value = Math.max(1, Math.min(harvestCount.value, stock))
  },
  { immediate: true },
)

const remainAfterHarvest = computed(() => props.pond.stock - harvestCount.value)
const generationWarning = computed(() => props.pond.stock > 0 && remainAfterHarvest.value < props.pond.steady_stock)
const steady = computed(() => props.pond.stock >= props.pond.steady_stock)

const steadyHint = computed(() => {
  const pond = props.pond
  const line = `稳态线 ${pond.steady_stock} 尾成鱼（鱼苗不计入）`
  if (steady.value) {
    return `${line}。鱼群稳定，世代加值每周期 +${pond.generation_gain}，捞起时的品质随之提升。`
  }
  return `${line}。成鱼不足，鱼群不稳定，世代加值每周期 −${pond.generation_decay}，直到重新回到稳态线以上。`
})

const harvestHint = computed(() => {
  if (generationWarning.value) {
    return `捞鱼不消耗体力。留下 ${remainAfterHarvest.value} 尾低于稳态线 ${props.pond.steady_stock} 尾，世代加值会逐周期回落。`
  }
  return `捞鱼不消耗体力。留下 ${remainAfterHarvest.value} 尾仍在稳态线之上，世代加值继续累积。`
})

function stock() {
  if (!stockable.value) return
  game.stockPond(props.pond.pond_id, stockable.value.id, Math.max(1, stockCount.value))
}

function harvest() {
  game.harvestPond(props.pond.pond_id, Math.max(1, harvestCount.value))
}
</script>

<template>
  <article class="pond surface-card" :style="{ '--pond-accent': accent }">
    <header>
      <div>
        <h3>{{ pond.definition?.name || pond.pond_id }}</h3>
        <small>{{ pond.definition?.description }}</small>
      </div>
      <span class="pond-stock">{{ pond.population }}<i>/{{ pond.capacity }}</i></span>
    </header>

    <p v-if="pond.stalled" class="pond-alert">
      <AlertTriangle :size="15" /> 饲料槽已空，本塘暂停运转：不再繁殖，鱼苗也停止生长，存量不会减少。
    </p>

    <template v-if="pond.empty">
      <div class="pond-empty">
        <Droplet :size="30" />
        <strong>空塘</strong>
        <span>投入鱼苗即可开塘。鱼苗要养满三个周期才长成能捞的成鱼，成鱼越多，繁殖越快。</span>
      </div>
    </template>

    <template v-else>
      <div class="cycle">
        <div class="cycle-line">
          <span>下一周期</span>
          <strong>{{ cycleLabel }}</strong>
        </div>
        <ProgressBar :value="cycleProgress" color="var(--pond-accent)" :height="8" />
        <p class="cycle-spawn">
          <Sprout :size="14" /> 预计产生 <b>{{ pond.next_spawn }}</b> 尾新鱼苗
        </p>
      </div>

      <section class="census">
        <h4>当前状态</h4>
        <ul>
          <li>
            <Fish :size="15" />
            <span>成鱼</span>
            <b>{{ pond.stock }} 条</b>
          </li>
          <li v-for="group in fryGroups" :key="group.cycles" class="census-fry">
            <Sprout :size="15" />
            <span>鱼苗<i><Hourglass :size="11" />{{ group.cycles }}</i></span>
            <b>{{ group.count }} 条</b>
          </li>
        </ul>
      </section>

      <dl class="pond-stats">
        <div>
          <dt>品种</dt>
          <dd>{{ pond.species?.name }}</dd>
        </div>
        <div>
          <dt>周期</dt>
          <dd>{{ formatDuration(pond.cycle_seconds) }}</dd>
        </div>
        <div>
          <dt>吃料</dt>
          <dd>{{ pond.feed_per_cycle }} 份 / 周期</dd>
        </div>
        <div>
          <dt>品质</dt>
          <dd>Q{{ pond.quality_ability.toFixed(1) }}</dd>
        </div>
      </dl>

      <div class="generation">
        <div class="generation-line">
          <span>世代加值</span>
          <strong>+{{ pond.generation_score.toFixed(1) }} / {{ pond.generation_cap }}</strong>
        </div>
        <ProgressBar :value="generationPercent" :color="steady ? 'var(--gold)' : '#cf8c80'" />
        <small :class="{ warn: !steady }">{{ steadyHint }}</small>
      </div>
    </template>

    <PartnerPicker
      industry="aquatic"
      :action-key="`aquatic:pond:${pond.pond_id}:partner`"
      :assigned="pond.assigned_partners[0] || null"
      placeholder="安排一位伙伴看塘"
      dialog-title="鱼塘驻场"
      solo-label="自己照看"
      @select="(partnerId) => game.assignPondPartner(pond.pond_id, partnerId)"
    />

    <PartnerSwapNote
      :pending-ids="pond.pending_partner_ids"
      :pending-partner="pond.pending_partner"
      :assigned="pond.assigned_partners[0] || null"
      :swap-open="pond.swap_open"
      :ready-at="pond.last_settled_at + pond.next_cycle_seconds"
      :cycle-seconds="pond.cycle_seconds"
      :window-seconds="pond.swap_window_seconds"
    />

    <div v-if="stockable && stockable.room > 0" class="pond-action">
      <label>
        <span><Sprout :size="14" /> 投{{ stockable.fry_item.name }}</span>
        <input
          v-model.number="stockCount"
          type="number"
          min="1"
          :max="Math.min(stockable.room, stockable.owned_fry) || 1"
        >
      </label>
      <ActionButton
        :action-key="`aquatic:pond:${pond.pond_id}:stock`"
        :disabled="!stockable.owned_fry || stockCount > stockable.owned_fry || stockCount > stockable.room"
        :reason="!stockable.owned_fry ? '仓库里没有鱼苗' : undefined"
        variant="secondary"
        @click="stock"
      >
        投苗
      </ActionButton>
      <small>
        仓库 {{ stockable.owned_fry }} 尾 · 还能放 {{ stockable.room }} 尾 ·
        投入后 {{ formatDuration(pond.maturation_seconds || pond.cycle_seconds * 3) }}长成
      </small>
    </div>

    <div v-if="pond.stock > 0" class="pond-action">
      <label>
        <span><Fish :size="14" /> 捞 {{ harvestCount }} 尾，留 {{ remainAfterHarvest }} 尾</span>
        <input v-model.number="harvestCount" type="range" min="1" :max="pond.stock">
      </label>
      <ActionButton :action-key="`aquatic:pond:${pond.pond_id}:harvest`" @click="harvest">捞鱼</ActionButton>
      <small :class="{ warn: generationWarning }">{{ harvestHint }}</small>
    </div>

    <p v-if="pond.produce_item" class="pond-produce">
      <GameIcon :name="pond.produce_item.icon" :size="14" /> 产出 {{ pond.produce_item.name }}
    </p>
  </article>
</template>

<style scoped>
.pond { --pond-accent: #4f8f9c; display: flex; flex-direction: column; gap: 13px; padding: 18px; border-color: color-mix(in srgb, var(--pond-accent) 30%, transparent); background: linear-gradient(150deg, color-mix(in srgb, var(--pond-accent) 9%, #141b16), #101713); }
.pond header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.pond h3 { margin: 0; font: 600 17px Georgia, 'Noto Serif SC', serif; }
.pond header small { display: block; margin-top: 5px; color: #849087; line-height: 1.55; }
.pond-stock { flex: 0 0 auto; color: var(--pond-accent); font-size: 21px; font-weight: 700; }
.pond-stock i { color: #6f7a72; font-size: 13px; font-style: normal; }

.pond-alert { display: flex; align-items: center; gap: 7px; margin: 0; padding: 9px 11px; color: #e0b98a; font-size: 12px; line-height: 1.5; border: 1px solid rgba(215, 173, 88, .24); border-radius: 10px; background: rgba(215, 173, 88, .07); }

.pond-empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 22px 12px; color: #6f7a72; text-align: center; border: 1px dashed var(--line); border-radius: 14px; }
.pond-empty strong { color: #aeb8ad; }
.pond-empty span { font-size: 12px; line-height: 1.6; max-width: 32ch; }

.cycle-line { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 6px; }
.cycle-line span { color: #77837a; font-size: 12px; }
.cycle-line strong { color: var(--pond-accent); font-size: 15px; }
.cycle-spawn { display: flex; align-items: center; gap: 6px; margin: 8px 0 0; color: #8d998e; font-size: 12px; }
.cycle-spawn b { color: #cbd6c9; }

.census h4 { margin: 0 0 8px; color: #77837a; font-size: 12px; font-weight: 600; }
.census ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.census li { display: flex; align-items: center; gap: 8px; padding: 8px 11px; color: #b3bdb2; font-size: 13px; border: 1px solid var(--line); border-radius: 11px; background: #ffffff04; }
.census li b { margin-left: auto; font-size: 13px; }
.census-fry { color: #93a495; }
.census-fry i { display: inline-flex; align-items: center; gap: 2px; margin-left: 4px; padding: 1px 6px; color: #9fc0a8; font-style: normal; font-size: 11px; border-radius: 99px; background: rgba(159, 192, 168, .12); }

.pond-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 18px; margin: 0; }
.pond-stats dt { color: #77837a; font-size: 12px; }
.pond-stats dd { margin: 3px 0 0; font-size: 13px; font-weight: 600; }

.generation-line { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 6px; }
.generation-line span { color: #77837a; font-size: 12px; }
.generation-line strong { color: var(--gold); }
.generation small { display: block; margin-top: 6px; color: #6f7a72; font-size: 11px; line-height: 1.5; }
.generation small.warn { color: #cf8c80; }

.pond-action { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 9px; padding: 12px; border-radius: 13px; background: #0b110d99; }
.pond-action label { min-width: 0; }
.pond-action label span { display: flex; align-items: center; gap: 6px; color: #9aa79b; font-size: 12px; }
.pond-action input { width: 100%; margin-top: 7px; }
.pond-action input[type='number'] { height: 32px; padding: 0 9px; color: inherit; border: 1px solid var(--line); border-radius: 8px; background: #ffffff06; }
.pond-action input[type='range'] { accent-color: var(--pond-accent); }
.pond-action small { grid-column: 1 / -1; color: #6f7a72; font-size: 11px; line-height: 1.5; }
.pond-action small.warn { color: #e0b98a; }

.pond-produce { display: flex; align-items: center; gap: 6px; margin: 0; color: #7d887f; font-size: 12px; }

@media (max-width: 620px) {
  .pond-action { grid-template-columns: 1fr; }
}
</style>
