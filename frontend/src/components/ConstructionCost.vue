<script setup lang="ts">
import { Coins } from 'lucide-vue-next'
import type { FacilityMaterial } from '@/types'

withDefaults(defineProps<{
  coins: number
  materials: FacilityMaterial[]
  coinsAffordable?: boolean
}>(), { coinsAffordable: true })
</script>

<template>
  <div class="construction-cost">
    <div class="construction-coins">
      <Coins :size="16" />
      <strong :class="{ short: !coinsAffordable }">{{ coins }}</strong>
      <span>红叶币</span>
    </div>
    <div v-if="materials.length" class="construction-materials">
      <span v-for="material in materials" :key="material.item_id">{{ material.item.name }} {{ material.owned }} / {{ material.quantity }}</span>
    </div>
  </div>
</template>

<style scoped>
.construction-cost { display: grid; gap: 12px; }
.construction-coins { display: flex; align-items: center; gap: 7px; color: var(--gold); }
.construction-coins strong { font-size: 19px; }
.construction-coins strong.short { color: var(--danger); }
.construction-coins span { color: var(--muted); font-size: 12px; }
.construction-materials { display: grid; gap: 4px; color: var(--muted); font-size: 12px; line-height: 1.5; }
</style>
