<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Anchor, Backpack, BookOpen, Check, ChevronRight, Clock, Coins, Compass, Fish, Package, Sailboat, UsersRound, Waves, Zap } from 'lucide-vue-next'
import ActionButton from '@/components/ActionButton.vue'
import ModalSheet from '@/components/ModalSheet.vue'
import PartnerPicker from '@/components/PartnerPicker.vue'
import ProgressBar from '@/components/ProgressBar.vue'
import StateBlock from '@/components/StateBlock.vue'
import { useCountdown } from '@/composables/useCountdown'
import { formatDuration } from '@/lib/format'
import { useGameStore } from '@/stores/game'
import { useUiStore } from '@/stores/ui'
import type { OwnedPartner } from '@/types'

const game = useGameStore()
const ui = useUiStore()
const sailing = computed(() => game.state?.sailing)
const selectedRoute = ref('reed_bay')
const selectedSupply = ref('none')
const slots = ref(['', '', ''])
const party = computed(() => slots.value.filter(Boolean))
const requestId = ref(crypto.randomUUID())
const codexOpen = ref(false)
const supplyOpen = ref(false)
const produceOpen = ref(false)
const route = computed(() => sailing.value?.routes.find(r => r.id === selectedRoute.value))
const supply = computed(() => sailing.value?.supplies.find(s => s.id === selectedSupply.value))
const industry = computed(() => (route.value?.required_voyages || 0) >= 3 ? 'exploration' : 'aquatic')
const recorded = computed(() => sailing.value?.collection.filter(item => item.quantity > 0).length || 0)
const run = computed(() => sailing.value?.active_run)
const journal = computed(() => run.value ? (elapsed.value ? run.value : null) : sailing.value?.last_run)
const { label, progress, elapsed } = useCountdown(() => run.value?.ready_at, () => run.value ? run.value.ready_at - run.value.started_at : 0)
const attributeNames: Record<string, string> = { strength: '力量', agility: '敏捷', intelligence: '智力', luck: '幸运' }
const rarityNames = { common: '普通海产', rare: '稀有物产', seed: '树果种子', equipment: '探索装备' }
const candidates = computed(() => (game.state?.partners || []).filter(partner => !unavailable(partner)))
const blockedReason = computed(() => {
  if (!sailing.value?.ship_built) return '请先建造初帆号'
  if (!route.value?.unlocked) return '这条航线尚未开放'
  if (!party.value.length) return '请至少选择一名伙伴'
  if (party.value.some(id => unavailable(partnerById(id)))) return '队伍中有忙碌的伙伴'
  if ((game.player?.coins || 0) < route.value.coins) return '红叶币不足'
  if (game.liveStamina < route.value.stamina) return '体力不足'
  if (supply.value && supply.value.owned < supply.value.quantity) return '额外补给不足'
  return ''
})

function partnerById(id: string) {
  return game.state?.partners.find(partner => partner.partner_id === id)
}

function unavailable(partner: OwnedPartner | undefined) {
  return !partner || partner.missing || (partner.locked && (partner.locked_until || 0) > game.serverNow)
    || Boolean(game.state?.exploration.active_run?.partner_ids.includes(partner.partner_id))
}

function selectPartner(index: number, id: string | null) {
  if (id && (unavailable(partnerById(id)) || slots.value.some((value, slot) => slot !== index && value === id))) return
  slots.value = slots.value.map((value, slot) => slot === index ? id || '' : value)
}

watch([selectedRoute, selectedSupply, slots], () => { requestId.value = crypto.randomUUID() })

async function cancelVoyage() {
  const voyage = run.value
  if (!voyage || elapsed.value) return
  const accepted = await ui.confirm({
    title: '取消这次航行？',
    description: `将返还 ${voyage.coins} 红叶币及实际消耗的补给（保留原品质），${voyage.stamina} 点体力按上限返还。伙伴立即空闲，本次不获得收获、经验，也不计入完成航次。`,
    confirmLabel: '取消航行',
    cancelLabel: '继续航行',
    tone: 'danger',
  })
  if (accepted) await game.cancelSailing(voyage.run_id)
}

async function start() {
  const result = await game.startSailing(selectedRoute.value, party.value, selectedSupply.value, requestId.value)
  if (result) requestId.value = crypto.randomUUID()
}
</script>

<template>
  <section class="sailing-view" aria-label="旧港出海">
    <StateBlock v-if="!sailing" title="旧港尚未开放" description="暂时无法获取出海状态，请稍后重试。" />
    <StateBlock v-else-if="!sailing.unlocked" :title="`居民 ${sailing.min_level} 级开放出海`" description="码头已备好第一艘船，达到等级后即可试航。" />
    <template v-else>
      <header class="sailing-heading">
        <div><h2><Sailboat :size="18" /> 出海</h2><p>安排伙伴、带上补给，回港后领取海产与航海见闻。</p></div>
        <button class="codex-button" @click="codexOpen = true"><BookOpen :size="15" /> 航海见闻册 {{ recorded }}/{{ sailing.collection.length }}</button>
      </header>

      <article class="ship-card surface-card">
        <div class="ship-heading">
          <span class="ship-icon"><Sailboat :size="24" /></span>
          <div><h3 class="ui-section-title">初帆号</h3><p class="ui-description">{{ !sailing.ship_built ? '尚未建造 · 备好材料，开启首次航行' : `${sailing.completed_voyages} 次归航 · ${run ? (elapsed ? '已回港，等待领取' : `正在驶向${run.route_name}`) : '准备离港'}` }}</p></div>
          <span v-if="sailing.ship_built" class="ui-label ship-upgrade-label"><Anchor :size="14" /> 船舶改装</span>
        </div>
        <div v-if="!sailing.ship_built" class="construction-panel">
          <p class="ui-description">建造需要 {{ sailing.construction.coins }} 红叶币。</p>
          <p v-for="material in sailing.construction.materials" :key="material.item_id" class="ui-description">
            {{ material.item_name }} ×{{ material.quantity }}（持有 {{ material.owned }} 个）
          </p>
          <div class="construction-actions">
            <ActionButton action-key="sailing:build" group="sailing:" :disabled="(game.player?.coins || 0) < sailing.construction.coins || sailing.construction.materials.some(material => material.owned < material.quantity)" @click="game.buildSailingShip()">建造初帆号</ActionButton>
            <RouterLink class="text-button" to="/crafting">前往镇民工坊<ChevronRight :size="14" /></RouterLink>
          </div>
        </div>
        <div v-else class="upgrade-grid">
          <div v-for="upgrade in sailing.upgrades" :key="upgrade.kind" class="upgrade-row">
            <div><strong>{{ upgrade.name }} Lv.{{ upgrade.level }}</strong><p>{{ upgrade.description }}</p><small v-if="upgrade.level < 3">{{ upgrade.coins }} 红叶币 · {{ upgrade.item_name }} {{ upgrade.owned }}/{{ upgrade.quantity }}</small></div>
            <ActionButton :action-key="`sailing:upgrade:${upgrade.kind}`" group="sailing:" variant="secondary" :disabled="Boolean(run) || upgrade.level >= 3 || (game.player?.coins || 0) < upgrade.coins || upgrade.owned < upgrade.quantity" @click="game.upgradeSailing(upgrade.kind, upgrade.level)">{{ upgrade.level >= 3 ? '已满级' : '改装' }}</ActionButton>
          </div>
        </div>
        <p v-if="run" class="ui-description">回港领取收获后可以改装。</p>
      </article>

      <article v-if="run" class="voyage-status surface-card" aria-live="polite">
        <div class="panel-heading"><h3 class="ui-section-title"><Waves :size="18" /> {{ run.route_name }}{{ run.trial ? ' · 首次试航' : '' }}</h3><strong>{{ elapsed ? '已回港' : label }}</strong></div>
        <ProgressBar :value="progress" color="#80c8cb" :height="8" />
        <p class="ui-description">{{ run.partner_ids.map(id => partnerById(id)?.name || id).join('、') }} · 出航能力 {{ run.ability }}</p>
        <div class="voyage-actions">
          <ActionButton action-key="sailing:collect" group="sailing:" :disabled="!elapsed" @click="game.collectSailing(run.run_id)">{{ elapsed ? '领取航海收获' : '伙伴正在航行' }}</ActionButton>
          <ActionButton v-if="!elapsed" action-key="sailing:cancel" group="sailing:" variant="secondary" @click="cancelVoyage"><Anchor :size="15" />取消航行</ActionButton>
        </div>
      </article>

      <template v-else-if="sailing.ship_built">
        <p v-if="sailing.trial_available" class="trial-note"><Compass :size="16" /> 首次试航 60 秒，仅需 20 红叶币、1 体力。</p>
        <div class="route-tabs" aria-label="选择航线">
          <button v-for="r in sailing.routes" :key="r.id" class="route-tab" :class="{ selected: selectedRoute === r.id, locked: !r.unlocked }" :aria-pressed="selectedRoute === r.id" @click="selectedRoute = r.id">
            <small>出海 · {{ formatDuration(r.duration) }}</small><strong>{{ r.name }}</strong>
            <span>{{ r.unlocked ? `${r.coins} 红叶币 · ${r.stamina} 体力` : `完成 ${r.required_voyages} 次航行开放` }}</span>
          </button>
        </div>
        <article v-if="route" class="expedition-card surface-card">
          <div class="expedition-heading">
            <span class="expedition-icon"><Compass :size="28" /></span>
            <div><small class="ui-kicker">出海航线</small><h3 class="ui-section-title">{{ route.name }}</h3><p class="ui-description">{{ route.description }}</p></div>
          </div>
          <div class="route-facts">
            <span><Coins :size="16" /> {{ route.coins }} 红叶币</span><span><Zap :size="16" /> {{ route.stamina }} 体力</span><span><Clock :size="16" /> {{ formatDuration(route.duration) }}</span>
            <button class="codex-button" @click="produceOpen = true"><Fish :size="15" /> 航线物产 {{ route.outputs.length }} 种<ChevronRight :size="14" /></button>
          </div>
          <div class="party-panel">
            <h3 class="ui-section-title"><UsersRound :size="18" /> 组织队伍 <small>{{ party.length }}/3</small></h3>
            <p class="ui-description">至少安排一名伙伴。{{ industry === 'aquatic' ? '水产' : '探索' }}伙伴擅长本航线，其余伙伴也可参与检定；出航会撤下原有驻场安排。</p>
            <div class="party-selects">
              <section v-for="(_, index) in slots" :key="index" class="party-slot">
                <span class="ui-label">伙伴 {{ index + 1 }}{{ index ? ' · 可选' : '' }}</span>
                <PartnerPicker :industry="industry" :action-key="`sailing:party:${index}`" :assigned="partnerById(slots[index] || '')" :candidates="candidates" :excluded-partner-ids="slots.filter((id, slot) => Boolean(id) && slot !== index)" :placeholder="index ? '暂不安排' : '选择出海伙伴'" solo-label="暂不安排" dialog-title="选择出海伙伴" :clearable="true" elevated @select="id => selectPartner(index, id)" />
              </section>
            </div>
          </div>
          <div class="supply-panel">
            <h3 class="ui-section-title"><Backpack :size="18" /> 补给准备</h3>
            <button class="pack-trigger" :class="{ packed: selectedSupply !== 'none' }" @click="supplyOpen = true">
              <span class="trigger-icon"><Backpack :size="19" /></span><span class="trigger-copy"><strong>{{ supply?.name || '基础补给' }}</strong><small>{{ supply?.quantity ? `${supply.item_name} ×${supply.quantity} · 持有 ${supply.owned} · 优先使用低品质` : '船上自备，无需额外物品' }}</small></span><ChevronRight :size="16" />
            </button>
            <p class="ui-description">{{ supply?.description }}</p>
          </div>
          <ActionButton class="start-button" action-key="sailing:start" group="sailing:" :disabled="Boolean(blockedReason)" :reason="blockedReason" @click="start"><Sailboat :size="17" /> 支付 {{ route.coins }} 红叶币、{{ route.stamina }} 体力并出航</ActionButton>
          <p v-if="blockedReason" class="warning">{{ blockedReason }}</p>
        </article>
      </template>

      <details v-if="journal" class="journal-panel surface-card" :open="Boolean(run && elapsed)">
        <summary>{{ run ? '本次航海日志' : '上次航海日志' }} · {{ journal.route_name }}</summary>
        <div class="sailing-loot"><span v-for="drop in journal.drops" :key="`${drop.item_id}:${drop.quality}`" :style="drop.quality ? { color: `var(--quality-${drop.quality})` } : undefined"><Package :size="14" /> {{ drop.quality_name }}{{ drop.name }} ×{{ drop.quantity }}</span><span>居民经验 ＋{{ journal.experience }}</span></div>
        <ol class="journal-list"><li v-for="log in journal.logs" :key="log.event_id"><strong>{{ log.name }}</strong><p>{{ log.text }}</p><small>{{ partnerById(log.actor_id)?.name || log.actor_id }} · {{ attributeNames[log.attribute] }}检定 {{ log.roll }} {{ log.modifier >= 0 ? '+' : '−' }} {{ Math.abs(log.modifier) }} · {{ log.success ? '成功' : '未成功' }}</small></li></ol>
      </details>

      <ModalSheet :open="codexOpen" title="航海见闻册" :subtitle="`已记录 ${recorded} / ${sailing.collection.length} 种物产 · ${sailing.discoveries.filter(d => d.discovered).length} / ${sailing.discoveries.length} 种见闻`" @close="codexOpen = false">
        <div class="discovery-list"><span v-for="entry in sailing.discoveries" :key="entry.id" :class="{ discovered: entry.discovered }">{{ entry.discovered ? '✓' : '◇' }} {{ entry.name }}</span></div>
        <ul class="codex-list"><li v-for="item in sailing.collection" :key="item.item_id" :class="{ unknown: !item.quantity }"><Fish :size="20" /><div><strong>{{ item.quantity ? item.name : '？？？' }}</strong><small>{{ item.quantity ? `累计带回 ${item.quantity}` : '尚未发现' }}</small></div></li></ul>
      </ModalSheet>
      <ModalSheet :open="supplyOpen" title="准备出海补给" subtitle="选择一种额外补给，出发时优先消耗低品质物品。" @close="supplyOpen = false">
        <div class="supply-list"><button v-for="s in sailing.supplies" :key="s.id" class="supply-option" :class="{ selected: selectedSupply === s.id }" :disabled="s.owned < s.quantity" :aria-pressed="selectedSupply === s.id" @click="selectedSupply = s.id; supplyOpen = false"><Backpack :size="20" /><div><strong>{{ s.name }}</strong><p>{{ s.description }}</p><small v-if="s.quantity">{{ s.item_name }} {{ s.owned }}/{{ s.quantity }}{{ s.owned < s.quantity ? ' · 数量不足' : '' }}</small></div><Check v-if="selectedSupply === s.id" :size="18" /></button></div>
      </ModalSheet>
      <ModalSheet :open="produceOpen" :title="`${route?.name || ''}物产`" subtitle="每航次多次加权抽取，伙伴与补给可增加收获机会。" @close="produceOpen = false">
        <p class="ui-description">树果种子带回后消耗体力种植；本航线探索装备首次获得平均需要 {{ route?.equipment_expected_stamina }} 体力，并非保底。</p>
        <ul class="codex-list produce-list"><li v-for="item in route?.outputs" :key="item.item_id"><Package :size="20" /><div><strong>{{ item.name }}</strong><small>{{ rarityNames[item.rarity] }}</small></div></li></ul>
      </ModalSheet>
    </template>
  </section>
</template>

<style scoped>
.voyage-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.voyage-actions > :first-child { flex: 1 1 180px; }
.voyage-actions > * { min-height: 42px; }
@media (max-width: 420px) { .voyage-actions > * { flex: 1 1 100%; } }
.sailing-view { --expedition-accent: #80b6b0; display: grid; gap: 16px; min-width: 0; }
.sailing-heading { display: flex; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
.sailing-heading h2 { display: flex; align-items: center; gap: 8px; margin: 0; font: 600 18px Georgia, 'Noto Serif SC', serif; }
.sailing-heading p { margin: 6px 0 0; color: #849087; line-height: 1.6; font-size: 13px; }
.codex-button { display: inline-flex; align-items: center; gap: 7px; min-height: 34px; padding: 0 12px; color: #b6c3b6; border: 1px solid var(--line); border-radius: 99px; background: #ffffff05; cursor: pointer; white-space: nowrap; }
.ship-card, .expedition-card, .voyage-status, .journal-panel { min-width: 0; padding: 20px; }
.ship-heading { display: flex; align-items: center; gap: 12px; }
.ship-heading h3 { margin: 0 0 4px; }
.ship-icon { display: grid; place-items: center; flex: 0 0 42px; height: 42px; color: var(--expedition-accent); background: #80b6b012; border-radius: 12px 4px; }
.ship-upgrade-label { display: inline-flex; align-items: center; gap: 5px; margin-left: auto; white-space: nowrap; }
.upgrade-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 14px; }
.construction-panel { display: grid; gap: 8px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line); }
.construction-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.construction-actions a { display: inline-flex; align-items: center; gap: 4px; text-decoration: none; }
.upgrade-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
.upgrade-row strong { font-size: var(--font-copy); }
.upgrade-row p { margin: 4px 0; color: #849087; font-size: var(--font-caption); line-height: 1.6; }
.upgrade-row small { color: #8a968b; font-size: var(--font-caption); }
.trial-note { display: flex; align-items: center; gap: 7px; margin: 0; color: var(--gold); font-size: var(--font-copy); }
.route-tabs { display: flex; flex-wrap: wrap; gap: 9px; }
.route-tab { display: grid; gap: 3px; min-width: 150px; padding: 10px 14px; text-align: left; color: #c6d0c5; border: 1px solid var(--line); border-radius: 13px; background: #121914; cursor: pointer; }
.route-tab.selected { color: #f0d6a8; border-color: var(--expedition-accent); background: color-mix(in srgb, var(--expedition-accent) 16%, #121914); }
.route-tab.locked { opacity: .6; }
.route-tab small { color: #8a968b; font-size: var(--font-caption); }
.route-tab strong { font-size: var(--font-body); }
.route-tab span { color: #90a091; font-size: var(--font-caption); }
.expedition-card { border-top: 3px solid var(--expedition-accent); }
.expedition-heading { display: flex; gap: 15px; align-items: flex-start; }
.expedition-heading .ui-section-title { margin: 3px 0 6px; }
.expedition-icon { width: 52px; height: 52px; display: grid; flex: 0 0 auto; place-items: center; color: #cce4df; border-radius: 15px 5px; background: color-mix(in srgb, var(--expedition-accent) 34%, transparent); }
.route-facts { display: flex; flex-wrap: wrap; gap: 9px; margin: 18px 0; }
.route-facts > span { display: inline-flex; align-items: center; gap: 6px; padding: 8px 11px; color: #aeb9b0; font-size: var(--font-copy); border-radius: 99px; background: #ffffff08; }
.party-panel { padding-top: 17px; border-top: 1px solid var(--line); }
.party-panel h3, .supply-panel h3 { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
.party-panel h3 small { margin-left: auto; color: #849087; font-size: var(--font-caption); }
.party-selects { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 15px; }
.party-slot { min-width: 0; }
.party-slot :deep(.partner-picker) { margin: 6px 0 0; }
.supply-panel { padding-top: 17px; margin-top: 17px; border-top: 1px solid var(--line); }
.pack-trigger { width: 100%; min-height: 52px; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; padding: 8px 11px; text-align: left; color: #8c998f; border: 1px dashed #ffffff14; border-radius: 12px; background: #0d141086; cursor: pointer; }
.pack-trigger.packed { color: #d9e2d6; border-style: solid; border-color: #8ead7130; background: #8ead710b; }
.trigger-icon { width: 34px; height: 34px; display: grid; place-items: center; color: var(--gold); border-radius: 11px 4px; background: #d7ad5812; }
.pack-trigger:not(.packed) .trigger-icon { color: #7b867d; background: #ffffff07; }
.trigger-copy { display: grid; gap: 2px; min-width: 0; }
.trigger-copy strong { font-size: var(--font-copy); font-weight: 600; }
.trigger-copy small { overflow: hidden; color: #7f8c81; font-size: var(--font-caption); white-space: nowrap; text-overflow: ellipsis; }
.supply-panel > p { margin-top: 7px; }
.start-button { min-height: 42px; margin-top: 18px; gap: 7px; }
.warning { color: var(--danger); font-size: var(--font-copy); margin-bottom: 0; }
.panel-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
.panel-heading h3 { display: flex; align-items: center; gap: 7px; }
.panel-heading > strong { color: var(--expedition-accent); font-size: var(--font-copy); }
.voyage-status > p { margin: 14px 0; }
.journal-panel summary { cursor: pointer; color: #c6d0c5; font-size: var(--font-copy); }
.sailing-loot { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.sailing-loot span { display: inline-flex; align-items: center; gap: 5px; padding: 6px 9px; border-radius: 8px; color: #ddc594; background: #ddc5940a; font-size: var(--font-copy); }
.journal-list { margin: 16px 0 0; padding-left: 20px; display: grid; gap: 12px; font-size: var(--font-copy); }
.journal-list p { color: #a9b2a8; line-height: 1.6; margin: 5px 0; }
.journal-list small { color: #849087; }
.codex-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.codex-list li { display: flex; align-items: center; gap: 11px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 12px; background: #ffffff04; }
.codex-list li.unknown { color: #6d786f; opacity: .7; }
.codex-list strong { display: block; font-size: 13px; }
.codex-list small { display: block; margin-top: 3px; color: #7d887f; }
.discovery-list { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; font-size: var(--font-caption); color: #849087; }
.discovery-list .discovered { color: #80b6b0; }
.produce-list { margin-top: 14px; }
.supply-list { display: grid; gap: 8px; }
.supply-option { width: 100%; display: flex; align-items: center; gap: 11px; padding: 12px; text-align: left; border: 1px solid var(--line); border-radius: 12px; background: #ffffff04; color: #c6d0c5; cursor: pointer; }
.supply-option > div { flex: 1; }
.supply-option > svg { flex-shrink: 0; }
.supply-option.selected { border-color: #80b6b080; background: #80b6b010; }
.supply-option:disabled { opacity: .5; cursor: default; }
.supply-option strong { font-size: var(--font-copy); }
.supply-option p, .supply-option small { color: #849087; font-size: var(--font-caption); line-height: 1.6; margin: 5px 0 0; }
button:focus-visible, summary:focus-visible { outline: 2px solid #80b6b0; outline-offset: 3px; }
@media (max-width: 720px) {
  .party-selects, .upgrade-grid { grid-template-columns: 1fr; }
  .upgrade-grid { gap: 10px; }
  .ship-heading { flex-wrap: wrap; }
  .ship-upgrade-label { margin-left: 0; }
  .route-tab { min-width: min(150px, 100%); }
}
</style>
