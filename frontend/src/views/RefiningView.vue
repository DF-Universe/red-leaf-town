<script setup lang="ts">
import { computed, ref } from 'vue'
import { Sparkles } from 'lucide-vue-next'
import ViewHeader from '@/components/ViewHeader.vue'
import ActionButton from '@/components/ActionButton.vue'
import FacilityUpgradePanel from '@/components/FacilityUpgradePanel.vue'
import PartnerPicker from '@/components/PartnerPicker.vue'
import ItemSelect from '@/components/ItemSelect.vue'
import GameIcon from '@/components/GameIcon.vue'
import ModalSheet from '@/components/ModalSheet.vue'
import QualityTag from '@/components/QualityTag.vue'
import { QUALITY_NAMES } from '@/lib/quality'
import { useGameStore } from '@/stores/game'
import type { RefiningTask } from '@/types'
const game = useGameStore()
const room = computed(() => game.state?.facilities.refining)
const chosenSlot = ref<number | null>(null)
const recipeId = ref('grape_wine')
const rawQuality = ref(1)
const recipe = computed(() => room.value?.recipes.find(r => r.id === recipeId.value))
const option = computed(() => recipe.value?.options.find(o => o.quality === rawQuality.value))
const names = ['未成品', ...QUALITY_NAMES.slice(1, 5)]
const recipeOptions = computed(() => (room.value?.recipes || []).map(r => ({
 value: r.id, label: r.item.name, icon: r.item.icon, disabled: !r.unlocked,
 description: r.unlocked ? `${r.input_item.name} × ${r.input_quantity} → ${r.item.name} × 1` : `居民 ${r.min_level} 级开放`,
})))
const qualityOptions = computed(() => (recipe.value?.options || []).map(o => ({
 value: String(o.quality), label: names[o.quality], quality: o.quality, icon: recipe.value?.input_item.icon,
 description: `持有 ${o.owned} · 每批需要 ${recipe.value?.input_quantity || 0}`,
})))
const quality = (task: RefiningTask) => task.quality_times.filter(at => at <= game.serverNow).length
const duration = (seconds: number) => { const m = Math.max(0, Math.ceil(seconds / 60)); return m >= 60 ? `${Math.floor(m / 60)} 小时 ${m % 60} 分` : `${m} 分钟` }
const nextLabel = (task: RefiningTask) => { const q = quality(task); return q === task.max_quality ? '已达本批品质上限' : `${duration(task.quality_times[q] - game.serverNow)}后达到${names[q + 1]}` }
async function start() {
 if (chosenSlot.value === null || !recipe.value) return
 const result = await game.startRefining(chosenSlot.value, recipe.value.id, rawQuality.value)
 if (result) chosenSlot.value = null
}
</script>
<template>
 <section v-if="room && game.state" class="view-section">
  <ViewHeader eyebrow="TIME & CRAFT" title="精制房"><template #chip><Sparkles :size="18" /> 共用加工编制 {{ game.state.industry_rules.crafting?.partner_capacity }}</template></ViewHeader>
  <FacilityUpgradePanel kind="refining" />
  <template v-if="room.built">
   <article class="surface-card room-summary">
    <h3>交给时间，慢慢精制</h3>
    <p>六格独立运转，不消耗体力。达到普通品质后可随时收取，继续等待可提升品质。所有格子清空后才能更换伙伴。</p>
    <PartnerPicker industry="crafting" action-key="refining:partner" :assigned="room.assigned_partner" :locked="room.locked" locked-label="清空全部精制格后可换人" placeholder="安排一位加工伙伴" @select="game.assignRefiningPartner" />
    <p v-if="!game.state.talents.nodes.find(n => n.id === 'crafting_roster_1')?.unlocked">需要同时派驻工坊与精制房时，可在总览中点亮加工天赋「工坊统筹」增加编制。</p>
    <p>加工能力 {{ room.ability }} · 新批次品质上限：{{ names[room.max_quality] }}。伙伴按房间实际工作时长获得经验，多格不会重复计时。</p>
   </article>
   <div class="refining-grid">
    <article v-for="(task, slot) in room.slots" :key="slot" class="surface-card refining-slot">
     <small>精制格 {{ slot + 1 }}</small>
     <template v-if="task">
      <h3><GameIcon :name="task.item.icon" :size="24" />{{ task.item.name }}</h3>
      <QualityTag v-if="quality(task)" :quality="quality(task)" /><strong v-else>未成品</strong><p>{{ nextLabel(task) }}</p>
      <div class="steps"><span v-for="(at, q) in task.quality_times" :key="q" :class="{ reached: at <= game.serverNow }">{{ names[q + 1] }}</span></div>
      <ActionButton :action-key="`refining:${slot}`" @click="game.finishRefining(slot, task.task_id, quality(task) === 0)">{{ quality(task) ? '按当前品质收取' : '取消并退回原料' }}</ActionButton>
      <small>{{ quality(task) ? '收取后本格停止精制' : '普通品质完成前取消，原材料原样返还' }}</small>
     </template>
     <template v-else><h3>等待投入</h3><p>同一品质的原料可放入一批，奇迹原料不可投入。</p><ActionButton :action-key="`refining:${slot}`" :disabled="!room.assigned_partner" @click="chosenSlot = slot">{{ room.assigned_partner ? '选择物产' : '请先安排伙伴' }}</ActionButton></template>
    </article>
   </div>
  </template>
  <ModalSheet :open="chosenSlot !== null" title="投入精制" subtitle="高品质原料精制更快；品质上限在开工时确定" @close="chosenSlot = null">
   <div class="refining-form">
    <ItemSelect v-model="recipeId" label="物产" :options="recipeOptions" elevated />
    <ItemSelect :model-value="String(rawQuality)" label="原料品质" :options="qualityOptions" elevated @update:model-value="rawQuality = Number($event)" />
    <p v-if="recipe">消耗 {{ recipe.input_item.name }} × {{ recipe.input_quantity }}，产出 {{ recipe.item.name }} × 1</p>
    <table v-if="recipe && option"><thead><tr><th>产物品质</th><th>从投入起等待</th><th>售价 / 净增值</th></tr></thead><tbody><tr v-for="(at, q) in option.quality_times" :key="q"><td>{{ names[q + 1] }}</td><td>{{ duration(at - game.state.server_time) }}</td><td>{{ recipe.output_prices[q] }} / <span :style="{ color: recipe.output_prices[q] < option.input_value ? 'var(--danger)' : 'var(--leaf-bright)' }">{{ recipe.output_prices[q] - option.input_value }}</span></td></tr></tbody></table>
    <div class="refining-notes">
     <p>净增值已扣除原料售价；过早收取可能低于原料价值。</p>
     <p>加工伙伴特性与加工减时天赋不作用于精制。</p>
    </div>
   </div>
   <template #footer><ActionButton :action-key="`refining:${chosenSlot}`" :disabled="!recipe?.unlocked || !option || option.owned < (recipe?.input_quantity || 0)" @click="start">{{ option && recipe && option.owned < recipe.input_quantity ? '该品质原料不足' : '开始精制' }}</ActionButton></template>
  </ModalSheet>
 </section>
</template>
<style scoped>
.room-summary { padding: 20px; margin-bottom: 20px; } h3 { margin: 0; display: flex; align-items: center; gap: 8px; }
p,small { color: var(--muted); line-height: 1.7; } p { font-size: 13px; }
.refining-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 16px; }
.refining-slot { padding: 20px; display: flex; flex-direction: column; gap: 12px; } .refining-slot p { flex: 1; margin: 0; }
.refining-slot :deep(.quality-tag) { align-self: flex-start; }
.steps { display: flex; gap: 6px; } .steps span { flex: 1; font-size: 11px; border-bottom: 3px solid var(--line); padding-bottom: 8px; color: var(--muted); } .steps .reached { color: var(--leaf-bright); border-color: var(--leaf-bright); }
.refining-form { display: grid; gap: 12px; }
.refining-form p { margin: 0; color: color-mix(in srgb, var(--muted) 80%, var(--surface)); font-size: 12px; line-height: 1.5; }
.refining-form table { width: 100%; border-collapse: collapse; color: var(--muted); }
.refining-form th, .refining-form td { text-align: left; padding: 7px 2px; font-size: 12px; line-height: 1.45; }
.refining-form th { color: var(--muted); font-size: 11px; font-weight: 500; border-bottom: 1px solid var(--line); }
.refining-notes { display: grid; gap: 4px; }
@media(max-width:1000px) { .refining-grid { grid-template-columns: repeat(2,minmax(0,1fr)); } } @media(max-width:520px) { .refining-grid { grid-template-columns: 1fr; } }
</style>
