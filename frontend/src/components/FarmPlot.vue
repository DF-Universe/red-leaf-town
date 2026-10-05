<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChevronRight, Clock3, LockKeyhole, Sparkles } from 'lucide-vue-next'

import ActionButton from '@/components/ActionButton.vue'
import GameIcon from '@/components/GameIcon.vue'
import PartnerPicker from '@/components/PartnerPicker.vue'
import PlantSeedDialog from '@/components/PlantSeedDialog.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import QualityTag from '@/components/QualityTag.vue'
import StateBlock from '@/components/StateBlock.vue'
import { useCountdown } from '@/composables/useCountdown'
import { useGameStore } from '@/stores/game'
import { useUiStore } from '@/stores/ui'
import type { CropDefinition, PlotState } from '@/types'

const props = defineProps<{ plot?: PlotState; lockedLevel?: number | null; crops?: CropDefinition[] }>()

const game = useGameStore()
const ui = useUiStore()
const plantOpen = ref(false)

async function cancelPlanting() {
  if (!props.plot) return
  const accepted = await ui.confirm({
    title: '取消这次种植？',
    description: '种下的种子会退回仓库，伙伴仍留在这块土地。',
    confirmLabel: '取消种植',
    tone: 'danger',
  })
  if (accepted) game.cancelTask('farming', props.plot.slot)
}

const { progress, label, elapsed } = useCountdown(
  () => props.plot?.ready_at,
  () => props.plot?.task_snapshot?.final_duration || props.plot?.crop?.growth_seconds,
)

const ready = computed(() => Boolean(props.plot?.crop) && (props.plot?.ready || elapsed.value))
const assignedPartner = computed(() => props.plot?.assigned_partners[0] || null)
const activeItems = computed(() => (game.state?.task_items || []).filter((item) => (
  item.timing === 'active'
  && (!item.eligible_industries.length || item.eligible_industries.includes('farming'))
)))

function selectPartner(partnerId: string | null) {
  if (!props.plot) return
  game.assignPartner(props.plot.slot, partnerId)
}
</script>

<template>
  <StateBlock
    v-if="!plot"
    variant="tile"
    :icon="LockKeyhole"
    title="待开垦土地"
    :description="`等级 ${lockedLevel || '?'} 解锁`"
  />

  <article v-else-if="plot.empty" class="farm-plot farm-plot--empty">
    <div class="soil-lines" aria-hidden="true"><i /><i /><i /></div>
    <div class="plot-heading"><span>{{ plot.size > 1 ? `双倍田 ${plot.slot + 1}＋${plot.slot + 2}` : `土地 ${plot.slot + 1}` }}</span><small>空闲</small></div>

    <PartnerPicker
      industry="farming"
      :action-key="`plot:${plot.slot}:partner`"
      :assigned="assignedPartner"
      placeholder="安排伙伴"
      solo-label="不安排伙伴"
      dialog-title="选择驻场伙伴"
      @select="selectPartner"
    />

    <button class="plant-button" :disabled="!crops?.length" @click="plantOpen = true">
      <GameIcon name="sprout" :size="18" />
      <span>{{ crops?.length ? '选择种子播种' : '仓库里没有可用种子' }}</span>
      <ChevronRight v-if="crops?.length" :size="16" />
    </button>

    <PlantSeedDialog :open="plantOpen" :plot="plot" :crops="crops || []" @close="plantOpen = false" />
  </article>

  <article
    v-else
    class="farm-plot farm-plot--growing"
    :class="{ 'farm-plot--ready': ready }"
    :style="{ '--crop-accent': plot.crop?.accent }"
  >
    <div class="crop-orb">
      <Sparkles v-if="ready" class="ready-sparkle" :size="18" />
      <GameIcon :name="plot.crop?.icon" :size="42" />
    </div>
    <div class="plot-heading"><strong>{{ plot.crop?.name }}</strong><small>{{ plot.size > 1 ? `双倍田 ${plot.slot + 1}＋${plot.slot + 2}` : `土地 ${plot.slot + 1}` }}</small></div>

    <PartnerPicker
      industry="farming"
      :action-key="`plot:${plot.slot}:partner`"
      :assigned="assignedPartner"
      placeholder="没有驻场伙伴"
      solo-label="撤下伙伴"
      :locked="plot.assignment_locked"
      locked-label="任务中 · 已锁定"
      dialog-title="选择驻场伙伴"
      @select="selectPartner"
    />

    <span v-if="plot.task_snapshot && !ready" class="task-boost">
      能力 {{ plot.task_snapshot.total_ability }} · 品质 Q{{ plot.task_snapshot.quality_parameters.ability }} · 效率
      {{ plot.task_snapshot.time_efficiency.toFixed(2) }}×
    </span>

    <template v-if="ready">
      <p v-if="plot.task_results.length" class="ready-label harvest-list">
        <span v-for="(result, index) in plot.task_results" :key="`${index}:${result.item_id}:${result.quality}`">
          <QualityTag :quality="result.quality" /> {{ result.quantity }} 个
        </span>
      </p>
      <p v-else class="ready-label">已经成熟</p>
      <ActionButton class="harvest-button" :action-key="`plot:${plot.slot}:harvest`" @click="game.harvest(plot.slot)">
        收获
      </ActionButton>
    </template>
    <template v-else>
      <div class="plot-actions">
        <ActionButton
          v-for="item in activeItems"
          :key="item.id"
          variant="secondary"
          :action-key="`farming:${plot.slot}:item:${item.id}`"
          :disabled="plot.ready_at - game.serverNow > item.value || item.quantity < plot.size"
          reason="剩余时间还太长"
          @click="game.useActiveTaskItem('farming', plot.slot, item.id)"
        >{{ item.name }} · 消耗 {{ plot.size }} 份</ActionButton>
        <ActionButton
          variant="secondary"
          :action-key="`plot:${plot.slot}:cancel`"
          @click="cancelPlanting"
        >取消种植</ActionButton>
      </div>
      <ProgressBar class="plot-progress" :value="progress" :color="plot.crop?.accent" track="#111713" smooth />
      <span class="time-left"><Clock3 :size="14" /> {{ label }}</span>
    </template>
  </article>
</template>

<style scoped>
.task-boost { color: #91aa7d; font-size: 12px; margin-top: auto; margin-bottom: 10px; }
.harvest-list { display: flex; flex-wrap: wrap; gap: 4px 10px; justify-content: center; }
.plot-actions { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.plot-progress { width: 100%; margin-top: 10px; }
.plant-button {
  position: relative;
  width: 100%;
  min-height: 42px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  text-align: left;
  color: #dbe6d6;
  border: 1px solid rgba(135, 169, 107, .28);
  border-radius: 10px;
  background: rgba(135, 169, 107, .12);
  cursor: pointer;
}
.plant-button:disabled { color: #7e8a80; border-color: #ffffff12; background: #ffffff05; }
</style>
