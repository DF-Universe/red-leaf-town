<script setup lang="ts">
import { computed } from 'vue'
import { Tractor } from 'lucide-vue-next'

import FarmPlot from '@/components/FarmPlot.vue'
import FacilityUpgradePanel from '@/components/FacilityUpgradePanel.vue'
import ViewHeader from '@/components/ViewHeader.vue'
import { useGameStore } from '@/stores/game'

const game = useGameStore()

const availableCrops = computed(() => {
  if (!game.state) return []
  return game.state.crops.filter((crop) => (game.inventoryMap.get(crop.seed_item_id)?.quantity || 0) > 0)
})
</script>

<template>
  <section v-if="game.state" class="view-section">
    <ViewHeader eyebrow="AUTUMN FARM" title="我的农场">
      <template #chip><Tractor :size="18" /> 农作编制 {{ game.state.industry_rules.farming?.partner_capacity || 0 }}</template>
    </ViewHeader>

    <FacilityUpgradePanel kind="farm" />
    <div class="plot-grid">
      <FarmPlot v-for="plot in game.state.plots" :key="plot.slot" :plot="plot" :crops="availableCrops" />
      <FarmPlot v-if="game.state.next_plot_level" :locked-level="game.state.next_plot_level" />
    </div>
  </section>
</template>
