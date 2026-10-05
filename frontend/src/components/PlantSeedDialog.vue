<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, Clock3 } from 'lucide-vue-next'

import ActionButton from '@/components/ActionButton.vue'
import CostChip from '@/components/CostChip.vue'
import GameIcon from '@/components/GameIcon.vue'
import ItemTile from '@/components/ItemTile.vue'
import ModalSheet from '@/components/ModalSheet.vue'
import PartnerPicker from '@/components/PartnerPicker.vue'
import TaskItemSelect from '@/components/TaskItemSelect.vue'
import { usePartnerRoster } from '@/composables/usePartnerRoster'
import { formatDuration } from '@/lib/format'
import { estimateDuration, taskItemDurationMultiplier } from '@/lib/production'
import { useGameStore } from '@/stores/game'
import type { CropDefinition, PlotState } from '@/types'

const props = defineProps<{ open: boolean; plot: PlotState; crops: CropDefinition[] }>()
const emit = defineEmits<{ (event: 'close'): void }>()

const game = useGameStore()
const { ability } = usePartnerRoster('farming')
const selectedId = ref<string | null>(null)
const taskItemId = ref('')

const assignedPartner = computed(() => props.plot.assigned_partners[0] || null)
const selected = computed(() => props.crops.find((crop) => crop.id === selectedId.value) || null)
const size = computed(() => props.plot.size || 1)
const affordable = computed(() => Boolean(selected.value) && seedCount(selected.value!) >= size.value && game.liveStamina >= (selected.value?.stamina_cost || 0) * size.value && (!taskItemId.value || (game.state?.task_items.find(i => i.id === taskItemId.value)?.quantity || 0) >= size.value))

watch(
  () => props.open,
  (open) => {
    if (open) selectedId.value = props.crops.length === 1 ? props.crops[0].id : null
    if (open) taskItemId.value = ''
  },
  { immediate: true },
)

function seedCount(crop: CropDefinition) {
  return game.inventoryMap.get(crop.seed_item_id)?.quantity || 0
}

function estimatedDuration(crop: CropDefinition) {
  const baseAbility = game.state?.industry_rules.farming?.character_base_ability || 0
  return estimateDuration(
    crop.growth_seconds,
    crop.time_difficulty,
    baseAbility + ability(assignedPartner.value),
    1,
    taskItemDurationMultiplier(game.state?.task_items, taskItemId.value),
  )
}

function selectPartner(partnerId: string | null) {
  game.assignPartner(props.plot.slot, partnerId)
}

async function confirmPlant() {
  const crop = selected.value
  if (!crop) return
  const result = await game.plant(props.plot.slot, crop.id, taskItemId.value)
  if (result) emit('close')
}
</script>

<template>
  <ModalSheet
    :open="open"
    title="选择种子"
    :subtitle="size > 1 ? `双倍田 ${plot.slot + 1}＋${plot.slot + 2} · 同时种植，独立结算两份收获` : `土地 ${plot.slot + 1}`"
    @close="emit('close')"
  >
    <template #toolbar>
      <p class="toolbar-label">驻场伙伴</p>
      <PartnerPicker
        class="dialog-partner"
        industry="farming"
        :action-key="`plot:${plot.slot}:partner`"
        :assigned="assignedPartner"
        placeholder="安排伙伴"
        solo-label="不安排伙伴"
        dialog-title="选择驻场伙伴"
        elevated
        @select="selectPartner"
      />
    </template>

    <div class="seed-list">
      <button
        v-for="crop in crops"
        :key="crop.id"
        class="seed-option"
        :class="{ current: crop.id === selectedId }"
        :disabled="seedCount(crop) < size"
        :style="{ '--crop-accent': crop.accent }"
        @click="selectedId = crop.id"
      >
        <ItemTile :size="44" :accent="crop.accent">
          <GameIcon :name="crop.icon" :size="24" />
        </ItemTile>
        <span class="seed-copy">
          <strong>{{ crop.name }}<i>×{{ seedCount(crop) }}</i></strong>
          <small>
            <CostChip v-if="crop.stamina_cost" kind="stamina" :amount="crop.stamina_cost * size" :affordable="game.liveStamina >= crop.stamina_cost * size" signed />
            <span v-else class="free-cost">无需体力</span>
            · <Clock3 :size="12" /> {{ formatDuration(estimatedDuration(crop)) }}
          </small>
        </span>
        <Check v-if="crop.id === selectedId" :size="16" class="seed-check" />
      </button>

      <p v-if="!crops.length" class="seed-empty">仓库里没有可用种子</p>
    </div>

    <TaskItemSelect v-model="taskItemId" industry="farming" :quantity="size" elevated />

    <template #footer>
      <p class="footer-summary">
        <template v-if="selected">
          将在土地 {{ plot.slot + 1 }} 种下<strong>{{ selected.name }}</strong> ·
          {{ size }} 份种子 · {{ selected.stamina_cost ? `消耗 ${selected.stamina_cost * size} 体力` : '无需体力' }} ·
          约 {{ formatDuration(estimatedDuration(selected)) }}
        </template>
        <template v-else>先选择一种种子</template>
      </p>
      <div class="footer-actions">
        <button class="secondary-button" @click="emit('close')">取消</button>
        <ActionButton
          :action-key="`plot:${plot.slot}:plant:${selectedId || 'none'}`"
          :group="`plot:${plot.slot}:plant`"
          :disabled="!selected || !affordable"
          :reason="selected && !affordable ? '种子、体力或道具不足' : undefined"
          @click="confirmPlant"
        >确认种植</ActionButton>
      </div>
    </template>
  </ModalSheet>
</template>

<style scoped>
.toolbar-label { margin: 0; color: #77837a; font-size: 12px; }
.dialog-partner { margin: 6px 0 0; }
.seed-list { display: grid; gap: 7px; }
.seed-option {
  --crop-accent: #8ead71;
  width: 100%;
  min-height: 62px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  text-align: left;
  color: #d9e1d7;
  border: 1px solid transparent;
  border-radius: 13px;
  background: #ffffff05;
  cursor: pointer;
}
.seed-option:hover { background: #ffffff0b; }
.seed-option.current { border-color: color-mix(in srgb, var(--crop-accent) 48%, transparent); background: color-mix(in srgb, var(--crop-accent) 11%, transparent); }
.seed-copy { min-width: 0; }
.seed-copy strong { display: block; font-size: 14px; }
.seed-copy strong i { margin-left: 7px; color: var(--gold); font-size: 12px; font-style: normal; }
.seed-copy small { display: flex; align-items: center; gap: 4px; margin-top: 5px; color: #808f81; font-size: 12px; }
.free-cost { color: var(--leaf-bright); }
.seed-check { color: var(--leaf-bright); }
.seed-empty { padding: 22px 10px; margin: 0; text-align: center; color: #7b877d; font-size: 12px; }
.footer-summary { margin: 0 0 10px; color: #9aa59c; font-size: 12px; line-height: 1.6; }
.footer-summary strong { color: var(--leaf-bright); margin: 0 3px; }
.footer-actions { display: grid; grid-template-columns: 1fr 1.4fr; gap: 9px; }
.footer-actions :deep(.primary-button) { width: 100%; min-height: 42px; }
.footer-actions .secondary-button { min-height: 42px; }
</style>
