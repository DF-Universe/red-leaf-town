<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { HandHeart, Search, Sparkles } from 'lucide-vue-next'

import ActionButton from '@/components/ActionButton.vue'
import ModalSheet from '@/components/ModalSheet.vue'
import PartnerAvatar from '@/components/PartnerAvatar.vue'
import RarityBadge from '@/components/RarityBadge.vue'
import RarityFrame from '@/components/RarityFrame.vue'
import { useGameStore } from '@/stores/game'
import type { InventoryItem, PartnerSelectCandidate } from '@/types'

const props = defineProps<{ open: boolean; item: InventoryItem | null }>()
const emit = defineEmits<{ (event: 'close'): void; (event: 'used'): void }>()

const game = useGameStore()

const candidates = ref<PartnerSelectCandidate[]>([])
const eligibleTotal = ref(0)
const selectedId = ref('')
const keyword = ref('')
const rarities = ref<number[]>([])

const selected = computed(() => candidates.value.find((entry) => entry.id === selectedId.value) || null)
const availableRarities = computed(() =>
  [...new Set(candidates.value.map((entry) => entry.rarity as number))].sort((left, right) => right - left),
)
const visible = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return candidates.value.filter((entry) => {
    if (text && !entry.name.toLowerCase().includes(text)) return false
    if (rarities.value.length && !rarities.value.includes(entry.rarity)) return false
    return true
  })
})

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    selectedId.value = ''
    keyword.value = ''
    rarities.value = []
    candidates.value = []
    const payload = await game.loadPartnerSelectCandidates()
    if (payload) {
      candidates.value = payload.candidates
      eligibleTotal.value = payload.eligible_total
    }
  },
)

watch(availableRarities, (values) => {
  rarities.value = rarities.value.filter((entry) => values.includes(entry))
})

function toggleRarity(rarity: number) {
  const index = rarities.value.indexOf(rarity)
  if (index >= 0) rarities.value.splice(index, 1)
  else rarities.value.push(rarity)
}

function compensationText(entry: PartnerSelectCandidate) {
  if (!entry.companion_marks_granted) return '无（五星伙伴）'
  return `同行印记 ×${entry.companion_marks_granted}`
}

async function confirm() {
  if (!props.item || !selected.value) return
  const result = await game.useInventoryItem(props.item.item_id, selected.value.id)
  if (result) emit('used')
}
</script>

<template>
  <ModalSheet
    :open="open && Boolean(item)"
    :title="item?.name || '伙伴邀约函'"
    subtitle="从名单里选择一位伙伴，迎进你的小镇"
    @close="emit('close')"
  >
    <template #toolbar>
      <label class="search-field">
        <Search :size="16" />
        <input v-model="keyword" type="search" placeholder="搜索伙伴名字" />
      </label>

      <div class="filter-row">
        <button
          v-for="rarity in availableRarities"
          :key="rarity"
          class="filter-chip rarity"
          :class="{ active: rarities.includes(rarity) }"
          @click="toggleRarity(rarity)"
        >{{ rarity }}★</button>
      </div>
    </template>

    <article v-if="selected" class="invite-detail">
      <RarityFrame class="invite-portrait" :rarity="selected.rarity" aspect="9 / 16" badge-size="md">
        <img v-if="selected.artwork?.url" :src="selected.artwork.url" :alt="selected.name" />
        <div v-else class="invite-portrait-empty"><Sparkles :size="46" /><span>暂无立绘</span></div>
      </RarityFrame>

      <div class="invite-profile">
        <p class="eyebrow">{{ selected.id }}</p>
        <div class="invite-title"><h2>{{ selected.name }}</h2><RarityBadge :rarity="selected.rarity" /></div>
        <p class="invite-description">{{ selected.description || '这位伙伴的故事尚未记录。' }}</p>

        <div class="invite-meta">
          <span><small>成长曲线</small><strong>{{ selected.growth_curve_name || '—' }}</strong></span>
          <span><small>结缘补偿</small><strong>{{ compensationText(selected) }}</strong></span>
        </div>

        <section class="invite-section">
          <h3>产业倾向</h3>
          <div class="invite-tendencies">
            <div v-for="tendency in selected.tendencies" :key="tendency.industry">
              <span>{{ tendency.name }}</span><strong>{{ tendency.ability }}</strong><small>初始能力</small>
            </div>
          </div>
        </section>

        <section v-if="selected.traits.length" class="invite-section">
          <h3>特性</h3>
          <div class="invite-traits">
            <div v-for="trait in selected.traits" :key="trait.code"><strong>{{ trait.name }} <small v-if="trait.min_breakthrough">突破 {{ trait.min_breakthrough }} 次解锁</small></strong><span>{{ trait.description }}</span></div>
          </div>
        </section>
      </div>
    </article>

    <p v-else class="invite-hint">点击一位伙伴的头像查看详情</p>

    <div v-if="candidates.length" class="invite-grid">
      <button
        v-for="entry in visible"
        :key="entry.id"
        class="invite-tile"
        :class="{ selected: entry.id === selectedId }"
        @click="selectedId = entry.id"
      >
        <PartnerAvatar :artwork="entry.artwork" :crop="entry.avatar_crop" :name="entry.name" :size="62" />
        <span class="invite-name">{{ entry.name }}</span>
        <RarityBadge :rarity="entry.rarity" size="xs" />
      </button>

      <p v-if="!visible.length" class="invite-empty">没有符合条件的伙伴</p>
    </div>

    <p v-else-if="eligibleTotal" class="invite-empty">
      名单上的伙伴已经全部加入小镇
    </p>

    <template #footer>
      <div class="invite-footer">
        <span class="invite-usage">剩余 <strong>×{{ item?.quantity || 0 }}</strong></span>
        <ActionButton
          :action-key="`inventory:${item?.item_id}:use`"
          :disabled="!selected"
          @click="confirm"
        ><HandHeart :size="16" />使用</ActionButton>
      </div>
    </template>
  </ModalSheet>
</template>

<style scoped>
.search-field { display: flex; align-items: center; gap: 8px; padding: 0 11px; color: #7f8a80; border: 1px solid var(--line); border-radius: 10px; background: #0f1712; }
.search-field input { flex: 1; min-width: 0; height: 38px; color: var(--cream); border: 0; background: transparent; outline: none; }
.filter-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 9px; }
.filter-chip { min-height: 28px; padding: 0 11px; color: #93a094; border: 1px solid var(--line); border-radius: 99px; background: #ffffff05; cursor: pointer; }
.filter-chip.rarity.active { color: #16210f; font-weight: 700; border-color: transparent; background: var(--gold); }

.invite-detail { display: grid; gap: 12px; }
.invite-portrait { width: 132px; margin: 0 auto; }
.invite-portrait img { width: 100%; height: 100%; object-fit: cover; }
.invite-portrait-empty { height: 100%; display: grid; place-content: center; justify-items: center; gap: 8px; color: #9aa59c; font-size: 12px; }
.invite-profile { display: grid; gap: 7px; }
.invite-profile > .eyebrow { margin: 0; }
.invite-title { display: flex; align-items: center; gap: 10px; }
.invite-title h2 { margin: 4px 0 0; font: 700 22px Georgia, 'Noto Serif SC', serif; }
.invite-description { margin: 4px 0 0; color: #9ba69c; font-size: 13px; line-height: 1.8; }
.invite-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.invite-meta > span { padding: 10px 11px; border: 1px solid var(--line); border-radius: 10px; background: #ffffff04; }
.invite-meta small, .invite-meta strong { display: block; }
.invite-meta small { color: #6f7b72; font-size: 12px; }
.invite-meta strong { margin-top: 4px; color: var(--cream); font-size: 12px; }
.invite-section { margin-top: 8px; }
.invite-section h3 { margin: 0 0 8px; font-size: 13px; }
.invite-tendencies { display: grid; grid-template-columns: repeat(3, minmax(90px, 1fr)); gap: 8px; }
.invite-tendencies > div { display: grid; grid-template-columns: 1fr auto; align-items: end; padding: 10px 11px; border: 1px solid #87a96b24; border-radius: 10px; background: #87a96b0b; }
.invite-tendencies span { font-size: 12px; }
.invite-tendencies strong { color: var(--leaf-bright); font-size: 16px; }
.invite-tendencies small { grid-column: 1 / -1; margin-top: 3px; color: #6e7a71; font-size: 12px; }
.invite-traits { display: grid; gap: 7px; }
.invite-traits > div { padding: 10px 12px; border-left: 2px solid var(--gold); background: #ffffff04; }
.invite-traits strong, .invite-traits span { display: block; }
.invite-traits strong { font-size: 12px; }
.invite-traits span { margin-top: 4px; color: #7e8a81; font-size: 12px; }

.invite-hint { margin: 0 0 10px; text-align: center; color: #77837a; font-size: 12px; }
.invite-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; margin-top: 14px; }
.invite-tile {
  display: grid;
  justify-items: center;
  gap: 6px;
  padding: 11px 6px 9px;
  text-align: center;
  color: #d9e1d7;
  border: 1px solid transparent;
  border-radius: 13px 5px 13px 5px;
  background: #ffffff05;
  cursor: pointer;
}
.invite-tile:hover { background: #ffffff0b; }
.invite-tile.selected { border-color: #9abb7840; background: #9abb7812; }
.invite-name { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.invite-empty { grid-column: 1 / -1; padding: 16px 8px; margin: 0; text-align: center; color: #7b877d; font-size: 12px; line-height: 1.7; }

.invite-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.invite-usage { color: #8b968c; font-size: 13px; }
.invite-usage strong { color: var(--cream); }
</style>
