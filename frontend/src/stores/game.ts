import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api, ApiError } from '@/api'
import { PRODUCTION_COPY, type ProductionIndustry } from '@/lib/production'
import { qualityName } from '@/lib/quality'
import { rewardSummary } from '@/lib/rewards'
import { useStoryStore } from '@/stores/story'
import { useTickerStore } from '@/stores/ticker'
import type {
  AccountState,
  ActionResult,
  AchievementUnlock,
  AchievementClaimResult,
  AnimalBornResult,
  BigCatchResult,
  CareResult,
  CastResult,
  CommissionBoard,
  CrossoverCampaign,
  CrossoverClaimResult,
  DelveBattleActionResult,
  ExplorationResolutionResult,
  GachaResult,
  GameState,
  LivestockCollectResult,
  MailboxState,
  MailClaimResult,
  PartnerSelectPayload,
  PartnerSelectUseResult,
  PondHarvestResult,
  Reward,
  TributeDeliveryResult,
} from '@/types'

export interface ProductionOutcome {
  completed_count?: number
  experience?: number
  refunded_stamina?: number
  quantity?: number
  quality?: number
  quality_name?: string
  drops?: Array<{
    item_id: string
    quantity: number
    quality: number
    quality_name: string
    item?: { name: string } | null
  }>
  refunded_inputs?: Array<{
    item_id: string
    quantity: number
    quality: number
    quality_name: string
    item?: { name: string } | null
    trait_name?: string
  }>
}

interface ActionOptions {
  payload?: unknown
  method?: string
  successMessage?: string
  cue?: string
}

const API_ROOT = '/api/red-leaf-town'

export const useGameStore = defineStore('game', () => {
  const ticker = useTickerStore()
  const story = useStoryStore()
  const status = ref<'checking' | 'guest' | 'ready' | 'error'>('checking')
  const state = ref<GameState | null>(null)
  const account = ref<AccountState | null>(null)
  const pending = ref(new Set<string>())
  const notice = ref('')
  const craftingOutcome = ref<ProductionOutcome | null>(null)
  const error = ref('')
  const serverOffsetMs = ref(0)
  let acceptedAt = 0

  const player = computed(() => state.value?.player || null)
  const busy = computed(() => pending.value.size > 0)
  const serverNow = computed(() => Math.floor((ticker.now + serverOffsetMs.value) / 1000))

  const inventoryMap = computed(() => {
    const result = new Map<string, GameState['inventory'][number]>()
    for (const item of state.value?.inventory || []) {
      const current = result.get(item.item_id)
      result.set(item.item_id, current ? { ...current, quantity: current.quantity + item.quantity } : item)
    }
    return result
  })

  const liveStamina = computed(() => {
    const current = player.value
    if (!current) return 0
    // 体力药和枫火购买能把体力顶到上限之上，溢出期间不再自然回复，也不能夹回上限。
    if (current.stamina >= current.stamina_cap) return current.stamina
    const elapsed = Math.max(0, serverNow.value - current.stamina_updated_at)
    const gained = Math.floor(elapsed / current.stamina_restore_seconds)
    return Math.min(current.stamina_cap, current.stamina + gained)
  })

  const staminaNextIn = computed(() => {
    const current = player.value
    if (!current || liveStamina.value >= current.stamina_cap) return 0
    const elapsed = Math.max(0, serverNow.value - current.stamina_updated_at)
    return current.stamina_restore_seconds - (elapsed % current.stamina_restore_seconds)
  })

  const readyCounts = computed(() => {
    const snapshot = state.value
    const counts = { plots: 0, gathering: 0, mining: 0, crafting: 0, total: 0 }
    if (!snapshot) return counts
    counts.plots = snapshot.plots.filter((entry) => entry.ready).length
    counts.gathering = snapshot.gathering_sites.filter((entry) => entry.ready).length
    counts.mining = snapshot.mining_sites.filter((entry) => entry.ready).length
    counts.crafting = snapshot.crafting_stations.filter((entry) => entry.ready).length
    counts.total = counts.plots + counts.gathering + counts.mining + counts.crafting
    return counts
  })

  const nextReadyAt = computed(() => {
    const snapshot = state.value
    if (!snapshot) return 0
    const stamps: number[] = []
    for (const plot of snapshot.plots) {
      if (!plot.empty && !plot.ready && plot.ready_at) stamps.push(plot.ready_at)
    }
    const nodes = [...snapshot.gathering_sites, ...snapshot.mining_sites, ...snapshot.crafting_stations]
    for (const node of nodes) {
      if (!node.empty && !node.ready && node.task_snapshot) stamps.push(node.task_snapshot.ready_at)
    }
    return stamps.length ? Math.min(...stamps) : 0
  })

  // 剧情结算完的存档直接采用，伙伴加入之类的奖励不用等下一次轮询。
  story.bindState((settled) => {
    acceptState(settled.state, performance.now())
    const granted = (settled.result as { granted?: Reward | null }).granted
    const text = granted ? rewardText(granted) : ''
    if (text) showNotice(text)
    showAchievementNotice((settled.result as { achievements?: AchievementUnlock[] }).achievements)
  })

  function rewardText(reward: Reward) {
    const summary = rewardSummary(reward)
    return summary ? `获得 ${summary}` : ''
  }

  function isPending(key: string) {
    return pending.value.has(key)
  }

  function isPendingPrefix(prefix: string) {
    for (const key of pending.value) {
      if (key.startsWith(prefix)) return true
    }
    return false
  }

  function acceptState(nextState: GameState, startedAt: number) {
    if (startedAt < acceptedAt) return
    acceptedAt = startedAt
    state.value = nextState
    serverOffsetMs.value = nextState.server_time * 1000 - Date.now()
    if (account.value) account.value.state = nextState
  }

  async function initialize() {
    status.value = 'checking'
    error.value = ''
    const params = new URLSearchParams(location.search)
    const oauthError = params.get('oauth_error')
    if (oauthError) {
      error.value = oauthError
      history.replaceState({}, '', `${location.pathname}${location.hash}`)
    }
    try {
      const startedAt = performance.now()
      account.value = await api<AccountState>(`${API_ROOT}/account`)
      acceptState(account.value.state, startedAt)
      status.value = 'ready'
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) status.value = 'guest'
      else {
        status.value = 'error'
        error.value = caught instanceof Error ? caught.message : '加载失败'
      }
    }
  }

  async function refresh(silent = false) {
    if (status.value !== 'ready' || isPending('refresh')) return
    pending.value.add('refresh')
    try {
      const startedAt = performance.now()
      acceptState(await api<GameState>(`${API_ROOT}/state`), startedAt)
    } catch (caught) {
      if (!silent) fail(caught)
    } finally {
      pending.value.delete('refresh')
    }
  }

  async function action(key: string, path: string, options: ActionOptions = {}) {
    if (pending.value.has(key)) return undefined
    pending.value.add(key)
    error.value = ''
    const startedAt = performance.now()
    try {
      const result = await api<ActionResult>(path, {
        method: options.method || 'POST',
        body: options.payload === undefined ? undefined : JSON.stringify(options.payload),
      })
      acceptState(result.state, startedAt)
      if (options.successMessage) showNotice(options.successMessage)
      showAchievementNotice((result.result as { achievements?: AchievementUnlock[] }).achievements)
      if (options.cue) story.cue(options.cue)
      return result.result
    } catch (caught) {
      fail(caught)
      return undefined
    } finally {
      pending.value.delete(key)
    }
  }

  function refundText(result: ProductionOutcome | undefined) {
    // 伙伴特性退回的原料是悄悄进仓库的，不写在提示里玩家根本看不出触发过。
    const staminaText = result?.refunded_stamina ? `（物尽其用返还 ${result.refunded_stamina} 点体力）` : ''
    if (!result?.refunded_inputs?.length) return staminaText
    const summary = result.refunded_inputs
      .map((entry) => `${entry.quality_name || qualityName(entry.quality)}${entry.item?.name || entry.item_id}×${entry.quantity}`)
      .join('、')
    const traitName = result.refunded_inputs.find((entry) => entry.trait_name)?.trait_name
    return `（${traitName ? `${traitName}返还 ` : '返还 '}${summary}）${staminaText}`
  }

  function outcomeText(result: ProductionOutcome | undefined, verb: string, noun: string) {
    if (!result) return ''
    if (result.drops?.length) {
      const summary = result.drops
        .map((drop) => `${drop.quality_name || qualityName(drop.quality)}${drop.item?.name || drop.item_id}×${drop.quantity}`)
        .join('、')
      return `${verb}${summary}${refundText(result)}`
    }
    const quality = result.quality_name || qualityName(result.quality)
    return `${verb} ${result.quantity} 个${quality}${noun}${refundText(result)}`
  }

  function buy(shopId: string, quantity = 1) {
    return action(`shop:${shopId}:${quantity}`, `${API_ROOT}/shop/buy`, {
      payload: { shop_id: shopId, quantity },
      successMessage: '种子已放入仓库',
      cue: 'action:buy',
    })
  }

  function startExploration(
    expeditionId: string,
    partnerIds: string[],
    leaderPartnerId: string,
    loadout?: Record<string, { weapon_item_id?: string; accessory_item_id?: string }>,
    carriedItems?: Array<{ item_id: string; quality: number; quantity: number }>,
  ) {
    return action(`exploration:start:${expeditionId}`, `${API_ROOT}/exploration/${expeditionId}/start`, {
      payload: {
        partner_ids: partnerIds,
        leader_partner_id: leaderPartnerId,
        ...(loadout ? { loadout } : {}),
        ...(carriedItems ? { carried_items: carriedItems } : {}),
      },
      successMessage: '队伍已经出发',
    })
  }

  async function resolveDelveBattleAction(
    battleAction: 'attack' | 'item' | 'flee' | 'advantage',
    target = '',
    itemId = '',
    itemQuality = 0,
  ) {
    const result = await action('exploration:battle', `${API_ROOT}/exploration/current/battle/act`, {
      payload: { action: battleAction, target, item_id: itemId, item_quality: itemQuality },
    }) as DelveBattleActionResult | undefined
    if (result?.outcome === 'wiped') showNotice('队伍失去了战斗力，冻结的战利品全部损失')
    return result
  }

  async function resolveExploration(choiceId: string, actorPartnerId = '') {
    const result = await action('exploration:resolve', `${API_ROOT}/exploration/current/resolve`, {
      payload: { choice_id: choiceId, actor_partner_id: actorPartnerId },
    }) as ExplorationResolutionResult | undefined
    // 敌人抢到先攻并在我方出手前就打光了队伍，这一趟当场结束。
    if (result?.outcome === 'wiped') showNotice('队伍失去了战斗力，冻结的战利品全部损失')
    return result
  }

  async function withdrawExploration() {
    const result = await action('exploration:withdraw', `${API_ROOT}/exploration/current/withdraw`) as
      | (ProductionOutcome & { depth?: number })
      | undefined
    if (result) showNotice(outcomeText(result, '返程带回了', '战利品') || `从第 ${result.depth || 0} 段路线返程`)
    return result
  }

  function sell(itemId: string, quantity: number, quality: number | null = null) {
    return action(`inventory:${itemId}:${quality || 0}:${quantity}`, `${API_ROOT}/inventory/${itemId}/sell`, {
      payload: { quantity, quality: quality || 0 },
      successMessage: '交易完成',
      cue: 'action:sell',
    })
  }

  function plant(slot: number, cropId: string, taskItemId = '') {
    return action(`plot:${slot}:plant:${cropId}`, `${API_ROOT}/plots/${slot}/plant`, {
      payload: { crop_id: cropId, task_item_id: taskItemId },
      successMessage: '种子已经种下',
      cue: 'action:plant',
    })
  }

  async function harvest(slot: number) {
    const result = (await action(`plot:${slot}:harvest`, `${API_ROOT}/plots/${slot}/harvest`, {
      cue: 'action:harvest',
    })) as
      | ProductionOutcome
      | undefined
    if (result) showNotice(outcomeText(result, '收获了', '产物'))
    return result
  }

  function assignPartner(slot: number, partnerId: string | null) {
    return action(`plot:${slot}:partner`, `${API_ROOT}/plots/${slot}/partners`, {
      payload: { partner_id: partnerId || '' },
      method: 'PUT',
      successMessage: partnerId ? '伙伴已安排到这块土地' : '伙伴已撤下',
    })
  }

  function unlockTalent(nodeId: string) {
    return action(`talent:${nodeId}`, `${API_ROOT}/talents/${nodeId}/unlock`, {
      successMessage: '天赋已经点亮',
      cue: 'action:unlock_talent',
    })
  }

  async function deliverTribute(portalId: string, tributeId: string, quantity: number) {
    const result = (await action(
      `portal:${portalId}:${tributeId}`,
      `${API_ROOT}/portals/${portalId}/tributes/${tributeId}/deliver`,
      { payload: { quantity }, cue: 'action:deliver_tribute' },
    )) as TributeDeliveryResult | undefined
    if (!result) return undefined
    // 传送门开了是主线进度，让剧情层也有机会插话。
    if (result.portal_completed) story.cue('action:portal_complete')
    showNotice(deliveryText(result))
    return result
  }

  function deliveryText(result: TributeDeliveryResult) {
    if (result.portal_completed) return `${result.portal_name}开启了`
    if (result.tribute_completed) return '这一项贡品已经交齐'
    return `交付了 ${result.delivered} 个，还差 ${result.required - result.total_delivered} 个`
  }

  async function loadCommissionBoard() {
    if (isPending('commissions:board')) return undefined
    pending.value.add('commissions:board')
    try {
      return await api<CommissionBoard>(`${API_ROOT}/commissions/board`)
    } catch (caught) {
      fail(caught)
      return undefined
    } finally {
      pending.value.delete('commissions:board')
    }
  }

  function submitCommission() {
    return action('commissions:submit', `${API_ROOT}/commissions/submit`, {
      successMessage: '委托已经交付',
      cue: 'action:submit_commission',
    })
  }

  function forwardCommission() {
    return action('commissions:forward', `${API_ROOT}/commissions/forward`, {
      successMessage: '委托已经放进公共转发池',
    })
  }

  function withdrawCommission() {
    return action('commissions:withdraw', `${API_ROOT}/commissions/withdraw`, {
      successMessage: '委托已经收回',
    })
  }

  function takeCommission(commissionId: string) {
    return action(`commissions:take:${commissionId}`, `${API_ROOT}/commissions/${commissionId}/take`, {
      successMessage: '你替对方跑完了这一趟',
      cue: 'action:submit_commission',
    })
  }

  async function loadMailbox() {
    if (isPending('mail:list')) return undefined
    pending.value.add('mail:list')
    try {
      return await api<MailboxState>(`${API_ROOT}/mail`)
    } catch (caught) {
      fail(caught)
      return undefined
    } finally {
      pending.value.delete('mail:list')
    }
  }

  function readMail(mailId: string) {
    return action(`mail:read:${mailId}`, `${API_ROOT}/mail/${mailId}/read`)
  }

  async function claimMail(mailId: string) {
    const result = (await action(`mail:claim:${mailId}`, `${API_ROOT}/mail/${mailId}/claim`)) as
      | MailClaimResult
      | undefined
    if (result) showNotice(rewardText(result.granted) || '附件已经收下')
    return result
  }

  function productionBase(industry: ProductionIndustry, nodeId: string) {
    return `${API_ROOT}/${PRODUCTION_COPY[industry].endpoint}/${nodeId}`
  }

  function assignProductionPartner(industry: ProductionIndustry, nodeId: string, partnerId: string | null) {
    return action(`${industry}:${nodeId}:partner`, `${productionBase(industry, nodeId)}/partner`, {
      payload: { partner_id: partnerId || '' },
      method: 'PUT',
      successMessage: partnerId ? '伙伴已安排到岗位' : '伙伴已撤下',
    })
  }

  function startProduction(industry: ProductionIndustry, nodeId: string, taskId: string, taskItemId = '', quantity = 1) {
    const copy = PRODUCTION_COPY[industry]
    return action(`${industry}:${nodeId}:start:${taskId}`, `${productionBase(industry, nodeId)}/start`, {
      payload: { [copy.startPayloadKey]: taskId, task_item_id: taskItemId, ...(industry === 'crafting' ? { quantity } : {}) },
      successMessage: '任务已经开始',
      cue: `action:start_${industry}`,
    })
  }

  async function collectProduction(industry: ProductionIndustry, nodeId: string) {
    const copy = PRODUCTION_COPY[industry]
    const result = (await action(`${industry}:${nodeId}:collect`, `${productionBase(industry, nodeId)}/collect`, {
      cue: `action:collect_${industry}`,
    })) as
      | ProductionOutcome
      | undefined
    if (result) {
      if (industry === 'crafting') craftingOutcome.value = result
      else showNotice(outcomeText(result, copy.collectVerb, copy.collectNoun))
    }
    return result
  }

  function dropText(drops: Array<{ quantity: number; quality: number | null; quality_name: string | null; name: string }>) {
    return drops
      .map((drop) => `${drop.quality_name || qualityName(drop.quality)}${drop.name}×${drop.quantity}`)
      .join('、')
  }

  function assignFishingCompanion(partnerId: string | null) {
    return action('aquatic:companion', `${API_ROOT}/fishing/companion`, {
      payload: { partner_id: partnerId || '' },
      method: 'PUT',
      successMessage: partnerId ? '伙伴陪你去钓鱼' : '伙伴回去了',
    })
  }

  async function castLine(spotId: string) {
    // 抛竿是高频动作，带上幂等 ID，重试不会重复发放。
    const requestId = crypto.randomUUID()
    const result = (await action(`aquatic:cast:${spotId}`, `${API_ROOT}/fishing/spots/${spotId}/cast`, {
      payload: { request_id: requestId },
      cue: 'action:cast_line',
    })) as CastResult | undefined
    if (!result || result.duplicate) return result
    if (result.big_catch) showNotice(`有大家伙咬钩了！`)
    else if (result.drops.length) showNotice(`钓上${dropText(result.drops)}`)
    showCodexRewards(result.codex_milestones)
    return result
  }

  async function resolveBigCatch(nextAction: 'fight' | 'release') {
    const result = (await action(`aquatic:big-catch:${nextAction}`, `${API_ROOT}/fishing/big-catch`, {
      payload: { action: nextAction },
      cue: 'action:big_catch',
    })) as BigCatchResult | undefined
    if (!result) return result
    if (result.success) showNotice(`拉上来了！${dropText(result.drops)} ${result.size} 厘米`)
    else showNotice(`跑了，只剩${dropText(result.drops)}`)
    showCodexRewards(result.codex_milestones)
    return result
  }

  function showCodexRewards(milestones: CastResult['codex_milestones']) {
    for (const milestone of milestones) {
      showNotice(`鱼类图鉴达成「${milestone.name}」，已自动发放：${rewardSummary(milestone.granted)}`)
    }
  }

  function buildPond(pondId: string) {
    return action(`aquatic:pond:${pondId}:build`, `${API_ROOT}/ponds/${pondId}/build`, {
      successMessage: '塘挖好了',
    })
  }

  function assignPondPartner(pondId: string, partnerId: string | null) {
    return action(`aquatic:pond:${pondId}:partner`, `${API_ROOT}/ponds/${pondId}/partner`, {
      payload: { partner_id: partnerId || '' },
      method: 'PUT',
      successMessage: partnerId ? '伙伴开始看塘' : '伙伴已撤下',
    })
  }

  function stockPond(pondId: string, speciesId: string, quantity: number) {
    return action(`aquatic:pond:${pondId}:stock`, `${API_ROOT}/ponds/${pondId}/stock`, {
      payload: { species_id: speciesId, quantity },
      successMessage: '鱼苗下塘了',
      cue: 'action:stock_pond',
    })
  }

  async function harvestPond(pondId: string, quantity: number) {
    const result = (await action(`aquatic:pond:${pondId}:harvest`, `${API_ROOT}/ponds/${pondId}/harvest`, {
      payload: { quantity },
      cue: 'action:harvest_pond',
    })) as PondHarvestResult | undefined
    if (result) showNotice(`捞起${dropText(result.drops)}`)
    return result
  }

  function buildLivestockFacility(facilityId: string) {
    return action(`livestock:${facilityId}:build`, `${API_ROOT}/livestock/facilities/${facilityId}/build`, {
      successMessage: '盖好了',
    })
  }

  function upgradeLivestockFacility(facilityId: string, targetTier: number) {
    return action(`livestock:${facilityId}:upgrade`, `${API_ROOT}/livestock/facilities/${facilityId}/upgrade`, {
      payload: { target_tier: targetTier }, successMessage: '扩建完成',
    })
  }

  function assignLivestockPartner(facilityId: string, partnerId: string | null) {
    return action(`livestock:${facilityId}:partner`, `${API_ROOT}/livestock/facilities/${facilityId}/partner`, {
      payload: { partner_id: partnerId || '' },
      method: 'PUT',
      successMessage: partnerId ? '伙伴开始照看牲口' : '伙伴已撤下',
    })
  }

  async function buyAnimal(facilityId: string, speciesId: string, nickname = '') {
    const result = (await action(
      `livestock:${facilityId}:buy:${speciesId}`,
      `${API_ROOT}/livestock/facilities/${facilityId}/buy`,
      { payload: { species_id: speciesId, nickname } },
    )) as AnimalBornResult | undefined
    if (result) showNotice(`${result.animal.name}住了进来`)
    return result
  }

  async function collectLivestock(facilityId: string, animalId = '') {
    const result = (await action(
      `livestock:${facilityId}:collect${animalId ? `:${animalId}` : ''}`,
      `${API_ROOT}/livestock/facilities/${facilityId}/collect`,
      { payload: { animal_id: animalId }, cue: 'action:collect_livestock' },
    )) as LivestockCollectResult | undefined
    if (result) showNotice(`收下${dropText(result.drops)}`)
    return result
  }

  async function careAnimal(animalId: string) {
    const result = (await action(`livestock:animal:${animalId}:care`, `${API_ROOT}/livestock/animals/${animalId}/care`, {
      cue: 'action:care_animal',
    })) as CareResult | undefined
    if (result) {
      const full = result.affection >= result.affection_cap
      showNotice(full ? '它已经很黏你了' : `亲密度 ${result.affection_before} → ${result.affection}`)
    }
    return result
  }

  async function incubateEgg(facilityId: string, quality: number, nickname = '', speciesId = '') {
    const result = (await action(
      `livestock:${facilityId}:incubate:${quality}`,
      `${API_ROOT}/livestock/facilities/${facilityId}/incubate`,
      { payload: { quality, nickname, species_id: speciesId } },
    )) as AnimalBornResult | undefined
    if (result) showNotice('蛋已入窝')
    return result
  }

  async function breedAnimals(facilityId: string, parentIds: string[], nickname = '') {
    const result = (await action(
      `livestock:${facilityId}:breed`,
      `${API_ROOT}/livestock/facilities/${facilityId}/breed`,
      { payload: { parent_ids: parentIds, nickname } },
    )) as AnimalBornResult | undefined
    if (result) {
      const calf = result.animal
      showNotice(`${calf.name}出生了：品质基因 ${calf.quality_gene}、产量基因 ${calf.yield_gene}`)
    }
    return result
  }

  async function sellAnimal(animalId: string) {
    const result = (await action(`livestock:animal:${animalId}:sell`, `${API_ROOT}/livestock/animals/${animalId}/sell`, {})) as
      | { price: number }
      | undefined
    if (result) showNotice(`卖了 ${result.price} 红叶币`)
    return result
  }

  function depositFeed(itemId: string, quality: number, count: number) {
    return action(`aquatic:feed:${itemId}:${quality}`, `${API_ROOT}/feed-slot/deposit`, {
      payload: { item_id: itemId, quality, count },
      successMessage: '饲料已经倒进槽里',
    })
  }

  function dumpFeed() {
    return action('aquatic:feed:dump', `${API_ROOT}/feed-slot/dump`, {
      successMessage: '饲料槽已经倒空',
    })
  }

  async function loadCrossoverCampaigns() {
    if (isPending('crossover:list')) return undefined
    pending.value.add('crossover:list')
    try {
      return await api<{ campaigns: CrossoverCampaign[] }>(`${API_ROOT}/crossover`)
    } catch (caught) {
      fail(caught)
      return undefined
    } finally {
      pending.value.delete('crossover:list')
    }
  }

  async function claimCrossover(campaignId: string) {
    const result = (await action(`crossover:claim:${campaignId}`, `${API_ROOT}/crossover/${campaignId}/claim`)) as
      | CrossoverClaimResult
      | undefined
    if (result) {
      const leaves = result.granted.guide_leaves
      showNotice(leaves ? `联动礼物收下了，引路枫叶 ×${leaves}` : rewardText(result.granted) || '联动礼物收下了')
    }
    return result
  }

  async function loadPartnerSelectCandidates() {
    if (isPending('partner-select:candidates')) return undefined
    pending.value.add('partner-select:candidates')
    try {
      return await api<PartnerSelectPayload>(`${API_ROOT}/partner-select/candidates`)
    } catch (caught) {
      fail(caught)
      return undefined
    } finally {
      pending.value.delete('partner-select:candidates')
    }
  }

  async function useInventoryItem(itemId: string, partnerId: string) {
    const result = (await action(`inventory:${itemId}:use`, `${API_ROOT}/inventory/${itemId}/use`, {
      payload: { partner_id: partnerId },
      cue: 'action:use_item',
    })) as PartnerSelectUseResult | undefined
    if (result) {
      const marks = result.companion_marks_granted ? `，同行印记 ×${result.companion_marks_granted}` : ''
      showNotice(`${result.name} 加入了小镇${marks}`)
    }
    return result
  }

  function convertMapleFlame(quantity: number) {
    return action(`gacha:convert:${quantity}`, `${API_ROOT}/gacha/convert`, {
      payload: { quantity },
      successMessage: '引路枫叶已经点亮',
    })
  }

  async function recruit(count: 1 | 10, poolId: string) {
    const requestId = crypto.randomUUID()
    const result = await action(`gacha:pull:${requestId}`, `${API_ROOT}/gacha/pull`, {
      payload: { count, request_id: requestId, pool_id: poolId },
    })
    return result as unknown as GachaResult | undefined
  }

  function trainPartner(partnerId: string, itemId: string, quantity = 1) {
    return action(`partner:${partnerId}:train:${itemId}`, `${API_ROOT}/partners/${partnerId}/train`, {
      payload: { item_id: itemId, quantity },
      successMessage: '伙伴获得了经验',
    })
  }

  function starUpPartner(partnerId: string) {
    return action(`partner:${partnerId}:star-up`, `${API_ROOT}/partners/${partnerId}/star-up`, {
      successMessage: '伙伴升星完成',
    })
  }

  function selectPartnerArtwork(partnerId: string, stage: number) {
    return action(`partner:${partnerId}:artwork`, `${API_ROOT}/partners/${partnerId}/artwork`, {
      payload: { artwork_stage: stage },
    })
  }

  function breakthroughPartner(partnerId: string) {
    return action(`partner:${partnerId}:breakthrough`, `${API_ROOT}/partners/${partnerId}/breakthrough`, {
      successMessage: '伙伴突破完成',
    })
  }

  function useActiveTaskItem(industry: ProductionIndustry | 'farming', slotId: string | number, taskItemId: string) {
    return action(`${industry}:${slotId}:item:${taskItemId}`, `${API_ROOT}/tasks/use-item`, {
      payload: { industry, slot_id: String(slotId), task_item_id: taskItemId },
      successMessage: '特殊道具已经生效',
    })
  }

  function cancelTask(industry: ProductionIndustry | 'farming', slotId: string | number) {
    return action(`${industry}:${slotId}:cancel`, `${API_ROOT}/tasks/cancel`, {
      payload: { industry, slot_id: String(slotId) },
      successMessage: industry === 'crafting'
        ? '加工已取消，未开始部分的预留资源已退回，已完成产物可继续领取'
        : '任务已经取消，消耗的资源已退回',
      cue: `action:cancel_${industry}`,
    })
  }

  function redeemCode(code: string) {
    return action('monthly-card:redeem', `${API_ROOT}/monthly-card/redeem`, {
      payload: { code },
      successMessage: '月卡已激活',
    })
  }

  function claimMonthlyCard() {
    return action('monthly-card:claim', `${API_ROOT}/monthly-card/claim`, {
      successMessage: '今日月卡奖励已领取',
    })
  }

  function useStaminaPotion() {
    return action('stamina:potion', `${API_ROOT}/stamina/potion`, {
      successMessage: '喝下绯恩特调，体力回来了',
    })
  }

  function buyStamina() {
    return action('stamina:purchase', `${API_ROOT}/stamina/purchase`, {
      successMessage: '体力已补充',
    })
  }

  async function createBindingCode() {
    if (isPending('binding-code')) return null
    pending.value.add('binding-code')
    try {
      return await api<{ binding_code: string; command: string; expires_in: number }>(
        `${API_ROOT}/account/binding-code`,
        { method: 'POST' },
      )
    } catch (caught) {
      fail(caught)
      return null
    } finally {
      pending.value.delete('binding-code')
    }
  }

  async function logout() {
    try {
      const result = await api<{ logout_url: string }>(`${API_ROOT}/logout`, { method: 'POST' })
      account.value = null
      state.value = null
      status.value = 'guest'
      window.location.assign(result.logout_url)
    } catch (caught) {
      fail(caught)
    }
  }

  function effectiveNow() {
    return Math.floor((Date.now() + serverOffsetMs.value) / 1000)
  }

  const noticeQueue: string[] = []
  let noticeShowing = false

  function showNotice(message: string) {
    noticeQueue.push(message)
    if (!noticeShowing) showNextNotice()
  }

  function showNextNotice() {
    const message = noticeQueue.shift()
    noticeShowing = message !== undefined
    notice.value = message ?? ''
    if (message === undefined) return
    window.setTimeout(() => {
      showNextNotice()
    }, Math.min(10000, Math.max(2800, message.length * 100)))
  }

  function showAchievementNotice(achievements: AchievementUnlock[] | undefined) {
    if (!achievements?.length) return
    const names = achievements.length === 1
      ? `「${achievements[0].name}」`
      : `「${achievements[0].name}」等 ${achievements.length} 项`
    window.setTimeout(() => showNotice(`达成成就${names}，请前往成就册领取奖励`), 80)
  }

  async function claimAchievement(achievementId: string) {
    const result = await action(
      `achievement:claim:${achievementId}`,
      `${API_ROOT}/achievements/${achievementId}/claim`,
    ) as AchievementClaimResult | undefined
    if (result) showNotice(`成就奖励领取成功，获得 ${result.maple_flame} 枫火`)
    return result
  }

  async function claimAllAchievements() {
    const result = await action(
      'achievement:claim-all',
      `${API_ROOT}/achievements/claim-all`,
    ) as AchievementClaimResult | undefined
    if (result?.claimed.length) showNotice(`领取 ${result.claimed.length} 项成就奖励，获得 ${result.maple_flame} 枫火`)
    return result
  }

  function fail(caught: unknown) {
    error.value = caught instanceof Error ? caught.message : '操作失败'
    showNotice(error.value)
  }

  return {
    status,
    state,
    account,
    player,
    inventoryMap,
    pending,
    busy,
    notice,
    craftingOutcome,
    error,
    serverNow,
    liveStamina,
    staminaNextIn,
    readyCounts,
    nextReadyAt,
    isPending,
    isPendingPrefix,
    redeemCode,
    claimMonthlyCard,
    useStaminaPotion,
    buyStamina,
    initialize,
    refresh,
    buy,
    buildSailingShip: () => action('sailing:build', `${API_ROOT}/sailing/build`, {
      payload: {}, successMessage: '初帆号建造完成，可以准备出航了',
    }),
    startSailing: (routeId: string, partnerIds: string[], supplyId: string, requestId: string) =>
      action('sailing:start', `${API_ROOT}/sailing/start`, {
        payload: { route_id: routeId, partner_ids: partnerIds, supply_id: supplyId, request_id: requestId },
        successMessage: '船已离港，伙伴将按时带回收获',
      }),
    cancelSailing: (runId: string) => action('sailing:cancel', `${API_ROOT}/sailing/cancel`, {
      payload: { run_id: runId }, successMessage: '航行已取消，红叶币和补给已退回，体力按上限返还',
    }),
    collectSailing: (runId: string) => action('sailing:collect', `${API_ROOT}/sailing/collect`, {
      payload: { run_id: runId }, successMessage: '航海收获已放入仓库',
    }),
    upgradeSailing: (kind: string, expectedLevel: number) => action(`sailing:upgrade:${kind}`, `${API_ROOT}/sailing/upgrade`, {
      payload: { kind, expected_level: expectedLevel }, successMessage: '船舶改装完成',
    }),
    startExploration,
    resolveExploration,
    resolveDelveBattleAction,
    withdrawExploration,
    sell,
    plant,
    harvest,
    assignPartner,
    unlockTalent,
    assignProductionPartner,
    startProduction,
    collectProduction,
    assignFishingCompanion,
    castLine,
    resolveBigCatch,
    upgradeFacility: (id: string, partnerId = '') => action(`facility:${id}`, `${API_ROOT}/facilities/${id}`, { payload: { partner_id: partnerId }, successMessage: '设施改良完成' }),
    assignRefiningPartner: (partnerId: string | null) => action('refining:partner', `${API_ROOT}/refining/partner`, { method: 'PUT', payload: { partner_id: partnerId || '' }, successMessage: '精制房伙伴已调整' }),
    startRefining: (slot: number, recipeId: string, quality: number) => action(`refining:${slot}`, `${API_ROOT}/refining/${slot}/start`, { payload: { recipe_id: recipeId, quality }, successMessage: '开始精制' }),
    finishRefining: (slot: number, taskId: string, cancel = false) => action(`refining:${slot}`, `${API_ROOT}/refining/${slot}/finish`, { payload: { task_id: taskId, cancel }, successMessage: '物产已放回仓库' }),
    buildPond,
    assignPondPartner,
    stockPond,
    harvestPond,
    buildLivestockFacility,
    upgradeLivestockFacility,
    assignLivestockPartner,
    buyAnimal,
    collectLivestock,
    careAnimal,
    incubateEgg,
    breedAnimals,
    sellAnimal,
    depositFeed,
    dumpFeed,
    convertMapleFlame,
    loadPartnerSelectCandidates,
    useInventoryItem,
    loadCrossoverCampaigns,
    claimCrossover,
    recruit,
    trainPartner,
    starUpPartner,
    breakthroughPartner,
    selectPartnerArtwork,
    useActiveTaskItem,
    cancelTask,
    deliverTribute,
    loadCommissionBoard,
    submitCommission,
    forwardCommission,
    withdrawCommission,
    takeCommission,
    loadMailbox,
    readMail,
    claimMail,
    claimSummerReward: (points: number) => action(`summer:claim:${points}`, `${API_ROOT}/summer/rewards/${points}/claim`, { successMessage: '活动回礼已放入仓库' }),
    claimSummerSkin: (skinId: string) => action(`summer:skin:${skinId}`, `${API_ROOT}/summer/skins/${skinId}/claim`, { successMessage: '限定立绘已解锁' }),
    claimAchievement,
    claimAllAchievements,
    createBindingCode,
    logout,
    effectiveNow,
    showNotice,
  }
})
