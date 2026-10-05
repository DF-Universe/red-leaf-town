<script setup lang="ts">
import { computed, ref } from 'vue'
import ActionButton from '@/components/ActionButton.vue'
import ConstructionCost from '@/components/ConstructionCost.vue'
import PartnerPicker from '@/components/PartnerPicker.vue'
import { useGameStore } from '@/stores/game'
const props = defineProps<{ kind: 'farm' | 'feed' | 'refining' }>()
const game = useGameStore()
const retained = ref<string | null>(null)
const upgrade = computed(() => game.state?.facilities.upgrades.find(u => u.kind === props.kind))
const plots = computed(() => game.state?.plots.filter(p => upgrade.value && [2 * (upgrade.value.stage - 1), 2 * upgrade.value.stage - 1].includes(p.slot)) || [])
const candidates = computed(() => plots.value.flatMap(p => p.assigned_partners))
const blocked = computed(() => props.kind === 'farm' && plots.value.some(p => !p.empty))
const description = computed(() => props.kind === 'farm' ? `合并土地 ${2 * (upgrade.value?.stage || 1) - 1}、${2 * (upgrade.value?.stage || 1)}：同种同收，一位伙伴照看两份产出。需两块田均为空。` : props.kind === 'feed' ? '扩充共用饲料容量，降低鱼塘与畜牧的生产耗料。' : '一位加工伙伴照看六个精制格，精制不消耗体力。')
async function build() {
 if (!upgrade.value) return
 await game.upgradeFacility(upgrade.value.id, candidates.value.some(p => p.partner_id === retained.value) ? retained.value! : '')
 retained.value = null
}
</script>
<template>
 <article v-if="upgrade" class="surface-card facility-upgrade">
  <div><h3>{{ upgrade.name }}</h3><p>{{ description }}</p></div>
  <ConstructionCost :coins="upgrade.coins" :materials="upgrade.inputs" :coins-affordable="!upgrade.unlocked || (game.state?.player.coins || 0) >= upgrade.coins" />
  <PartnerPicker v-if="kind === 'farm' && candidates.length" industry="farming" :action-key="`facility:${upgrade.id}`" :candidates="candidates" :assigned="candidates.find(p => p.partner_id === retained)" placeholder="选择合并后保留的伙伴" solo-label="暂不派驻" @select="retained = $event" />
  <ActionButton :action-key="`facility:${upgrade.id}`" :disabled="!upgrade.unlocked || !upgrade.affordable || blocked" @click="build">{{ !upgrade.unlocked ? `居民 ${upgrade.min_level} 级开放（需完成前阶段）` : blocked ? '请先清空两块田' : !upgrade.affordable ? '材料或红叶币不足' : '建造 / 改良' }}</ActionButton>
 </article>
</template>
<style scoped>
.facility-upgrade { padding: 20px; margin-bottom: 20px; display: grid; gap: 12px; }
h3,p { margin: 0; } p { margin-top: 8px; color: var(--muted); font-size: 13px; line-height: 1.7; }
</style>
