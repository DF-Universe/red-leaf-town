<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDownWideNarrow, Brain, Check, Clover, Dumbbell, Lock, Search, Sparkles, UserRoundX, Wind } from 'lucide-vue-next'

import ModalSheet from '@/components/ModalSheet.vue'
import PartnerAvatar from '@/components/PartnerAvatar.vue'
import { usePartnerRoster } from '@/composables/usePartnerRoster'
import { industryName } from '@/lib/industries'
import { isPartnerAssigned, partnerAssignmentLabel } from '@/lib/partners'
import { useGameStore } from '@/stores/game'
import type { IndustryId, OwnedPartner } from '@/types'

const props = defineProps<{
  open: boolean
  industry: IndustryId
  assigned?: OwnedPartner | null
  title?: string
  soloLabel?: string
  elevated?: boolean
  candidates?: OwnedPartner[]
  excludedPartnerIds?: string[]
  clearable?: boolean
}>()

const emit = defineEmits<{ (event: 'select', partnerId: string | null): void; (event: 'close'): void }>()

const SORTS = [
  { id: 'ability', name: '能力' },
  { id: 'level', name: '等级' },
  { id: 'rarity', name: '稀有度' },
  { id: 'recent', name: '最近获得' },
] as const

const STATUSES = [
  { id: 'all', name: '全部' },
  { id: 'free', name: '空闲' },
  { id: 'assigned', name: '已派驻' },
] as const

const EXPLORATION_STATS = {
  strength: { name: '力量较高', icon: Dumbbell },
  agility: { name: '敏捷较高', icon: Wind },
  intelligence: { name: '智力较高', icon: Brain },
  luck: { name: '幸运较高', icon: Clover },
} as const

type SortId = (typeof SORTS)[number]['id']
type StatusId = (typeof STATUSES)[number]['id']

const game = useGameStore()
const { partners: rosterPartners, ability, isBusyElsewhere } = usePartnerRoster(() => props.industry)
const partners = computed(() => props.candidates ?? rosterPartners.value)

const keyword = ref('')
const sort = ref<SortId>('ability')
const status = ref<StatusId>('all')
const rarities = ref<number[]>([])

const currentId = computed(() => props.assigned?.partner_id || null)
const availableRarities = computed(() =>
  [...new Set(partners.value.map((partner) => partner.rarity || 0))].filter(Boolean).sort((a, b) => b - a),
)

const visible = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  const filtered = partners.value.filter((partner) => {
    if (props.excludedPartnerIds?.includes(partner.partner_id) && partner.partner_id !== currentId.value) return false
    if (text && !partner.name.toLowerCase().includes(text)) return false
    if (rarities.value.length && !rarities.value.includes(partner.rarity || 0)) return false
    if (status.value === 'free' && (partner.locked || isPartnerAssigned(partner))) return false
    if (status.value === 'assigned' && !isPartnerAssigned(partner)) return false
    return true
  })
  return filtered.sort((left, right) => {
    const busy = Number(isBusyElsewhere(left, currentId.value)) - Number(isBusyElsewhere(right, currentId.value))
    if (busy) return busy
    if (sort.value === 'level') return right.level - left.level || ability(right) - ability(left)
    if (sort.value === 'rarity') return (right.rarity || 0) - (left.rarity || 0) || ability(right) - ability(left)
    if (sort.value === 'recent') return right.acquired_at - left.acquired_at
    return ability(right) - ability(left) || right.level - left.level
  })
})

watch(
  () => props.open,
  (open) => {
    if (open) keyword.value = ''
  },
)

function toggleRarity(rarity: number) {
  const index = rarities.value.indexOf(rarity)
  if (index >= 0) rarities.value.splice(index, 1)
  else rarities.value.push(rarity)
}

function choose(partnerId: string | null) {
  emit('select', partnerId)
  emit('close')
}

function statusText(partner: OwnedPartner) {
  if (isBusyElsewhere(partner, currentId.value)) return partnerAssignmentLabel(partner, game.state) || '任务中'
  if (partner.partner_id === currentId.value) return '当前派驻在这里'
  return partnerAssignmentLabel(partner, game.state) || '空闲中'
}

function explorationTraits(partner: OwnedPartner) {
  return (partner.traits || []).filter((trait) => trait.unlocked !== false && trait.phases?.includes('exploration_event'))
}
</script>

<template>
  <ModalSheet
    :open="open"
    :title="title || '选择伙伴'"
    :subtitle="`${industryName(industry)} · 共 ${partners.length} 位可派驻伙伴`"
    :elevated="elevated"
    @close="emit('close')"
  >
    <template #toolbar>
      <label class="search-field">
        <Search :size="16" />
        <input v-model="keyword" type="search" placeholder="搜索伙伴名字" />
      </label>

      <div class="filter-row">
        <button
          v-for="entry in STATUSES"
          :key="entry.id"
          class="filter-chip"
          :class="{ active: status === entry.id }"
          @click="status = entry.id"
        >{{ entry.name }}</button>
        <span class="filter-divider" />
        <button
          v-for="rarity in availableRarities"
          :key="rarity"
          class="filter-chip rarity"
          :class="{ active: rarities.includes(rarity) }"
          @click="toggleRarity(rarity)"
        >{{ rarity }}★</button>
      </div>

      <div class="filter-row">
        <span class="sort-label"><ArrowDownWideNarrow :size="14" /> 排序</span>
        <button
          v-for="entry in SORTS"
          :key="entry.id"
          class="filter-chip"
          :class="{ active: sort === entry.id }"
          @click="sort = entry.id"
        >{{ entry.name }}</button>
      </div>
    </template>

    <div class="option-list">
      <button v-if="assigned && clearable !== false" class="partner-option solo" @click="choose(null)">
        <span class="option-mark"><UserRoundX :size="18" /></span>
        <span class="option-copy">
          <strong>{{ soloLabel || '撤下伙伴' }}</strong>
        </span>
      </button>

      <button
        v-for="partner in visible"
        :key="partner.partner_id"
        class="partner-option"
        :class="{ current: partner.partner_id === currentId }"
        :disabled="isBusyElsewhere(partner, currentId)"
        @click="choose(partner.partner_id)"
      >
        <PartnerAvatar :artwork="partner.artwork" :crop="partner.avatar_crop" :name="partner.name" :size="40" />
        <span class="option-copy">
          <strong>{{ partner.name }}<i v-if="partner.rarity">{{ partner.rarity }}★</i></strong>
          <small>Lv.{{ partner.level }} · {{ statusText(partner) }}</small>
          <span v-if="industry === 'exploration' && partner.exploration_high_stats?.length" class="exploration-stat-marks">
            <i
              v-for="stat in partner.exploration_high_stats"
              :key="stat"
              class="exploration-stat-mark"
              :title="EXPLORATION_STATS[stat].name"
            ><component :is="EXPLORATION_STATS[stat].icon" :size="13" />{{ EXPLORATION_STATS[stat].name }}</i>
          </span>
          <span v-if="industry === 'exploration' && explorationTraits(partner).length" class="exploration-trait-lines">
            <i v-for="trait in explorationTraits(partner)" :key="trait.code" :title="trait.description">
              <Sparkles :size="12" />{{ trait.name }}
            </i>
          </span>
        </span>
        <span class="option-ability"><strong>{{ ability(partner) }}</strong><small>能力</small></span>
        <Lock v-if="isBusyElsewhere(partner, currentId)" :size="14" class="option-lock" />
        <Check v-else-if="partner.partner_id === currentId" :size="16" class="option-check" />
      </button>

      <p v-if="!visible.length" class="option-empty">
        {{ partners.length ? '没有符合筛选条件的伙伴' : '还没有适合这个产业的伙伴' }}
      </p>
    </div>
  </ModalSheet>
</template>

<style scoped>
.search-field { display: flex; align-items: center; gap: 8px; padding: 0 11px; color: #7f8a80; border: 1px solid var(--line); border-radius: 10px; background: #0f1712; }
.search-field input { flex: 1; min-width: 0; height: 38px; color: var(--cream); border: 0; background: transparent; outline: none; }
.filter-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 9px; }
.filter-chip { min-height: 28px; padding: 0 11px; color: #93a094; border: 1px solid var(--line); border-radius: 99px; background: #ffffff05; cursor: pointer; }
.filter-chip.active { color: #16210f; font-weight: 700; border-color: transparent; background: var(--leaf-bright); }
.filter-chip.rarity.active { background: var(--gold); }
.filter-divider { width: 1px; height: 18px; margin: 0 3px; background: var(--line); }
.sort-label { display: inline-flex; align-items: center; gap: 4px; color: #77837a; font-size: 12px; }
.option-list { display: grid; gap: 6px; }
.partner-option {
  width: 100%;
  min-height: 58px;
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: 11px;
  padding: 8px 11px;
  text-align: left;
  color: #d9e1d7;
  border: 1px solid transparent;
  border-radius: 12px;
  background: #ffffff05;
  cursor: pointer;
}
.partner-option:hover:not(:disabled) { background: #ffffff0b; }
.partner-option.current { border-color: #9abb7840; background: #9abb7812; }
.partner-option:disabled { opacity: .45; cursor: default; }
.partner-option.solo { grid-template-columns: auto 1fr; }
.option-mark { width: 40px; height: 40px; display: grid; place-items: center; color: #9aa59c; border: 1px dashed #ffffff1f; border-radius: 11px 4px; }
.option-copy { min-width: 0; }
.option-copy strong, .option-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.option-copy strong { font-size: 13px; }
.option-copy strong i { margin-left: 6px; color: var(--gold); font-size: 12px; font-style: normal; }
.option-copy small { margin-top: 3px; color: #79857b; font-size: 12px; }
.exploration-stat-marks { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.exploration-stat-mark { display: inline-flex; align-items: center; gap: 3px; padding: 2px 6px; color: #c9d5c5; font-size: 10px; font-style: normal; border-radius: 99px; background: #ffffff09; }
.exploration-trait-lines { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.exploration-trait-lines i { display: inline-flex; align-items: center; gap: 3px; color: #d8bb7c; font-size: 10px; font-style: normal; }
.option-ability { text-align: right; }
.option-ability strong { display: block; color: var(--leaf-bright); font-size: 15px; }
.option-ability small { color: #6f7b72; font-size: 12px; }
.option-lock { color: #77837a; }
.option-check { color: var(--leaf-bright); }
.option-empty { padding: 22px 10px; margin: 0; text-align: center; color: #7b877d; font-size: 12px; line-height: 1.7; }
</style>
