<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watchEffect } from 'vue'
import { ArrowLeft, ArrowRight, Backpack, Brain, CheckCircle2, ChevronRight, Clover, Coins, Dices, Dumbbell, FlaskConical, Footprints, PackageOpen, ScrollText, Sparkles, Swords, Trees, UsersRound, Wind, XCircle, Zap } from 'lucide-vue-next'

import BattleUnitChip from '@/components/BattleUnitChip.vue'
import DelveBattleLogDialog from '@/components/DelveBattleLogDialog.vue'
import DelveOutcomeCard from '@/components/DelveOutcomeCard.vue'
import DelveSettlingCard from '@/components/DelveSettlingCard.vue'
import DelvePackDialog from '@/components/DelvePackDialog.vue'
import DelveStrike from '@/components/DelveStrike.vue'
import GameIcon from '@/components/GameIcon.vue'
import ItemPicker from '@/components/ItemPicker.vue'
import ItemPickerDialog from '@/components/ItemPickerDialog.vue'
import PartnerAvatar from '@/components/PartnerAvatar.vue'
import PartnerPicker from '@/components/PartnerPicker.vue'
import StateBlock from '@/components/StateBlock.vue'
import ViewHeader from '@/components/ViewHeader.vue'
import { useGameStore } from '@/stores/game'
import { useUiStore } from '@/stores/ui'
import { itemKindName } from '@/lib/items'
import type {
  DelveBattleActionResult,
  DelveBattleLog,
  ExplorationAttribute,
  ExplorationChoice,
  ExplorationExpedition,
  ExplorationKind,
  ExplorationResolutionResult,
  InventoryItem,
  ItemDefinition,
} from '@/types'

const game = useGameStore()
const ui = useUiStore()
const leaderId = ref('')
const memberTwo = ref('')
const memberThree = ref('')
const resolution = ref<ExplorationResolutionResult | null>(null)
const selectedActors = ref<Record<string, string>>({})
const EXPLORATION_PARTY_STORAGE_PREFIX = 'red-leaf-town:exploration-party:'
let restoredPartyStorageKey = ''

const CHECK_ATTRIBUTES: Record<ExplorationAttribute, { name: string; icon: typeof Dumbbell }> = {
  strength: { name: '力量', icon: Dumbbell },
  agility: { name: '敏捷', icon: Wind },
  intelligence: { name: '智力', icon: Brain },
  luck: { name: '幸运', icon: Clover },
}

const DEGREE_NAMES = {
  automatic_success: '行动完成',
  critical_failure: '大失败',
  failure: '检定失败',
  success: '检定成功',
  critical_success: '大成功',
} as const

const KIND_LABELS: Record<ExplorationKind, string> = {
  transport: '采运',
  survey: '勘探',
  delve: '探秘',
}

const selectedExpeditionId = ref('')
const loadouts = ref<Record<string, { weapon_item_id: string; accessory_item_id: string }>>({})
const carried = ref<Record<string, number>>({})
const battleResult = ref<DelveBattleActionResult | null>(null)

const exploration = computed(() => game.state?.exploration || null)
const run = computed(() => exploration.value?.active_run || null)
const expeditions = computed(() => exploration.value?.expeditions || [])
const expedition = computed(() =>
  expeditions.value.find((entry) => entry.id === selectedExpeditionId.value)
  || expeditions.value.find((entry) => entry.unlocked)
  || expeditions.value[0]
  || null,
)
const isDelve = computed(() => expedition.value?.kind === 'delve')
const battle = computed(() => run.value?.battle || null)
const inventory = computed(() => game.state?.inventory || [])
const weapons = computed(() => inventory.value.filter((item) => item.equipment?.slot === 'weapon'))
const accessories = computed(() => inventory.value.filter((item) => item.equipment?.slot === 'accessory'))
const delveItems = computed(() => inventory.value.filter((item) => item.delve_use))
const carriedCount = computed(() => Object.values(carried.value).reduce((total, count) => total + count, 0))
const CARRY_SLOTS = 6

function kindLabel(entry: ExplorationExpedition) {
  return KIND_LABELS[entry.kind] || '探索'
}

function loadoutFor(partnerId: string) {
  return loadouts.value[partnerId] || { weapon_item_id: '', accessory_item_id: '' }
}

function setLoadout(partnerId: string, slot: 'weapon_item_id' | 'accessory_item_id', itemId: string) {
  loadouts.value = {
    ...loadouts.value,
    [partnerId]: { ...loadoutFor(partnerId), [slot]: itemId },
  }
}

function gearFor(partnerId: string, slot: 'weapon_item_id' | 'accessory_item_id') {
  const itemId = loadoutFor(partnerId)[slot]
  if (!itemId) return null
  return inventory.value.find((item) => item.item_id === itemId) || null
}

function equippedCount(itemId: string) {
  return selectedIds.value.filter((partnerId) => {
    const entry = loadoutFor(partnerId)
    return entry.weapon_item_id === itemId || entry.accessory_item_id === itemId
  }).length
}

/** 同一件装备的件数不够全队用时，把原因写在选项上而不是让玩家撞后端报错。 */
function blockedGear(partnerId: string, slot: 'weapon_item_id' | 'accessory_item_id') {
  const entries: Record<string, string> = {}
  const pool = slot === 'weapon_item_id' ? weapons.value : accessories.value
  for (const item of pool) {
    if (loadoutFor(partnerId)[slot] === item.item_id) continue
    if (equippedCount(item.item_id) >= item.quantity) entries[item.inventory_key] = '已经装备在别人身上'
  }
  return entries
}

function gearSummary(item: InventoryItem | null) {
  const gear = item?.equipment
  if (!gear) return ''
  const parts: string[] = []
  if (gear.attribute) parts.push(`${CHECK_ATTRIBUTES[gear.attribute].name} · ${gear.damage_dice}`)
  if (gear.attack_bonus) parts.push(`命中 +${gear.attack_bonus}`)
  if (gear.proficiency_bonus) parts.push(`熟练 +${gear.proficiency_bonus}`)
  if (gear.armor_bonus) parts.push(`防御 +${gear.armor_bonus}`)
  if (gear.max_hp_bonus) parts.push(`生命 +${gear.max_hp_bonus}`)
  if (gear.initiative_bonus) parts.push(`先攻 +${gear.initiative_bonus}`)
  if (gear.advantage_uses) parts.push(`每场 ${gear.advantage_uses} 次优势骰`)
  return parts.join(' · ')
}

/** 「冻结的战利品」里固定奖励那一路不全是装备：种子、同行札记也从这里出，标签按物品类型给。 */
function fixedRewardLabel(item: ItemDefinition | null) {
  if (!item) return '战利品'
  if (item.id.startsWith('partner_notes')) return '札记'
  if (item.kind === 'equipment') return '装备'
  return itemKindName(item.kind)
}

function carryCount(key: string) {
  return carried.value[key] || 0
}

function setCarry(key: string, count: number) {
  carried.value = { ...carried.value, [key]: Math.max(0, count) }
}

function carriedPayload() {
  return Object.entries(carried.value)
    .filter(([, quantity]) => quantity > 0)
    .map(([key, quantity]) => {
      const [itemId, quality] = key.split(':')
      return { item_id: itemId, quality: Number(quality) || 0, quantity }
    })
}

const packSummary = computed(() => {
  const packed = delveItems.value.filter((item) => carryCount(item.inventory_key) > 0)
  if (!packed.length) return '还没有装东西'
  return packed.map((item) => `${item.name} ×${carryCount(item.inventory_key)}`).join('、')
})

function healText(item: { delve_use?: { dice: string; flat: number; quality_bonus: number } | null; quality?: number | null }) {
  const use = item.delve_use
  if (!use) return ''
  const flat = use.flat + Math.max(0, (item.quality || 1) - 1) * use.quality_bonus
  return `恢复 ${use.dice}${flat ? ` +${flat}` : ''}`
}

// ---------------------------------------------------------------- 战斗

const STRIKE_BEAT = 620

const strikeQueue = ref<DelveBattleLog[]>([])
const currentStrike = ref<DelveBattleLog | null>(null)
const replaying = ref(false)
const settling = ref(false)
const targeting = ref(false)
const itemDialogOpen = ref(false)
const logDialogOpen = ref(false)
const packDialogOpen = ref(false)
let strikeTimer: ReturnType<typeof setTimeout> | undefined
let pendingOutcome: DelveBattleActionResult | null = null

const partyKeys = computed(() => run.value?.partner_ids || [])
/** 出手顺序条上的每一格：队友用自己的头像，敌人统一用一枚骷髅印。 */
const initiativeUnits = computed(() => {
  const current = battle.value
  if (!current) return []
  return current.order.map((entry) => {
    if (entry.is_party) {
      const partner = run.value?.party.find((member) => member.partner_id === entry.key)
      return {
        key: entry.key,
        kind: 'party' as const,
        name: partner?.name || entry.name,
        artwork: partner?.artwork || null,
        crop: partner?.avatar_crop || null,
        icon: '',
        hp: partner?.combat?.hp ?? null,
        maxHp: partner?.combat?.max_hp ?? null,
        armorClass: partner?.combat?.armor_class ?? null,
        note: partner?.loadout?.weapon_name || '徒手',
        boss: false,
      }
    }
    const enemy = current.enemies.find((unit) => unit.key === entry.key)
    return {
      key: entry.key,
      kind: 'enemy' as const,
      name: enemy?.name || entry.name,
      artwork: null,
      crop: null,
      icon: enemy?.icon || '',
      hp: enemy?.hp ?? null,
      maxHp: enemy?.max_hp ?? null,
      armorClass: enemy?.armor_class ?? null,
      note: enemy?.boss ? '首领' : '',
      boss: Boolean(enemy?.boss),
    }
  })
})
const livingEnemies = computed(() => battle.value?.enemies.filter((enemy) => enemy.hp > 0) || [])
const pendingAction = computed(() => game.isPendingPrefix('exploration:'))
const myTurn = computed(() =>
  Boolean(battle.value?.current_actor_is_party) && !replaying.value && !settling.value,
)
const currentStrikeIsFriendly = computed(() =>
  Boolean(currentStrike.value && partyKeys.value.includes(currentStrike.value.actor)),
)
const actingPartner = computed(() =>
  run.value?.party.find((entry) => entry.partner_id === battle.value?.current_actor) || null,
)
const advantageLeft = computed(() =>
  battle.value ? battle.value.advantage_uses_left[battle.value.current_actor] || 0 : 0,
)
const advantageReady = computed(() =>
  Boolean(battle.value?.advantage_ready[battle.value?.current_actor || '']),
)
const haulCount = computed(() => {
  const current = run.value
  if (!current) return 0
  const rewards = current.pending_rewards.reduce((total, entry) => total + entry.quantity, 0)
  return rewards + current.pending_fixed_rewards.reduce((total, entry) => total + entry.quantity, 0)
})
const packRemaining = computed(() =>
  (run.value?.carried_items || []).reduce((total, entry) => total + entry.quantity, 0),
)
const carriedInPack = computed(() =>
  (run.value?.carried_items || []).map((entry) => ({
    item_id: entry.item_id,
    inventory_key: `${entry.item_id}:${entry.quality}`,
    name: entry.item?.name || entry.item_id,
    icon: entry.item?.icon || 'flask-conical',
    kind: 'consumable',
    tags: [],
    quantity: entry.quantity,
    quality: entry.quality || null,
    quality_name: null,
    quality_sale_multiplier: 1,
    base_sell_price: 0,
    sell_price: 0,
    // 带进副本的道具已经从仓库扣掉了，效果只能从冻结快照自带的物品定义上读。
    delve_use: entry.item?.delve_use || null,
  })) as InventoryItem[],
)

function reducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function playStrikes(logs: DelveBattleLog[]) {
  clearTimeout(strikeTimer)
  if (!logs.length) return finishPlayback()
  if (reducedMotion()) {
    currentStrike.value = logs[logs.length - 1]
    return finishPlayback()
  }
  replaying.value = true
  currentStrike.value = logs[0]
  strikeQueue.value = logs.slice(1)
  scheduleStrike()
}

function scheduleStrike() {
  strikeTimer = setTimeout(() => {
    if (!strikeQueue.value.length) return finishPlayback()
    currentStrike.value = strikeQueue.value[0]
    strikeQueue.value = strikeQueue.value.slice(1)
    scheduleStrike()
  }, STRIKE_BEAT)
}

/** 播到最后一条：这时才允许继续行动，或者亮出战斗结算。 */
function finishPlayback() {
  clearTimeout(strikeTimer)
  if (strikeQueue.value.length) currentStrike.value = strikeQueue.value[strikeQueue.value.length - 1]
  strikeQueue.value = []
  replaying.value = false
  if (pendingOutcome) {
    battleResult.value = pendingOutcome
    pendingOutcome = null
    currentStrike.value = null
  }
  settling.value = false
}

function dismissOutcome() {
  battleResult.value = null
  currentStrike.value = null
}

function resetBattleStage() {
  clearTimeout(strikeTimer)
  strikeQueue.value = []
  currentStrike.value = null
  replaying.value = false
  settling.value = false
  pendingOutcome = null
  targeting.value = false
}

async function battleAction(
  action: 'attack' | 'item' | 'flee' | 'advantage',
  target = '',
  item?: { item_id: string; quality?: number | null },
) {
  if (replaying.value || settling.value) return
  targeting.value = false
  settling.value = true
  const result = await game.resolveDelveBattleAction(
    action,
    target,
    item ? item.item_id : '',
    item ? (item.quality || 0) : 0,
  )
  if (!result) {
    settling.value = false
    return
  }
  if (result.outcome !== 'ongoing') pendingOutcome = result
  playStrikes(result.logs)
}

function beginAttack() {
  if (!myTurn.value) return
  if (livingEnemies.value.length === 1) return battleAction('attack', livingEnemies.value[0].key)
  targeting.value = !targeting.value
}

function attackTarget(key: string) {
  if (!targeting.value || !myTurn.value) return
  battleAction('attack', key)
}

function useCarriedItem(item: InventoryItem | null) {
  if (!item || !battle.value) return
  battleAction('item', battle.value.current_actor, { item_id: item.item_id, quality: item.quality })
}

async function confirmFlee() {
  if (!battle.value) return
  const accepted = await ui.confirm({
    title: '撤出这场战斗？',
    description: `全队敏捷最高的人掷一次 d20 对 DC ${battle.value.flee_dc}。成功就跳过这一节点的战利品继续前进，失败白丢一个回合、敌人立刻还手。`,
    confirmLabel: '拉开距离',
    cancelLabel: '继续打',
  })
  if (accepted) battleAction('flee')
}

onBeforeUnmount(() => clearTimeout(strikeTimer))

function unitHpRatio(current: number, max: number) {
  return `${Math.max(0, Math.min(100, Math.round((current / Math.max(1, max)) * 100)))}%`
}
const allPartners = computed(() => (game.state?.partners || []).filter((partner) => !partner.missing))
const leaders = computed(() => allPartners.value.filter((partner) =>
  (partner.tendencies || []).some((tendency) => tendency.industry === 'exploration'),
))
const selectedIds = computed(() => [leaderId.value, memberTwo.value, memberThree.value].filter(Boolean))

watchEffect(() => {
  const playerId = game.account?.player_id
  if (!playerId || !game.state || !allPartners.value.length) return

  const storageKey = `${EXPLORATION_PARTY_STORAGE_PREFIX}${playerId}`
  if (restoredPartyStorageKey === storageKey) return
  restoredPartyStorageKey = storageKey

  let savedIds: string[] = []
  try {
    const parsed = JSON.parse(localStorage.getItem(storageKey) || 'null')
    if (Array.isArray(parsed)) savedIds = parsed.filter((id): id is string => typeof id === 'string').slice(0, 3)
  } catch {
    savedIds = []
  }

  const desiredSize = savedIds.length || 1
  const used = new Set<string>()
  const savedLeader = savedIds[0]
  const leader = leaders.value.find((partner) => partner.partner_id === savedLeader) || leaders.value[0]
  if (leader) {
    leaderId.value = leader.partner_id
    used.add(leader.partner_id)
  }

  const restoredMembers = savedIds.slice(1)
    .map((partnerId) => allPartners.value.find((partner) => partner.partner_id === partnerId)?.partner_id || '')
    .filter((partnerId) => partnerId && !used.has(partnerId))
  const replacementMembers = allPartners.value
    .map((partner) => partner.partner_id)
    .filter((partnerId) => !used.has(partnerId) && !restoredMembers.includes(partnerId))
  const members = [...restoredMembers, ...replacementMembers].slice(0, Math.max(0, desiredSize - used.size))
  memberTwo.value = members[0] || ''
  memberThree.value = members[1] || ''
})

function availableFor(slot: number) {
  const selections = [leaderId.value, memberTwo.value, memberThree.value]
  return allPartners.value.filter((partner) => !selections.some((id, index) => index !== slot && id === partner.partner_id))
}

function partnerById(partnerId: string) {
  return allPartners.value.find((partner) => partner.partner_id === partnerId) || null
}

function excludedFor(slot: number) {
  return [leaderId.value, memberTwo.value, memberThree.value].filter((id, index) => index !== slot && Boolean(id))
}

function explorationAbility(partner: (typeof allPartners.value)[number]) {
  return (partner.tendencies || []).find((entry) => entry.industry === 'exploration')?.effective_ability || 0
}

async function start() {
  if (!expedition.value || !leaderId.value) return
  resolution.value = null
  battleResult.value = null
  resetBattleStage()
  const result = await game.startExploration(
    expedition.value.id,
    selectedIds.value,
    leaderId.value,
    isDelve.value ? Object.fromEntries(selectedIds.value.map((id) => [id, loadoutFor(id)])) : undefined,
    isDelve.value ? carriedPayload() : undefined,
  )
  if (result) {
    try {
      const playerId = game.account?.player_id
      if (playerId) localStorage.setItem(
        `${EXPLORATION_PARTY_STORAGE_PREFIX}${playerId}`,
        JSON.stringify(selectedIds.value),
      )
    } catch {
      // Local storage may be unavailable in private browsing; the run itself is unaffected.
    }
  }
}

async function resolveChoice(choiceId: string) {
  const result = await game.resolveExploration(choiceId, selectedActors.value[choiceId] || '')
  if (!result) return
  // 开场就被打光的话这一趟已经结束了，提示已经弹过，不再显示事件结算卡。
  if (result.outcome === 'wiped') return
  // 战斗节点直接进战斗面板，不再弹一次事件结算卡。
  if (!result.battle_started) resolution.value = result
}

function actorFor(choice: ExplorationChoice) {
  return selectedActors.value[choice.id] || choice.actor_partner_id || choice.actor_options[0]?.partner_id || ''
}

function selectActor(choice: ExplorationChoice, partnerId: string) {
  selectedActors.value = { ...selectedActors.value, [choice.id]: partnerId }
}

function actorName(choice: ExplorationChoice) {
  const id = actorFor(choice)
  return choice.actor_options.find((actor) => actor.partner_id === id)?.name || id
}

function continueRoute() {
  resolution.value = null
}

async function withdraw() {
  if (!run.value) return
  if (run.value.status !== 'completed') {
    const accepted = await ui.confirm({
      title: '提前撤离并结算？',
      description: '将结束本次路线，带回已有战利品和未用完的携带道具。已花费的入场费和体力不退还，无法继续本次进度。',
      confirmLabel: '撤离并结算',
      cancelLabel: '继续探索',
      tone: 'danger',
    })
    if (!accepted) return
  }
  const result = await game.withdrawExploration()
  if (result) {
    resolution.value = null
    battleResult.value = null
    resetBattleStage()
    carried.value = {}
  }
}

function rewardText(rewards: NonNullable<typeof run.value>['pending_rewards']) {
  return rewards.map((reward) => `${reward.quality_name}${reward.item?.name || reward.item_id} ×${reward.quantity}`).join('、')
}

function signed(value: number | null) {
  if (value === null) return ''
  return value >= 0 ? `+${value}` : String(value)
}

function diceTitle(result: ExplorationResolutionResult) {
  if (result.dice_mode === 'advantage') return '优势骰'
  if (result.dice_mode === 'disadvantage') return '劣势骰'
  if (result.rolls.length > 1) return '自动重投'
  return '检定骰'
}

function diceCaption(result: ExplorationResolutionResult) {
  if (result.dice_mode === 'advantage') return '两次掷骰，取较高结果'
  if (result.dice_mode === 'disadvantage') return '两次掷骰，取较低结果'
  if (result.rolls.length > 1) return '第一次普通失败，特性自动使用重投'
  return '本次检定使用的 d20 结果'
}

function keptDieIndex(result: ExplorationResolutionResult) {
  if (result.rolls.length < 2) return 0
  if (result.dice_mode === 'advantage') return result.rolls.indexOf(Math.max(...result.rolls))
  if (result.dice_mode === 'disadvantage') return result.rolls.indexOf(Math.min(...result.rolls))
  return 1
}

function dieLabel(result: ExplorationResolutionResult, index: number) {
  if (result.rolls.length < 2) return '结果'
  if (result.dice_mode === 'advantage') return index === keptDieIndex(result) ? '取高' : '舍弃'
  if (result.dice_mode === 'disadvantage') return index === keptDieIndex(result) ? '取低' : '舍弃'
  return index === 0 ? '首次失败' : '自动重投'
}
</script>

<template>
  <section v-if="game.state" class="view-section exploration-view">
    <ViewHeader eyebrow="EXPEDITION" title="探索" />

    <DelveSettlingCard
      v-if="settling && !run && !battleResult"
      class="outcome-standalone"
      :strike="currentStrike"
      :friendly="currentStrikeIsFriendly"
      :replaying="replaying"
      @skip="finishPlayback"
    />

    <DelveOutcomeCard
      v-if="battleResult && !run"
      :result="battleResult"
      standalone
      class="outcome-standalone"
      @next="dismissOutcome"
    />

    <StateBlock
      v-if="!exploration?.unlocked && !run && !settling && !battleResult"
      variant="card"
      :icon="Trees"
      title="探索尚未开放"
      description="居民等级 6 解锁采运，之后才会开出更深的路线。"
    />

    <template v-else-if="!run && expedition && !settling && !battleResult">
      <div v-if="expeditions.length > 1" class="route-tabs">
        <button
          v-for="entry in expeditions"
          :key="entry.id"
          type="button"
          class="route-tab"
          :class="{ selected: entry.id === expedition.id, locked: !entry.unlocked }"
          :style="{ '--expedition-accent': entry.accent }"
          @click="selectedExpeditionId = entry.id"
        >
          <small>{{ kindLabel(entry) }}<template v-if="entry.beta"> · 内测</template></small>
          <strong>{{ entry.name }}</strong>
          <span>{{ entry.unlocked ? `${entry.entry_fee} 红叶币` : `${entry.min_level} 级解锁` }}</span>
        </button>
      </div>

      <article class="expedition-card surface-card" :style="{ '--expedition-accent': expedition.accent }">
        <div class="expedition-heading">
          <span class="expedition-icon"><component :is="isDelve ? Swords : Trees" :size="28" /></span>
          <div>
            <small class="ui-kicker">{{ kindLabel(expedition) }}<template v-if="expedition.beta"> · 内测</template></small>
            <h2 class="ui-section-title">{{ expedition.name }}</h2>
            <p class="ui-description">{{ expedition.description }}</p>
          </div>
        </div>

        <div class="route-facts">
          <span><Coins :size="16" /> 入场 <strong>{{ expedition.entry_fee }}</strong></span>
          <span><Footprints :size="16" /> 最深 {{ expedition.max_depth }} 个节点</span>
          <span v-if="isDelve"><Swords :size="16" /> 沿途有遭遇战，终点是首领</span>
          <span v-else><Trees :size="16" /> 主要产出 枫木</span>
        </div>

        <div class="party-panel">
          <h3 class="ui-section-title"><UsersRound :size="18" /> 组织队伍</h3>
          <p class="ui-description">
            领队必须具有探索倾向；其他位置可以安排任意伙伴。探索伙伴作为队员时提供 25% 探索能力。
            <template v-if="isDelve">探秘必须三人满编，出发后队伍会带着冻结的装备和道具进入副本。</template>
          </p>
          <p v-if="expedition.leader_note" class="leader-note">{{ expedition.leader_note }}</p>
          <div class="party-selects">
            <section class="party-slot">
              <span class="ui-label">领队 · 必须具备探索倾向</span>
              <PartnerPicker
                industry="exploration"
                action-key="exploration:party:leader"
                :assigned="partnerById(leaderId)"
                :candidates="leaders"
                :excluded-partner-ids="excludedFor(0)"
                placeholder="选择领队"
                solo-label="取消领队"
                dialog-title="选择采运领队"
                :clearable="false"
                elevated
                @select="(partnerId) => leaderId = partnerId || ''"
              />
            </section>
            <section class="party-slot">
              <span class="ui-label">队员 1 · 可选</span>
              <PartnerPicker
                industry="exploration"
                action-key="exploration:party:member-1"
                :assigned="partnerById(memberTwo)"
                :candidates="availableFor(1)"
                :excluded-partner-ids="excludedFor(1)"
                placeholder="暂不安排"
                solo-label="暂不安排"
                dialog-title="选择采运队员"
                elevated
                @select="(partnerId) => memberTwo = partnerId || ''"
              />
            </section>
            <section class="party-slot">
              <span class="ui-label">队员 2 · 可选</span>
              <PartnerPicker
                industry="exploration"
                action-key="exploration:party:member-2"
                :assigned="partnerById(memberThree)"
                :candidates="availableFor(2)"
                :excluded-partner-ids="excludedFor(2)"
                placeholder="暂不安排"
                solo-label="暂不安排"
                dialog-title="选择采运队员"
                elevated
                @select="(partnerId) => memberThree = partnerId || ''"
              />
            </section>
          </div>
        </div>

        <div v-if="isDelve" class="gear-panel">
          <h3 class="ui-section-title"><Swords :size="18" /> 装备与携带包</h3>
          <p class="ui-description">
            武器决定这个人吃哪一维：大剑吃力量、短剑吃敏捷、法书吃智力。装备只冻结数值不消耗，
            道具出发时从仓库扣除，没用完的随撤离退回。
          </p>

          <div class="gear-grid">
            <section v-for="partnerId in selectedIds" :key="`gear:${partnerId}`" class="gear-slot">
              <header>
                <PartnerAvatar
                  :artwork="partnerById(partnerId)?.artwork"
                  :crop="partnerById(partnerId)?.avatar_crop"
                  :name="partnerById(partnerId)?.name || partnerId"
                  :size="30"
                />
                <strong>{{ partnerById(partnerId)?.name || partnerId }}</strong>
              </header>

              <ItemPicker
                label="武器"
                placeholder="徒手 · 1d4"
                hint="不带武器就按徒手结算"
                fallback-icon="sword"
                dialog-title="选择武器"
                dialog-subtitle="武器决定攻击吃哪一维，以及每次挥出的伤害骰"
                clear-label="徒手 · 1d4"
                empty-text="仓库里还没有武器"
                :items="weapons"
                :selected="gearFor(partnerId, 'weapon_item_id')"
                :summary="gearSummary(gearFor(partnerId, 'weapon_item_id'))"
                :blocked="blockedGear(partnerId, 'weapon_item_id')"
                @select="(item) => setLoadout(partnerId, 'weapon_item_id', item?.item_id || '')"
              >
                <template #meta="{ item }">
                  {{ gearSummary(item) }} · 仓库 {{ item.quantity }}
                  <span v-if="item.equipment?.description" class="gear-note">{{ item.equipment?.description }}</span>
                </template>
              </ItemPicker>

              <ItemPicker
                label="饰品"
                placeholder="不佩戴"
                hint="空着也能出发"
                fallback-icon="shield"
                dialog-title="选择饰品"
                dialog-subtitle="饰品给熟练加值、护甲、先攻或每场一次的优势骰"
                clear-label="不佩戴"
                empty-text="仓库里还没有饰品"
                :items="accessories"
                :selected="gearFor(partnerId, 'accessory_item_id')"
                :summary="gearSummary(gearFor(partnerId, 'accessory_item_id'))"
                :blocked="blockedGear(partnerId, 'accessory_item_id')"
                @select="(item) => setLoadout(partnerId, 'accessory_item_id', item?.item_id || '')"
              >
                <template #meta="{ item }">
                  {{ gearSummary(item) }} · 仓库 {{ item.quantity }}
                  <span v-if="item.equipment?.description" class="gear-note">{{ item.equipment?.description }}</span>
                </template>
              </ItemPicker>
            </section>
          </div>

          <button class="pack-trigger" :class="{ packed: carriedCount > 0 }" @click="packDialogOpen = true">
            <span class="trigger-icon"><Backpack :size="19" /></span>
            <span class="trigger-copy">
              <strong>携带包 {{ carriedCount }} / {{ CARRY_SLOTS }}</strong>
              <small>{{ packSummary }}</small>
            </span>
            <ChevronRight :size="16" class="picker-caret" />
          </button>
        </div>

        <button
          class="primary-button start-button"
          :disabled="!leaderId || !expedition.affordable || !expedition.unlocked || (isDelve && selectedIds.length < 3) || game.isPendingPrefix('exploration:')"
          @click="start"
        >
          支付 {{ expedition.entry_fee }} 红叶币并出发
        </button>
        <p v-if="!leaders.length" class="warning">你还没有具有探索倾向的伙伴。</p>
        <p v-else-if="!expedition.unlocked" class="warning">居民等级 {{ expedition.min_level }} 才能进入这条路线。</p>
        <p v-else-if="!expedition.affordable" class="warning">红叶币不足。</p>
        <p v-else-if="isDelve && selectedIds.length < 3" class="warning">探秘副本需要正好 3 名伙伴。</p>
      </article>
    </template>

    <template v-else-if="run">
      <div class="run-status">
        <div><small>当前路线</small><strong>{{ run.expedition.name }}</strong></div>
        <div><small>探索能力</small><strong>{{ run.exploration_ability }}</strong></div>
        <div><small>体力减免</small><strong>{{ Math.round(run.stamina_discount_rate * 100) }}%</strong></div>
        <div><small>探索进度</small><strong>{{ run.depth }} / {{ run.expedition.max_depth }}</strong></div>
        <div><small>已耗体力</small><strong>{{ run.stamina_spent }}</strong></div>
      </div>

      <div class="party-row" :class="{ 'in-delve': run.party.some((entry) => entry.combat) }">
        <div
          v-for="partner in run.party"
          :key="partner.partner_id"
          class="party-member"
          :class="{ downed: partner.combat && partner.combat.hp <= 0, acting: battle?.current_actor === partner.partner_id }"
        >
          <PartnerAvatar :artwork="partner.artwork" :crop="partner.avatar_crop" :name="partner.name" :size="38" />
          <span>
            <strong>{{ partner.name }}</strong>
            <small v-if="!partner.combat">{{ partner.partner_id === run.leader_partner_id ? '领队' : '队员' }}</small>
            <small v-else class="member-vitals">
              <b>{{ partner.combat.hp }}</b><em>/ {{ partner.combat.max_hp }}</em>
              <u>AC {{ partner.combat.armor_class }}</u>
              <template v-if="partner.loadout?.weapon_name">
                <u class="member-weapon">{{ partner.loadout.weapon_name }}</u>
              </template>
            </small>
            <i v-if="partner.combat" class="hp-bar"><b :style="{ width: unitHpRatio(partner.combat.hp, partner.combat.max_hp) }" /></i>
          </span>
        </div>
      </div>

      <DelveOutcomeCard
        v-if="battleResult"
        :result="battleResult"
        @next="dismissOutcome"
      />

      <article v-else-if="battle" class="battle-card surface-card">
        <header class="battle-heading">
          <span class="battle-icon"><Swords :size="21" /></span>
          <div class="battle-title">
            <small class="ui-kicker">第 {{ battle.depth }} 段 · 第 {{ battle.round }} 回合</small>
            <h2 class="ui-section-title">
              {{ pendingAction ? '正在结算' : replaying ? '结算中' : battle.current_actor_is_party ? `轮到 ${battle.current_actor_name}` : '敌人行动中' }}
            </h2>
          </div>
          <button class="text-button log-button" @click="logDialogOpen = true">
            <ScrollText :size="15" /> 战斗记录
          </button>
        </header>

        <div class="strike-stage" :class="{ waiting: pendingAction }">
          <DelveStrike
            v-if="currentStrike"
            :key="`${battle.round}:${currentStrike.actor}:${currentStrike.text}`"
            :log="currentStrike"
            :friendly="partyKeys.includes(currentStrike.actor)"
          />
          <p v-else class="strike-idle">
            <Dices :size="15" /> 行动结果会刻在这里：骰面、算式和落到谁身上的伤害。
          </p>
          <p v-if="pendingAction" class="strike-waiting"><i /><span>正在掷骰…</span></p>
          <button v-else-if="replaying" class="text-button skip-button" @click="finishPlayback">跳过结算</button>
        </div>

        <div class="initiative-track">
          <span class="track-label">出手顺序</span>
          <BattleUnitChip
            v-for="(unit, index) in initiativeUnits"
            :key="`${unit.key}:${index}`"
            :name="unit.name"
            :kind="unit.kind"
            :artwork="unit.artwork"
            :crop="unit.crop"
            :icon="unit.icon"
            :hp="unit.hp"
            :max-hp="unit.maxHp"
            :armor-class="unit.armorClass"
            :note="unit.note"
            :boss="unit.boss"
            :active="unit.key === battle.current_actor"
            :size="36"
          />
        </div>

        <div class="enemy-row" :class="{ targeting }">
          <button
            v-for="enemy in battle.enemies"
            :key="enemy.key"
            type="button"
            class="enemy-card"
            :class="{ defeated: enemy.hp <= 0, boss: enemy.boss, acting: battle.current_actor === enemy.key }"
            :disabled="enemy.hp <= 0 || !targeting || !myTurn"
            @click="attackTarget(enemy.key)"
          >
            <span class="enemy-icon"><GameIcon :name="enemy.icon" :size="21" /></span>
            <span class="enemy-copy">
              <strong>{{ enemy.name }}</strong>
              <small>
                {{ enemy.hp }} / {{ enemy.max_hp }} · AC {{ enemy.armor_class }}
                <b v-if="enemy.attacks_per_turn > 1" class="enemy-multi">一回合 {{ enemy.attacks_per_turn }} 动</b>
              </small>
              <i class="hp-bar enemy"><b :style="{ width: unitHpRatio(enemy.hp, enemy.max_hp) }" /></i>
            </span>
            <span v-if="targeting && enemy.hp > 0" class="enemy-aim">瞄准</span>
          </button>
        </div>

        <div v-if="advantageReady" class="actor-extra ready-note">
          <Zap :size="14" /> {{ battle.current_actor_name }}已经屏息，下一次攻击以优势骰进行。
        </div>
        <div v-else-if="actingPartner && advantageLeft > 0" class="actor-extra">
          <button class="ready-chip" :disabled="!myTurn" @click="battleAction('advantage')">
            <Zap :size="14" /> 屏息 · 下一击优势骰（还剩 {{ advantageLeft }} 次）
          </button>
        </div>

        <div class="battle-actions">
          <button
            type="button"
            class="battle-action attack"
            :class="{ armed: targeting }"
            :disabled="!myTurn"
            @click="beginAttack"
          >
            <span class="action-mark"><Swords :size="19" /></span>
            <strong>{{ targeting ? '选择目标' : '攻击' }}</strong>
            <small>{{ livingEnemies.length > 1 ? '点敌人指定目标' : '打向唯一的敌人' }}</small>
          </button>

          <button
            type="button"
            class="battle-action item"
            :disabled="!myTurn || !run.carried_items.length"
            @click="itemDialogOpen = true"
          >
            <span class="action-mark"><FlaskConical :size="19" /></span>
            <strong>道具</strong>
            <small>{{ run.carried_items.length ? `包里还有 ${packRemaining} 份` : '携带包已经空了' }}</small>
          </button>

          <button
            type="button"
            class="battle-action flee"
            :disabled="!myTurn || !battle.can_flee"
            @click="confirmFlee"
          >
            <span class="action-mark"><ArrowLeft :size="19" /></span>
            <strong>撤离</strong>
            <small>{{ battle.can_flee ? `敏捷检定 DC ${battle.flee_dc}` : '这场战斗跑不掉' }}</small>
          </button>
        </div>
      </article>

      <DelveSettlingCard
        v-if="settling && !battle && !battleResult"
        :strike="currentStrike"
        :friendly="currentStrikeIsFriendly"
        :replaying="replaying"
        @skip="finishPlayback"
      />

      <article v-else-if="resolution" class="event-card result-card surface-card" :class="{ failed: !resolution.success }">
        <div class="result-header">
          <component :is="resolution.success ? CheckCircle2 : XCircle" :size="30" class="result-icon" />
          <div class="result-copy">
            <small class="ui-kicker">节点 {{ run.depth }} · 事件结算</small>
            <h2 class="ui-section-title">{{ DEGREE_NAMES[resolution.degree] }}</h2>
            <p class="ui-description">{{ resolution.text }}</p>
          </div>
        </div>
        <div v-if="resolution.kept_roll !== null" class="dice-panel">
          <div class="dice-heading">
            <span class="dice-heading-icon"><Dices :size="17" /></span>
            <span><strong>{{ diceTitle(resolution) }}</strong><small>{{ diceCaption(resolution) }}</small></span>
            <b>{{ signed(resolution.modifier) }} = {{ resolution.total }}</b>
          </div>
          <div class="dice-track">
            <div
              v-for="(roll, index) in resolution.rolls"
              :key="`${index}:${roll}`"
              class="die-card"
              :class="{ kept: index === keptDieIndex(resolution), discarded: index !== keptDieIndex(resolution) && resolution.rolls.length > 1 }"
            >
              <small>{{ dieLabel(resolution, index) }}</small>
              <strong>{{ roll }}</strong>
            </div>
          </div>
        </div>
        <div class="result-summary">
          <span><Footprints :size="15" /> 体力 -{{ resolution.stamina_cost }}</span>
          <span v-for="drop in resolution.drops" :key="`${drop.item_id}:${drop.quality}`">
            <PackageOpen :size="15" /> {{ drop.quality_name }}{{ drop.item?.name || drop.item_id }} ×{{ drop.quantity }}
          </span>
          <span v-if="!resolution.drops.length"><PackageOpen :size="15" /> 本节点没有取得物资</span>
        </div>
        <button class="primary-button next-button" @click="continueRoute">
          {{ resolution.completed ? '查看路线终点' : '前往下一节点' }} <ArrowRight :size="17" />
        </button>
      </article>

      <article v-else-if="run.current_event && !battle && !battleResult && !settling" class="event-card surface-card">
        <small class="ui-kicker">第 {{ run.depth + 1 }} 段路线</small>
        <h2 class="ui-section-title">{{ run.current_event.name }}</h2>
        <p class="ui-description">{{ run.current_event.description }}</p>
        <div class="choice-list">
          <div
            v-for="choice in run.current_event.choices"
            :key="choice.id"
            class="choice-entry"
          >
            <div class="choice-button">
              <span><strong>{{ choice.label }}</strong><small>{{ choice.description }}</small></span>
              <span class="choice-meta">
                <b v-if="choice.check && choice.check_attribute">
                  <component :is="CHECK_ATTRIBUTES[choice.check_attribute].icon" :size="13" />
                  {{ choice.check_mode === 'sum' ? '全队' : '' }}{{ CHECK_ATTRIBUTES[choice.check_attribute].name }} · DC {{ choice.check.dc }}
                </b>
                <b v-if="choice.check && choice.actor_options.length">负责：{{ actorName(choice) }}</b>
                <b v-if="choice.check && !choice.actor_options.length">{{ choice.check_actor_names.join('、') }}负责</b>
                <b v-else-if="!choice.check">无需检定</b>
                <b>体力 {{ choice.stamina_cost_min }}<template v-if="choice.stamina_cost_max !== choice.stamina_cost_min">～{{ choice.stamina_cost_max }}</template></b>
              </span>
            </div>
            <div v-if="choice.actor_options.length" class="actor-options">
              <button
                v-for="actor in choice.actor_options"
                :key="actor.partner_id"
                type="button"
                class="actor-option"
                :class="{ selected: actorFor(choice) === actor.partner_id }"
                :disabled="game.isPendingPrefix('exploration:')"
                @click="selectActor(choice, actor.partner_id)"
              >
                {{ actor.name }} · {{ actor.success_chance >= 1 ? '必定' : `成功 ${Math.round(actor.success_chance * 100)}%` }}
                <span v-if="actor.applied_effects.length" class="actor-bonus">✦ 特性</span>
              </button>
            </div>
            <button
              type="button"
              class="primary-button resolve-button"
              :disabled="game.liveStamina < choice.stamina_cost_max || game.isPendingPrefix('exploration:')"
              @click="resolveChoice(choice.id)"
            >执行行动</button>
          </div>
        </div>
      </article>

      <article v-else-if="!battle && !battleResult && !settling" class="event-card completed surface-card">
        <Sparkles :size="30" />
        <h2 class="ui-section-title">路线已经走完</h2>
        <p class="ui-description">队伍抵达路线尽头，可以带着全部所得返程。</p>
      </article>

      <article class="haul-card surface-card">
        <header class="haul-heading">
          <h3 class="ui-section-title">冻结的战利品</h3>
          <small>{{ haulCount ? `共 ${haulCount} 件 · 撤离或走完路线才入库` : '撤离前一直挂在队伍身上' }}</small>
        </header>

        <div v-if="haulCount" class="haul-grid">
          <span
            v-for="drop in run.pending_rewards"
            :key="`drop:${drop.item_id}:${drop.quality}`"
            class="haul-chip"
            :class="`quality-tone-${drop.quality}`"
          >
            <i><GameIcon :name="drop.item?.icon" :size="17" /></i>
            <span>
              <strong>{{ drop.item?.name || drop.item_id }}</strong>
              <small>{{ drop.quality_name }}</small>
            </span>
            <b>×{{ drop.quantity }}</b>
          </span>

          <span
            v-for="gear in run.pending_fixed_rewards"
            :key="`gear:${gear.item_id}`"
            class="haul-chip gear"
          >
            <i><GameIcon :name="gear.item?.icon || 'shield'" :size="17" /></i>
            <span>
              <strong>{{ gear.item?.name || gear.item_id }}</strong>
              <small>{{ fixedRewardLabel(gear.item) }}</small>
            </span>
            <b>×{{ gear.quantity }}</b>
          </span>
        </div>
        <p v-else class="muted haul-empty">目前还没有收获。</p>

        <footer class="haul-footer">
          <button
            class="secondary-button withdraw-button"
            :disabled="Boolean(battle) || pendingAction"
            @click="withdraw"
          >
            <ArrowLeft :size="17" /> {{ run.status === 'completed' ? '完成路线并返程' : '现在撤离并结算' }}
          </button>
          <small v-if="battle" class="muted">战斗结束之前没法撤离。</small>
        </footer>
      </article>

      <div v-if="run.logs.length" class="event-log surface-card">
        <h3 class="ui-section-title">行程记录</h3>
        <article v-for="entry in [...run.logs].reverse()" :key="`${entry.depth}:${entry.event_id}`">
          <span>节点 {{ entry.depth }}</span>
          <div><strong>{{ entry.event_name }}</strong><p>{{ entry.text }}</p><small>体力 -{{ entry.stamina_cost }}<template v-if="entry.rewards.length"> · {{ rewardText(entry.rewards) }}</template></small></div>
        </article>
      </div>
    </template>

    <DelvePackDialog
      :open="packDialogOpen"
      :items="delveItems"
      :counts="carried"
      :slots="CARRY_SLOTS"
      @update="({ key, count }) => setCarry(key, count)"
      @close="packDialogOpen = false"
    />

    <ItemPickerDialog
      :open="itemDialogOpen"
      title="用一件道具"
      :subtitle="battle ? `${battle.current_actor_name}的回合 · 用在自己身上` : ''"
      :items="carriedInPack"
      empty-text="携带包已经空了"
      elevated
      @select="useCarriedItem"
      @close="itemDialogOpen = false"
    >
      <template #meta="{ item }">{{ healText(item) }} · 还剩 {{ item.quantity }} 份</template>
    </ItemPickerDialog>

    <DelveBattleLogDialog
      :open="logDialogOpen"
      :logs="battle?.logs || []"
      :party-keys="partyKeys"
      @close="logDialogOpen = false"
    />
  </section>
</template>

<style scoped>
.exploration-view { --expedition-accent: #b85f3f; }
/* 装备描述（equipment.description）：上游原本没有渲染位，这里在选择器列表的数值摘要下补一行 */
.gear-note { display: block; margin-top: 3px; color: #6f7d73; }
.expedition-card, .event-card, .haul-card, .event-log { padding: 20px; }
.expedition-card { border-top: 3px solid var(--expedition-accent); }
/* 活动路线的领队加成提示：文案由后端 leader_note 提供，只在有内容时显示 */
.leader-note {
  margin: -4px 0 14px;
  padding: 0;
  color: #8ab4f8;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.expedition-heading { display: flex; gap: 15px; align-items: flex-start; }
.expedition-heading .ui-section-title, .event-card .ui-section-title { margin: 3px 0 6px; }
.expedition-icon { width: 52px; height: 52px; display: grid; flex: 0 0 auto; place-items: center; color: #efd1b7; border-radius: 15px 5px; background: color-mix(in srgb, var(--expedition-accent) 34%, transparent); }
.route-facts { display: flex; flex-wrap: wrap; gap: 9px; margin: 18px 0; }
.route-facts span { display: inline-flex; align-items: center; gap: 6px; padding: 8px 11px; color: #aeb9b0; font-size: var(--font-copy); border-radius: 99px; background: #ffffff08; }
.party-panel { padding-top: 17px; border-top: 1px solid var(--line); }
.party-panel h3, .haul-card h3, .event-log h3 { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
.party-selects { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 15px; }
.party-slot { min-width: 0; }
.party-slot :deep(.partner-picker) { margin: 6px 0 0; }
.start-button, .withdraw-button { min-height: 42px; margin-top: 18px; }
.start-button { background: var(--leaf-bright); }
.withdraw-button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; }
.warning { color: var(--danger); font-size: var(--font-copy); }
.run-status { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
.run-status div { display: grid; gap: 3px; padding: 11px; border: 1px solid var(--line); border-radius: 11px; background: #121914; }
.run-status small, .party-member small { color: #7f8c81; font-size: var(--font-caption); }
.run-status strong { color: #ead9c2; }
.party-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.party-member { display: flex; align-items: center; gap: 8px; padding: 7px 11px; border: 1px solid var(--line); border-radius: 12px; background: #121914; }
.party-member span { display: grid; }
.party-member strong { color: #dbe3da; font-size: var(--font-copy); }
.choice-list { display: grid; gap: 9px; margin-top: 17px; }
.choice-entry { display: grid; gap: 8px; padding: 11px 14px; border: 1px solid #ffffff12; border-radius: 13px; background: #ffffff06; }
.choice-button { min-height: 67px; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 14px; text-align: left; color: #dce5da; border: 1px solid #ffffff12; border-radius: 13px; background: #ffffff06; cursor: pointer; }
.choice-entry:has(.resolve-button:not(:disabled)):hover { border-color: #b85f3f80; background: #b85f3f12; }
.choice-button > span:first-child { display: grid; gap: 4px; }
.choice-button strong { font-size: var(--font-body); }
.choice-button small { color: #8f9b91; font-size: var(--font-copy); line-height: 1.55; }
.choice-meta { display: grid; flex: 0 0 auto; gap: 3px; text-align: right; color: #b9c5ba; font-size: var(--font-caption); }
.choice-meta b { display: inline-flex; align-items: center; justify-content: flex-end; gap: 4px; }
.actor-options { display: flex; flex-wrap: wrap; gap: 6px; }
.actor-option { min-height: 32px; padding: 5px 9px; color: #b8c7b7; border: 1px solid #ffffff18; border-radius: 8px; background: #ffffff08; cursor: pointer; }
.actor-option.selected { color: #f0d6a8; border-color: #d79a68; background: #d79a6818; }
.actor-option:disabled { opacity: .5; cursor: default; }
.actor-bonus { margin-left: 3px; color: #d8bb7c; font-size: var(--font-caption); }
.resolve-button { justify-self: end; min-height: 34px; padding: 0 12px; }
.result-card { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 440px); align-items: start; gap: 14px 20px; border-color: color-mix(in srgb, var(--leaf-bright) 32%, var(--line)); background: linear-gradient(145deg, color-mix(in srgb, var(--leaf) 10%, var(--surface)), var(--surface)); }
.result-card.failed { border-color: color-mix(in srgb, var(--autumn) 34%, var(--line)); }
.result-header { display: flex; align-items: flex-start; gap: 14px; min-width: 0; }
.result-icon { margin-top: 2px; color: var(--leaf-bright); }
.result-card.failed .result-icon { color: var(--autumn); }
.result-copy { min-width: 0; }
.result-summary { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 7px; padding-top: 13px; border-top: 1px solid var(--line); }
.result-summary > span { display: inline-flex; align-items: center; gap: 6px; padding: 7px 10px; color: #b9c5ba; font-size: var(--font-copy); border-radius: 99px; background: #ffffff07; }
.dice-panel { width: 100%; min-width: 0; padding: 12px; border: 1px solid #d79a6840; border-radius: 12px; background: linear-gradient(135deg, #d79a6814, #ffffff05); }
.dice-heading { display: flex; align-items: center; gap: 9px; }
.dice-heading-icon { display: grid; width: 31px; height: 31px; place-items: center; color: #f0c48e; border: 1px solid #d79a6860; border-radius: 8px; background: #d79a6820; }
.dice-heading > span:nth-child(2) { display: grid; flex: 1; gap: 2px; min-width: 0; }
.dice-heading strong { color: #f0d6a8; font-size: var(--font-copy); }
.dice-heading small { color: #a9b4a9; font-size: var(--font-caption); }
.dice-heading > b { color: #e9d8bd; font-size: var(--font-body); white-space: nowrap; }
.dice-track { display: flex; gap: 8px; margin-top: 11px; }
.die-card { display: grid; flex: 1; min-width: 72px; gap: 3px; padding: 8px 10px; text-align: center; border: 1px solid #ffffff18; border-radius: 9px; background: #0e1510; }
.die-card small { color: #89978c; font-size: var(--font-caption); }
.die-card strong { color: #e2eadf; font-size: 25px; line-height: 1; }
.die-card.kept { border-color: #d79a68; box-shadow: inset 0 0 0 1px #d79a6830, 0 0 14px #d79a6818; background: #d79a6812; }
.die-card.kept small { color: #e8bb87; }
.die-card.discarded { opacity: .55; }
.dice-panel + .result-summary { align-self: stretch; }
.next-button { grid-column: 1 / -1; justify-self: end; gap: 7px; min-height: 40px; }
.event-card.completed { text-align: center; color: #d8ae79; }
.event-card.completed .ui-section-title { margin-top: 8px; }
.haul-card { margin-top: 12px; }
.haul-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 12px; }
.haul-heading small { color: #7f8c81; font-size: var(--font-caption); }
.haul-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 7px; }
.haul-chip {
  --chip-tone: var(--quality-1);
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 8px 11px;
  border: 1px solid color-mix(in srgb, var(--chip-tone) 26%, var(--line));
  border-radius: 12px 4px 12px 4px;
  background: color-mix(in srgb, var(--chip-tone) 7%, transparent);
}
.haul-chip.quality-tone-1 { --chip-tone: var(--quality-1); }
.haul-chip.quality-tone-2 { --chip-tone: var(--quality-2); }
.haul-chip.quality-tone-3 { --chip-tone: var(--quality-3); }
.haul-chip.quality-tone-4 { --chip-tone: var(--quality-4); }
.haul-chip.quality-tone-5 { --chip-tone: var(--quality-5); }
.haul-chip.gear { --chip-tone: var(--gold); }
.haul-chip i { display: grid; width: 30px; height: 30px; place-items: center; color: var(--chip-tone); border-radius: 10px 3px 10px 3px; background: color-mix(in srgb, var(--chip-tone) 14%, transparent); }
.haul-chip > span { display: grid; gap: 1px; min-width: 0; }
.haul-chip strong { overflow: hidden; color: #dce4da; font-size: var(--font-copy); font-weight: 600; white-space: nowrap; text-overflow: ellipsis; }
.haul-chip small { color: var(--chip-tone); font-size: var(--font-caption); }
.haul-chip b { color: #e7dcc7; font-size: var(--font-copy); font-variant-numeric: tabular-nums; }
.haul-empty { margin: 0; color: #78857b; font-size: var(--font-copy); }
.haul-footer { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding-top: 15px; margin-top: 15px; border-top: 1px solid var(--line); }
.haul-footer .withdraw-button { margin-top: 0; }
.muted { color: #78857b !important; }
.event-log { display: grid; gap: 8px; margin-top: 12px; }
.event-log article { display: grid; grid-template-columns: 70px 1fr; gap: 10px; padding-top: 10px; border-top: 1px solid var(--line); }
.event-log article > span { color: #9f765d; font-size: var(--font-caption); }
.event-log strong { color: #dce4da; font-size: var(--font-copy); }
.event-log p { margin: 3px 0; color: #929f95; font-size: var(--font-copy); line-height: 1.6; }
.event-log small { color: #78857b; font-size: var(--font-caption); }
.route-tabs { display: flex; flex-wrap: wrap; gap: 9px; margin-bottom: 12px; }
.route-tab { display: grid; gap: 3px; min-width: 150px; padding: 10px 14px; text-align: left; color: #c6d0c5; border: 1px solid var(--line); border-radius: 13px; background: #121914; cursor: pointer; }
.route-tab.selected { color: #f0d6a8; border-color: var(--expedition-accent); background: color-mix(in srgb, var(--expedition-accent) 16%, #121914); }
.route-tab.locked { opacity: .6; }
.route-tab small { color: #8a968b; font-size: var(--font-caption); }
.route-tab strong { font-size: var(--font-body); }
.route-tab span { color: #90a091; font-size: var(--font-caption); }
.gear-panel { padding-top: 17px; margin-top: 17px; border-top: 1px solid var(--line); }
.gear-panel h3 { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
.gear-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 15px 0 11px; }
.gear-slot { display: grid; align-content: start; gap: 9px; padding: 12px; border: 1px solid var(--line); border-radius: 15px 5px 15px 5px; background: #121914; }
.gear-slot header { display: flex; align-items: center; gap: 8px; }
.gear-slot header strong { overflow: hidden; color: #e7dcc7; font-size: var(--font-copy); white-space: nowrap; text-overflow: ellipsis; }
.pack-trigger {
  width: 100%;
  min-height: 52px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 10px;
  padding: 8px 11px;
  text-align: left;
  color: #8c998f;
  border: 1px dashed #ffffff14;
  border-radius: 12px;
  background: #0d141086;
  cursor: pointer;
}
.pack-trigger.packed { color: #d9e2d6; border-style: solid; border-color: #8ead7130; background: #8ead710b; }
.pack-trigger .trigger-icon { width: 34px; height: 34px; display: grid; place-items: center; color: var(--gold); border-radius: 11px 4px 11px 4px; background: #d7ad5812; }
.pack-trigger:not(.packed) .trigger-icon { color: #7b867d; background: #ffffff07; }
.trigger-copy { display: grid; gap: 2px; min-width: 0; }
.trigger-copy strong { font-size: var(--font-copy); font-weight: 600; }
.trigger-copy small { overflow: hidden; color: #7f8c81; font-size: var(--font-caption); white-space: nowrap; text-overflow: ellipsis; }
.picker-caret { color: #7b867d; }

.hp-bar { display: block; overflow: hidden; height: 4px; margin-top: 5px; border-radius: 99px; background: #ffffff14; }
.hp-bar b { display: block; height: 100%; background: var(--leaf-bright); transition: width .32s ease; }
.hp-bar.enemy b { background: var(--autumn); }
.party-member.downed { opacity: .5; }
.member-vitals { display: flex; flex-wrap: wrap; align-items: baseline; gap: 5px; font-variant-numeric: tabular-nums; }
.member-vitals b { color: #dbe3da; font-size: var(--font-copy); }
.member-vitals em, .member-vitals u { color: #7f8c81; font-style: normal; text-decoration: none; }
.member-vitals u::before { content: '·'; margin-right: 5px; color: #55605a; }
.party-member.acting { border-color: #d79a68; box-shadow: 0 0 0 1px #d79a6840; }

.outcome-standalone { margin-bottom: 12px; }

.battle-card { display: grid; gap: 13px; padding: 20px; border-color: color-mix(in srgb, var(--autumn) 28%, var(--line)); }
.battle-heading { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; }
.battle-icon { width: 42px; height: 42px; display: grid; place-items: center; color: #f0c48e; border-radius: 14px 5px 14px 5px; background: #d79a6820; }
.battle-title .ui-section-title { margin: 3px 0 0; }
.log-button { display: inline-flex; align-items: center; gap: 6px; }

.strike-stage { position: relative; display: grid; gap: 8px; min-height: 84px; }
.strike-idle { display: flex; align-items: center; justify-content: center; gap: 8px; min-height: 84px; margin: 0; color: #6f7c72; font-size: var(--font-copy); border: 1px dashed #ffffff12; border-radius: 4px 16px 4px 16px; }
.skip-button { justify-self: end; color: #8b978c; }
.strike-stage.waiting .strike { opacity: .55; }
.strike-waiting { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin: 0; color: #8b978c; font-size: var(--font-caption); }
.strike-waiting i { display: block; width: 13px; height: 13px; border: 2px solid #ffffff1f; border-top-color: var(--gold); border-radius: 50%; animation: strike-spin .7s linear infinite; }
@keyframes strike-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .strike-waiting i { animation-duration: 2.4s; }
}

.initiative-track { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 13px 4px 13px 4px; background: #ffffff04; }
.track-label { margin-right: 5px; color: #7f8c81; font-size: var(--font-caption); letter-spacing: .08em; }

.enemy-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 9px; }
.enemy-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 11px;
  text-align: left;
  color: inherit;
  border: 1px solid var(--line);
  border-radius: 15px 5px 15px 5px;
  background: #121914;
  cursor: default;
}
.enemy-icon { width: 38px; height: 38px; display: grid; place-items: center; color: var(--autumn); border-radius: 12px 4px 12px 4px; background: #dc744514; }
.enemy-copy { display: grid; gap: 2px; min-width: 0; }
.enemy-copy strong { overflow: hidden; color: #e7dcc7; font-size: var(--font-copy); white-space: nowrap; text-overflow: ellipsis; }
.enemy-copy small { color: #8b978c; font-size: var(--font-caption); font-variant-numeric: tabular-nums; }
.enemy-card.boss { border-color: #d79a6860; }
.enemy-card.boss .enemy-icon { color: var(--gold); background: #d7ad5818; }
.enemy-multi { margin-left: 6px; padding: 1px 6px; color: var(--danger); font-size: 11px; font-weight: 600; border: 1px solid currentColor; border-radius: 999px; }
.enemy-card.acting { border-color: #d79a68; box-shadow: 0 0 0 1px #d79a6840; }
.enemy-card.defeated { opacity: .42; }
.enemy-card:disabled { opacity: .42; }
.enemy-card.defeated:disabled { opacity: .42; }
.enemy-row.targeting .enemy-card:not(:disabled) { cursor: pointer; border-color: color-mix(in srgb, var(--autumn) 55%, transparent); animation: aim-pulse 1.4s ease-in-out infinite; }
.enemy-row.targeting .enemy-card:not(:disabled):hover { background: #dc744516; }
.enemy-aim { position: absolute; right: 10px; top: 9px; color: var(--autumn); font-size: 11px; letter-spacing: .1em; }
.enemy-card { position: relative; }

.actor-extra { display: flex; align-items: center; gap: 7px; }
.ready-chip { display: inline-flex; align-items: center; gap: 7px; min-height: 32px; padding: 0 12px; color: #a9d2e8; border: 1px solid #70afd640; border-radius: 99px; background: #70afd611; cursor: pointer; }
.ready-note { color: #a9d2e8; font-size: var(--font-caption); }

.battle-actions { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }
.battle-action {
  --action-tone: var(--autumn);
  display: grid;
  justify-items: center;
  gap: 5px;
  min-height: 96px;
  padding: 13px 10px;
  text-align: center;
  color: inherit;
  border: 1px solid var(--line);
  border-radius: 15px 5px 15px 5px;
  background: #121914;
  cursor: pointer;
  transition: border-color .16s ease, transform .16s ease, background .16s ease;
}
.battle-action.item { --action-tone: var(--quality-2); }
.battle-action.flee { --action-tone: #8b978c; }
.battle-action:hover:not(:disabled) { transform: translateY(-2px); border-color: color-mix(in srgb, var(--action-tone) 45%, transparent); }
.battle-action.armed { border-color: var(--action-tone); background: color-mix(in srgb, var(--action-tone) 12%, #121914); }
.battle-action:disabled { opacity: .45; cursor: not-allowed; }
.action-mark { width: 40px; height: 40px; display: grid; place-items: center; color: var(--action-tone); border-radius: 13px 4px 13px 4px; background: color-mix(in srgb, var(--action-tone) 14%, transparent); }
.battle-action strong { color: #e7dcc7; font-size: var(--font-copy); }
.battle-action small { color: #7f8c81; font-size: var(--font-caption); line-height: 1.45; }

@keyframes aim-pulse {
  0%, 100% { box-shadow: 0 0 0 0 #dc744500; }
  50% { box-shadow: 0 0 0 3px #dc744522; }
}
@media (prefers-reduced-motion: reduce) {
  .enemy-row.targeting .enemy-card:not(:disabled) { animation: none; }
  .battle-action:hover:not(:disabled) { transform: none; }
}
.gear-haul { display: inline-flex; align-items: center; gap: 6px; color: #d8bb7c !important; }
@media (max-width: 720px) {
  .result-card { grid-template-columns: 1fr; gap: 14px; }
  .result-header, .dice-panel, .result-summary { grid-column: 1; }
  .party-selects, .gear-grid { grid-template-columns: 1fr; }
  .party-row.in-delve { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
  .party-row.in-delve .party-member { align-items: flex-start; flex-direction: column; gap: 6px; padding: 8px; }
  .party-row.in-delve .party-member > span { width: 100%; min-width: 0; }
  .party-row.in-delve .party-member strong { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .party-row.in-delve .member-weapon { display: none; }
  .battle-heading { grid-template-columns: auto minmax(0, 1fr); }
  .log-button { grid-column: 2; justify-self: start; padding: 0; }
  .outcome-card { grid-template-columns: auto minmax(0, 1fr); }
  .outcome-next { grid-column: 1 / -1; justify-content: center; }
  .battle-action { min-height: 88px; padding: 11px 7px; }
  .battle-action small { font-size: 11px; }
  .run-status { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .choice-button { align-items: flex-start; flex-direction: column; }
  .choice-meta { display: flex; gap: 12px; text-align: left; }
  .choice-meta b { justify-content: flex-start; }
  .dice-heading { align-items: flex-start; flex-wrap: wrap; }
  .dice-heading > b { width: 100%; padding-left: 40px; }
  .dice-track { width: 100%; }
}
</style>
