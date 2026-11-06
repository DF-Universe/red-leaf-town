<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowRight, Check, CheckCircle2, Coins, Gem, Sparkles, TrendingUp } from 'lucide-vue-next'
import ActionButton from '@/components/ActionButton.vue'
import GameIcon from '@/components/GameIcon.vue'
import ModalSheet from '@/components/ModalSheet.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import QualityTag from '@/components/QualityTag.vue'
import { useGameStore } from '@/stores/game'
import type { OwnedPartner, PartnerAscensionState } from '@/types'

const props = defineProps<{ partner: OwnedPartner }>()
const game = useGameStore()
const open = ref(false)
const completed = ref<PartnerAscensionState | null>(null)
const ascension = computed(() => completed.value || props.partner.ascension)
const readyCount = computed(() => props.partner.ascension?.items.filter(item => item.owned >= item.quantity).length || 0)
const coinReady = computed(() => (game.player?.coins || 0) >= (ascension.value?.coins || 0))
const equipment = computed(() => ascension.value?.items.some(item => item.kind === 'equipment'))
const pending = computed(() => game.isPending(`partner:${props.partner.partner_id}:breakthrough`))
watch(() => props.partner.partner_id, () => { open.value = false; completed.value = null })

function show() { completed.value = null; open.value = true }
async function ascend() {
  const preview = props.partner.ascension
  if (!preview || !props.partner.breakthrough_available || pending.value) return
  const result = await game.breakthroughPartner(props.partner.partner_id)
  if (result) completed.value = preview
}
</script>

<template>
  <section v-if="partner.ascension" class="ascension-card">
    <div class="ascension-card-heading">
      <span class="crystal-mark"><Gem :size="23" /></span>
      <div><p class="ascension-eyebrow">新的成长篇章</p><h3>第 {{ partner.ascension.breakthrough }} 次突破</h3></div>
      <span class="cap-pill">Lv.{{ partner.level_cap }} <ArrowRight :size="12" /> {{ partner.ascension.level_cap }}</span>
    </div>
    <p class="card-description">解锁更高等级，迎接伙伴的新姿态。</p>
    <div class="material-glance">
      <span v-for="item in partner.ascension.items" :key="item.item_id" :class="{ ready: item.owned >= item.quantity }" :title="`${item.name}：${item.owned} / ${item.quantity}`">
        <GameIcon :name="item.icon" :size="23" /><span class="glance-copy"><strong>{{ item.name }}</strong><small>{{ item.min_quality === 3 ? '上品及以上' : '不限品质' }}</small></span><span class="glance-count">{{ item.owned }} / {{ item.quantity }}<Check v-if="item.owned >= item.quantity" :size="14" /></span>
      </span>
    </div>
    <div class="ascension-card-bottom">
      <small>{{ readyCount }}/{{ partner.ascension.items.length }} 项材料已备齐</small>
      <button class="primary-button review-button" @click="show">查看突破 <ArrowRight :size="15" /></button>
    </div>
  </section>
  <div v-else class="ascension-next"><Sparkles :size="17" /><span>{{ partner.breakthrough ? '下一阶段的故事，敬请期待' : '突破尚未开放' }}</span></div>

  <ModalSheet elevated :open="open" :title="completed ? `${partner.name} · 突破完成` : `${partner.name} · 伙伴突破`" :subtitle="completed ? '新的姿态，新的旅程' : '准备好心意，与伙伴一起迈向下一阶段'" size="wide" @close="open = false">
    <div v-if="ascension" class="ascension-layout" :class="{ completed: Boolean(completed) }">
      <div class="ascension-portrait-column">
        <div class="ascension-portrait">
          <img v-if="ascension.artwork?.url" :src="ascension.artwork.url" :alt="`${partner.name}的${ascension.breakthrough}破立绘`" />
          <div v-else class="portrait-placeholder"><Sparkles :size="36" /><span>新形态插画待补充</span></div>
          <span class="portrait-caption"><Sparkles :size="14" />{{ completed ? '已解锁' : '突破形态预览' }}</span>
        </div>
        <div class="portrait-copy"><p class="ascension-eyebrow">{{ completed ? '旅程再启' : '即将开启' }}</p><strong>{{ partner.name }}</strong><span>第 {{ ascension.breakthrough }} 次突破</span></div>
      </div>
      <div class="ascension-content">
        <div v-if="completed" class="success-message" role="status"><CheckCircle2 :size="25" /><div><strong>突破成功</strong><p>等级上限已提升至 {{ ascension.level_cap }} 级，积累的经验已继续结算。</p></div></div>
        <div class="ascension-benefit">
          <span class="benefit-icon"><TrendingUp :size="23" /></span>
          <div><small>等级上限提升</small><strong>{{ ascension.required_partner_level }} <ArrowRight :size="22" /> <em>{{ ascension.level_cap }}</em></strong></div>
          <span class="benefit-caption">继续培养<br />提升产业能力</span>
        </div>
        <div class="ability-preview">
          <span v-for="tendency in ascension.tendencies" :key="tendency.industry"><small>{{ tendency.name }}</small><strong>{{ tendency.current_ability }} <ArrowRight :size="12" /> {{ tendency.max_ability }}</strong></span>
        </div>
        <p class="ability-note">右侧为培养至 {{ ascension.level_cap }} 级时的伙伴能力，突破后随升级逐步获得。</p>
        <div v-for="trait in (partner.traits || []).filter(t => t.min_breakthrough === ascension?.breakthrough)" :key="trait.code" class="trait-unlock-preview">
          <strong>{{ trait.name }}</strong><small>突破 {{ trait.min_breakthrough }} 次解锁</small>
          <p>{{ trait.description }}</p>
        </div>
        <template v-if="!completed">
          <div class="requirements-heading"><h3>突破所需</h3><span>优先消耗最低合格品质</span></div>
          <div class="ascension-materials">
            <div v-for="(item, index) in ascension.items" :key="item.item_id" class="ascension-material" :class="{ enough: item.owned >= item.quantity }">
              <span class="material-icon" :class="{ core: index === 0 }"><GameIcon :name="item.icon" :size="25" /></span>
              <div class="material-copy"><small>{{ ['核心材料', '产业物产', '特色需求'][index] || '突破材料' }}</small><strong>{{ item.name }}</strong><span><QualityTag v-if="item.min_quality" :quality="item.min_quality" :name="item.min_quality === 3 ? '上品及以上' : undefined" /><small v-else>不限品质</small></span></div>
              <div class="material-count"><CheckCircle2 v-if="item.owned >= item.quantity" :size="16" /><strong>{{ item.owned }}<small> / {{ item.quantity }}</small></strong><span v-if="item.owned < item.quantity">还差 {{ item.quantity - item.owned }}</span><span v-else>已备齐</span></div>
              <ProgressBar class="material-progress" :value="Math.min(100, item.owned / item.quantity * 100)" :height="3" />
            </div>
          </div>
          <div class="ascension-coins" :class="{ enough: coinReady }"><span><Coins :size="18" />红叶币</span><strong>{{ (game.player?.coins || 0).toLocaleString() }} <small>/ {{ ascension.coins.toLocaleString() }}</small></strong></div>
          <p v-if="equipment" class="equipment-note">特色需求中的装备会被消耗，请预留队伍需要的武器。</p>
        </template>
      </div>
    </div>
    <template #footer>
      <div v-if="!completed" class="ascension-footer">
        <div><strong>{{ partner.breakthrough_available ? '一切就绪，准备突破' : partner.breakthrough_reason }}</strong><small>居民 Lv.{{ ascension?.min_player_level }} · 伙伴 Lv.{{ ascension?.required_partner_level }} · 消耗以上材料与红叶币</small></div>
        <ActionButton :action-key="`partner:${partner.partner_id}:breakthrough`" :group="`partner:${partner.partner_id}:`" :disabled="!partner.breakthrough_available" :reason="partner.breakthrough_reason || undefined" @click="ascend"><Sparkles :size="18" />{{ pending ? '正在突破…' : '确认突破' }}</ActionButton>
      </div>
      <button v-else class="primary-button completed-button" @click="open = false">继续同行 <ArrowRight :size="17" /></button>
    </template>
  </ModalSheet>
</template>

<style scoped>
.trait-unlock-preview { margin: 12px 0; padding: 12px; border-left: 2px solid var(--gold); background: #ffffff04; }
.trait-unlock-preview small { margin-left: 10px; color: var(--muted); }
.trait-unlock-preview p { margin: 6px 0 0; font-size: 12px; color: var(--muted); }

.ascension-card { margin-top: 24px; padding: 18px; border: 1px solid #c7af7045; border-radius: 18px 6px; background: radial-gradient(ellipse at top right, #bda05d18, transparent 75%), #ffffff03; }
.ascension-card-heading { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.crystal-mark { display: grid; place-items: center; width: 43px; height: 43px; flex-shrink: 0; color: #d7c58e; border: 1px solid #d7c58e38; border-radius: 13px; background: #d7c58e0c; }
.ascension-eyebrow { margin: 0 0 4px; color: #b6a477; font-size: 10px; letter-spacing: .14em; }
.ascension-card h3 { margin: 0; font-size: 16px; font-family: 'Noto Serif SC', serif; }
.cap-pill { margin-left: auto; display: flex; align-items: center; gap: 5px; color: #d8c796; font-size: 12px; }
.card-description { margin: 12px 0; color: #a4afa4; font-size: 12px; line-height: 1.6; }
.material-glance { display: grid; gap: 7px; }
.material-glance > span { display: flex; align-items: center; gap: 10px; padding: 10px; border: 1px solid var(--line); color: #b4beb2; background: #0002; border-radius: 9px; font-size: 12px; }
.glance-copy { flex: 1; }
.glance-copy strong, .glance-copy small { display: block; font-size: 12px; }
.glance-copy small { margin-top: 4px; color: #a4afa4; }
.glance-count { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.material-glance > .ready { color: var(--leaf-bright); }
.ascension-card-bottom { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 13px; }
.ascension-card-bottom small { color: #9aa799; font-size: 12px; }
.review-button { display: flex; align-items: center; gap: 8px; min-height: 44px; }
.ascension-next { display: flex; gap: 8px; margin-top: 22px; color: #9ba89b; font-size: 12px; }
.ascension-layout { display: grid; grid-template-columns: minmax(160px, 240px) minmax(0, 1fr); gap: 26px; padding: 8px; }
.ascension-portrait-column { align-self: start; }
.ascension-portrait { position: relative; overflow: hidden; aspect-ratio: 9 / 16; border: 1px solid #ccb77555; border-radius: 16px 5px; background: radial-gradient(ellipse at center, #4c5141, #18221c); box-shadow: 0 15px 40px #0004; }
.ascension-portrait img { display: block; width: 100%; height: 100%; object-fit: cover; }
.portrait-caption { position: absolute; bottom: 0; left: 0; right: 0; display: flex; align-items: center; justify-content: center; gap: 7px; padding: 32px 8px 14px; color: #eee4c8; background: linear-gradient(transparent, #09130df2); font-size: 12px; }
.portrait-placeholder { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; color: #c7b98d; font-size: 12px; }
.portrait-copy { margin-top: 18px; text-align: center; }
.portrait-copy > strong { display: block; font: 700 24px 'Noto Serif SC', serif; }
.portrait-copy > span { display: block; color: #a5b09f; margin-top: 6px; font-size: 12px; }
.ascension-content { min-width: 0; }
.ascension-benefit { display: flex; align-items: center; gap: 12px; padding: 16px; border: 1px solid #c6b16d33; border-radius: 12px; background: linear-gradient(115deg, #c6b16d14, #88a57708); }
.benefit-icon { color: #caba87; }
.ascension-benefit small { color: #bac3b3; font-size: 12px; }
.ascension-benefit strong { display: flex; align-items: center; gap: 11px; margin-top: 4px; font-size: 28px; font-variant-numeric: tabular-nums; }
.ascension-benefit em { color: #dfcf99; font-style: normal; }
.benefit-caption { margin-left: auto; color: #a5b29b; font-size: 11px; line-height: 1.8; }
.ability-preview { display: grid; grid-template-columns: repeat(auto-fit, minmax(85px, 1fr)); gap: 8px; margin-top: 12px; }
.ability-preview > span { padding: 9px; border-radius: 8px; background: #ffffff05; }
.ability-preview small { display: block; color: #a1ad9e; font-size: 11px; }
.ability-preview strong { display: flex; align-items: center; gap: 6px; margin-top: 5px; color: #c8d8b2; font-size: 13px; }
.ability-note { font-size: 11px; color: #9aa796; line-height: 1.6; }
.requirements-heading { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 6px; align-items: center; margin: 22px 0 10px; }
.requirements-heading h3 { margin: 0; font-size: 14px; }
.requirements-heading > span { color: #9aa796; font-size: 11px; }
.ascension-materials { display: grid; gap: 9px; }
.ascension-material { display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 12px 12px 8px; border: 1px solid var(--line); border-radius: 11px; background: #ffffff03; }
.ascension-material.enough { border-color: #9abb7840; }
.material-icon { display: grid; place-items: center; width: 42px; height: 48px; color: #b8c4ae; background: #ffffff05; border-radius: 10px; }
.material-icon.core { color: #c5b5ee; background: #ac8ddb16; }
.material-copy { min-width: 0; }
.material-copy > small { display: block; color: #9caa94; font-size: 10px; margin-bottom: 4px; }
.material-copy > strong { display: block; font-size: 13px; overflow-wrap: anywhere; }
.material-copy > span { display: block; margin-top: 5px; }
.material-copy > span > small { color: #9caa94; font-size: 11px; }
.material-count { text-align: right; font-variant-numeric: tabular-nums; }
.material-count > svg { color: var(--leaf-bright); }
.material-count > strong { display: block; margin-top: 3px; font-size: 15px; }
.material-count strong small { color: #a9b5a1; font-size: 11px; font-weight: normal; }
.material-count > span { display: block; margin-top: 4px; font-size: 10px; color: #c9b49c; }
.enough .material-count > span { color: var(--leaf-bright); }
.material-progress { grid-column: 1 / -1; margin-top: 2px; }
.ascension-coins { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; margin: 14px 0; font-size: 13px; }
.ascension-coins > span { display: flex; align-items: center; gap: 7px; color: #d4bd7d; }
.ascension-coins small { color: #a5b19b; font-size: 12px; }
.ascension-coins.enough > strong { color: var(--leaf-bright); }
.equipment-note { font-size: 11px; line-height: 1.6; color: #cfb88e; margin-bottom: 0; }
.ascension-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.ascension-footer > div { min-width: 0; }
.ascension-footer strong { display: block; font-size: 13px; }
.ascension-footer small { display: block; margin-top: 5px; color: #a5b29c; font-size: 11px; line-height: 1.5; }
.ascension-footer :deep(button), .completed-button { display: flex; align-items: center; justify-content: center; gap: 8px; min-height: 46px; flex-shrink: 0; }
.completed-button { width: 100%; }
.success-message { display: flex; align-items: start; gap: 12px; margin-bottom: 18px; color: var(--leaf-bright); }
.success-message svg { flex-shrink: 0; }
.success-message strong { font: 700 21px 'Noto Serif SC', serif; }
.success-message p { margin: 7px 0 0; color: #b9c5ad; font-size: 13px; line-height: 1.7; }
.completed .ascension-portrait { animation: ascension-glow 1.2s ease-out; }
@keyframes ascension-glow { from { box-shadow: 0 0 45px #d3bd8277; } to { box-shadow: 0 15px 40px #0004; } }
@media (max-width: 600px) {
  .ascension-layout { grid-template-columns: minmax(0, 1fr); gap: 18px; padding: 2px; }
  .ascension-portrait-column { display: grid; grid-template-columns: 100px minmax(0, 1fr); align-items: center; gap: 20px; }
  .portrait-copy { text-align: left; margin: 0; }
  .portrait-caption { font-size: 10px; gap: 3px; padding-bottom: 9px; }
  .portrait-caption > svg { display: none; }
  .portrait-placeholder { font-size: 10px; text-align: center; padding: 6px; }
  .ascension-footer { flex-direction: column; align-items: stretch; gap: 10px; }
  .ascension-footer :deep(button) { width: 100%; }
  .ascension-benefit { padding: 12px; }
  .material-count > strong { font-size: 14px; }
}
@media (prefers-reduced-motion: reduce) { .completed .ascension-portrait { animation: none; } }
</style>
