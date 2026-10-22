export interface PlayerSummary {
  player_id: string
  display_name: string
  level: number
  experience: number
  current_level_xp: number
  next_level_xp: number | null
  coins: number
  maple_flame: number
  guide_leaves: number
  companion_marks: number
  stamina: number
  stamina_cap: number
  stamina_restore_seconds: number
  stamina_updated_at: number
  unlocks: string[]
}

export interface CropDefinition {
  id: string
  name: string
  icon: string
  seed_item_id: string
  produce_item_id: string
  growth_seconds: number
  minimum_duration_seconds: number
  time_difficulty: number
  stamina_cost: number
  min_level: number
  accent: string
  quality: QualityCurveDefinition
}

export interface QualityCurveDefinition {
  thresholds: number[]
  width: number
  miracle_probability_cap: number
  miracle_eligible: boolean
}

export interface QualityGradeDefinition {
  level: number
  name: string
  sale_multiplier: number
}

export interface CropAdminDefinition extends CropDefinition {
  yield_min: number
  yield_max: number
  plant_xp: number
  harvest_xp: number
  seed_price: number | null
  shop_id: string | null
  produce_sell_price: number
  chart_enabled: boolean
}

export interface CropAdminPayload {
  schema_version: number
  quality_grades: QualityGradeDefinition[]
  crops: CropAdminDefinition[]
}

export interface TaskQualitySnapshot extends QualityCurveDefinition {
  ability: number
  probabilities: number[]
  miracle_width_multiplier: number
  miracle_cap_ignored: boolean
}

export interface PlotState {
  size: number
  slot: number
  crop_id: string
  planted_at: number
  ready_at: number
  assigned_partner_ids: string[]
  task_snapshot: ProductionTaskSnapshot | null
  task_results: ProductionResultSnapshot[]
  empty: boolean
  ready: boolean
  remaining_seconds: number
  crop: CropDefinition | null
  assigned_partners: OwnedPartner[]
  assignment_locked: boolean
  assignment_locked_until: number | null
}

export interface ProductionResultSnapshot {
  item_id: string
  quantity: number
  quality: number
  resolved_at: number
  quality_name?: string
  item?: {
    id: string
    name: string
    icon: string
    kind: string
    sell_price: number
    has_quality: boolean
  } | null
}

export interface TaskPartnerSnapshot {
  partner_id: string
  level: number
  effective_level: number
  breakthrough: number
  ability: number
}

export interface ProductionTaskSnapshot {
  rule_version: number
  industry: string
  content_id: string
  production_slot_id: string
  world_day: string
  weather_id: string
  started_at: number
  ready_at: number
  assigned_partner_ids: string[]
  support_partner_ids: string[]
  partner_snapshots: TaskPartnerSnapshot[]
  applied_effects: Array<Record<string, unknown>>
  character_ability: number
  total_ability: number
  time_efficiency: number
  yield_efficiency: number
  base_duration: number
  final_duration: number
  stamina_cost: number
  produce_item_id: string
  yield_min: number
  yield_max: number
  harvest_xp: number
  consumed_inputs: TaskInputSnapshot[]
  draw_count: number
  quality_parameters: TaskQualitySnapshot
}

export interface TaskInputSnapshot {
  item_id: string
  quality: number
  quantity: number
}

export interface IndustryRules {
  character_base_ability: number
  base_character_ability: number
  global_ability_bonus: number
  partner_capacity: number
  partner_level_cap: number
  collaborator_slots: number
  base_partner_capacity: number
}

export interface GatheringTaskDefinition {
  id: string
  site_id: string
  name: string
  duration_seconds: number
  minimum_duration_seconds: number
  time_difficulty: number
  outputs: GatheringOutputDefinition[]
  draws: GatheringDrawDefinition
  stamina_cost: number
  collect_xp: number
  min_level: number
  quality: QualityCurveDefinition
  item: {
    id: string
    name: string
    icon: string
    kind: string
    sell_price: number
    has_quality: boolean
  }
}

export interface GatheringDrawDefinition {
  base_draws: number
  ability_bonus: number
  difficulty: number
}

export interface GatheringOutputDefinition {
  item_id: string
  weight: number
  quantity_min: number
  quantity_max: number
  item: {
    id: string
    name: string
    icon: string
    kind: string
    sell_price: number
    has_quality: boolean
  }
}

export interface GatheringSiteState {
  site_id: string
  assigned_partner_ids: string[]
  task_snapshot: ProductionTaskSnapshot | null
  task_results: ProductionResultSnapshot[]
  empty: boolean
  ready: boolean
  remaining_seconds: number
  definition: {
    id: string
    name: string
    description: string
    accent: string
    min_level: number
  } | null
  task: GatheringTaskDefinition | null
  available_tasks: GatheringTaskDefinition[]
  assigned_partners: OwnedPartner[]
  assignment_locked: boolean
  assignment_locked_until: number | null
  /** 活动期间由服务端活动档期下发（如「夏日限定」），平时没有这个字段 */
  event_closed?: boolean
  event_badge?: string
  /** 活动期间由服务端活动档期下发的蓝字收益说明，平时没有这个字段 */
  event_note?: string
}

export interface TalentNode {
  id: string
  industry: string
  name: string
  description: string
  cost: number
  min_level: number
  prerequisites: string[]
  partner_capacity_bonus: number
  global_ability_bonus: number
  unlocked: boolean
  can_unlock: boolean
  locked_reason: string | null
}

export interface TalentState {
  earned_points: number
  available_points: number
  unlocked_node_ids: string[]
  nodes: TalentNode[]
}

export interface RewardItem {
  item_id: string
  name: string
  icon: string
  quantity: number
  quality: number
  quality_name: string | null
}

export interface RewardPartner {
  partner_id: string
  name: string
}

export interface Reward {
  coins: number
  experience: number
  talent_points: number
  maple_flame: number
  guide_leaves: number
  items: RewardItem[]
  partners: RewardPartner[]
  empty: boolean
}

export interface CrossoverCampaign {
  campaign_id: string
  title: string
  source: string
  description: string
  requirement: string
  home_url: string
  locked_hint: string
  reward: Reward
  eligible: boolean
  claimed: boolean
  claimed_at: number | null
  claimable: boolean
}

export interface CrossoverClaimResult {
  campaign_id: string
  title: string
  granted: Reward & { levels: number[] }
}

export type MailScope = 'global' | 'player'

export interface MailSummary {
  total: number
  unread: number
  unclaimed: number
}

export interface MailEntry {
  mail_id: string
  scope: MailScope
  title: string
  sender: string
  body: string
  created_at: number
  expires_at: number | null
  attachments: Reward
  read: boolean
  claimed: boolean
  claimable: boolean
}

export interface MailboxState extends MailSummary {
  available: boolean
  entries: MailEntry[]
}

export interface MailClaimResult {
  mail_id: string
  title: string
  granted: Reward & { levels: number[] }
}

export interface AdminMailEntry {
  mail_id: string
  scope: MailScope
  recipient_id: string
  recipient_name: string
  registered_before: number
  title: string
  sender: string
  body: string
  attachments: Reward
  created_at: number
  expires_at: number
}

export interface PortalTribute {
  id: string
  item_id: string
  name: string
  icon: string
  quantity: number
  min_quality: number | null
  min_quality_name: string | null
  delivered: number
  remaining: number
  deliverable: number
  owned: number
  completed: boolean
  reward: Reward
}

export interface PortalPrerequisite {
  portal_id: string
  name: string
  completed: boolean
}

export interface PortalState {
  portal_id: string
  name: string
  description: string
  accent: string
  min_level: number
  prerequisites: PortalPrerequisite[]
  unlocked: boolean
  locked_reason: string | null
  completed: boolean
  completed_at: number
  tributes: PortalTribute[]
  tribute_count: number
  completed_tribute_count: number
  completion_reward: Reward
}

export interface TributeDeliveryResult {
  portal_id: string
  portal_name: string
  tribute_id: string
  delivered: number
  total_delivered: number
  required: number
  tribute_completed: boolean
  portal_completed: boolean
  rewards: (Reward & { source: 'tribute' | 'portal'; label: string; levels: number[] })[]
  unlocked_portals: { portal_id: string; name: string }[]
}

export interface ItemDefinition {
  id: string
  name: string
  icon: string
  kind: string
  sell_price: number
  has_quality: boolean
  tags: string[]
  equipment?: EquipmentDefinition | null
  delve_use?: DelveUseDefinition | null
}

export type CommissionItem = ItemDefinition

export type CommissionStatus = 'open' | 'forwarded' | 'completed' | 'forward_completed'

export interface CommissionState {
  day: string
  commission_id: string
  npc_id: string
  npc_name: string
  npc_title: string
  line: string
  item_id: string
  quantity: number
  tier: number
  tier_name: string
  lucky: boolean
  reward_maple_flame: number
  status: CommissionStatus
  forwarded_at: number
  completed_at: number
  completed_by_name: string
  item: CommissionItem | null
  owned: number
  settled: boolean
  can_submit: boolean
  can_forward: boolean
  can_withdraw: boolean
  owner_reward: number
  taker_reward: number
}

export interface CommissionTake {
  day: string
  commission_id: string
  owner_name: string
  item_id: string
  quantity: number
  reward_maple_flame: number
  completed_at: number
}

export interface CommissionsState {
  day: string
  unlocked: boolean
  min_level: number
  board_available: boolean
  refresh_at: number
  lucky_weekday: number
  lucky_today: boolean
  reward_maple_flame: number
  lucky_reward_maple_flame: number
  daily_take_limit: number
  remaining_takes: number
  takes: CommissionTake[]
  commission: CommissionState | null
}

export interface CommissionBoardEntry {
  commission_id: string
  day: string
  owner_name: string
  npc_name: string
  npc_title: string
  line: string
  item_id: string
  quantity: number
  tier: number
  lucky: boolean
  reward_maple_flame: number
  owner_reward: number
  taker_reward: number
  forwarded_at: number
  item: CommissionItem | null
  owned: number
  can_take: boolean
}

export interface CommissionBoard {
  day: string
  available: boolean
  refresh_at: number
  remaining_takes: number
  daily_take_limit: number
  entries: CommissionBoardEntry[]
}

export interface RecipeInputState {
  item_id: string
  quantity: number
  item: GatheringTaskDefinition['item']
  owned_quantity: number
  owned_by_quality: Record<number, number>
}

export interface RecipeState {
  id: string
  station_id: string
  name: string
  inputs: RecipeInputState[]
  produce_item_id: string
  produce_quantity: number
  duration_seconds: number
  time_difficulty: number
  stamina_cost: number
  collect_xp: number
  quality: QualityCurveDefinition
  unlock_condition: { hook: string; params: Record<string, number | string | boolean> }
  item: GatheringTaskDefinition['item']
  unlocked: boolean
  unlock_description: string
  ingredients_available: boolean
}

export interface CraftingStationState {
  station_id: string
  assigned_partner_ids: string[]
  task_snapshot: ProductionTaskSnapshot | null
  task_results: ProductionResultSnapshot[]
  queue_total: number
  collected_count: number
  queued_count: number
  completed_count: number
  queue_remaining_seconds: number
  completed_results: ProductionResultSnapshot[]
  empty: boolean
  ready: boolean
  remaining_seconds: number
  definition: {
    id: string
    name: string
    description: string
    accent: string
    min_level: number
  } | null
  recipe: RecipeState | null
  recipes: RecipeState[]
  assigned_partners: OwnedPartner[]
  assignment_locked: boolean
  assignment_locked_until: number | null
}

export interface MiningTaskDefinition {
  id: string
  site_id: string
  name: string
  produce_item_id: string
  duration_seconds: number
  yield_bonus: number
  yield_difficulty: number
  yield_min: number
  yield_max: number
  stamina_cost: number
  collect_xp: number
  min_level: number
  quality: QualityCurveDefinition
  item: GatheringTaskDefinition['item']
}

export interface MiningSiteState {
  site_id: string
  assigned_partner_ids: string[]
  task_snapshot: ProductionTaskSnapshot | null
  task_results: ProductionResultSnapshot[]
  empty: boolean
  ready: boolean
  remaining_seconds: number
  definition: {
    id: string
    name: string
    description: string
    accent: string
    min_level: number
  } | null
  task: MiningTaskDefinition | null
  available_tasks: MiningTaskDefinition[]
  assigned_partners: OwnedPartner[]
  assignment_locked: boolean
  assignment_locked_until: number | null
}

export interface InventoryItem {
  item_id: string
  inventory_key: string
  name: string
  icon: string
  kind: string
  tags: string[]
  equipment?: EquipmentDefinition | null
  delve_use?: DelveUseDefinition | null
  quantity: number
  quality: number | null
  quality_name: string | null
  quality_sale_multiplier: number
  base_sell_price: number
  sell_price: number
}

export interface ShopEntry {
  id: string
  item_id: string
  price: number
  currency: 'coins'
  min_level: number
  locked: boolean
  item: {
    id: string
    name: string
    icon: string
    kind: string
    sell_price: number
  }
  crop: CropDefinition | null
}

export interface AquaticItemRef {
  id: string
  name: string
  icon: string
  kind: string
  sell_price: number
  has_quality: boolean
  tags: string[]
}

export interface FishingSpotState {
  id: string
  name: string
  description: string
  accent: string
  min_level: number
  stamina_cost: number
  cast_xp: number
  unlocked: boolean
  combo: number
  draws: { base_draws: number; ability_bonus: number; difficulty: number; expected: number }
  event_badge?: string
  /** 活动期间由服务端活动档期下发的蓝字收益说明，平时没有这个字段 */
  event_note?: string
}

export interface PendingBigCatch {
  spot_id: string
  spot_name: string
  created_at: number
  item_id: string
  name: string
  stamina_cost: number
  chance: number
  min_quality: number
}

export interface PondSpeciesState {
  id: string
  name: string
  icon: string
  fry_item_id: string
  produce_item_id: string
  base_cycle_seconds: number
  growth_rate: number
  maturation_cycles: number
  steady_ratio: number
  generation_gain: number
  generation_decay: number
  generation_cap: number
  min_level: number
  unlocked: boolean
  owned_fry: number
  fry_item: AquaticItemRef
  produce_item: AquaticItemRef
}

export interface FryBatchState {
  count: number
  cycles_left: number
}

export interface PondState {
  pond_id: string
  species_id: string
  stock: number
  fry: FryBatchState[]
  fry_total: number
  population: number
  growth_remainder: number
  generation_score: number
  settle_remainder: number
  last_settled_at: number
  ability: number
  cycle_seconds: number
  cycle_multiplier: number
  feed_multiplier: number
  quality_bonus: number
  generation_gain_bonus: number
  trait_effects: Array<Record<string, unknown>>
  pending_partner_ids: string[] | null
  pending_partner: OwnedPartner | null
  swap_open: boolean
  swap_window_seconds: number
  next_cycle_seconds: number
  maturation_seconds: number
  next_maturation_seconds: number
  stalled: boolean
  empty: boolean
  capacity: number
  feed_per_cycle: number
  next_spawn: number
  steady_stock: number
  generation_cap: number
  generation_gain: number
  generation_decay: number
  quality_ability: number
  assigned_partner_ids: string[]
  assigned_partners: OwnedPartner[]
  definition: { id: string; name: string; description: string; accent: string; min_level: number; species_id: string } | null
  species: PondSpeciesState | null
  produce_item: AquaticItemRef | null
}

export interface SlotInputEntry {
  item_id: string
  quality: number | null
  quality_name: string | null
  quantity: number
  units: number
  unit_score: number
  item: AquaticItemRef
}

/** 饲料槽和将来的肥料槽共用这一份结构，投料面板也就只有一个。 */
export interface SlotState {
  name: string
  units: number
  quality_score: number
  capacity: number
  base_capacity: number
  hourly_rate: number
  runtime_seconds: number
  quality_multipliers: number[]
  inputs: SlotInputEntry[]
}

export type FeedSlotState = SlotState

export interface FishCodexState {
  recorded: number
  total: number
  entries: Array<{ item_id: string; caught: number; first_caught_at: number; max_size: number; item: AquaticItemRef | null }>
  pool: Array<{ item_id: string | null; recorded: boolean; item: AquaticItemRef | null }>
  milestones: Array<{ id: string; name: string; required: number; claimed: boolean; reward: Reward }>
}

export interface BuildablePond {
  build_materials: FacilityMaterial[]
  id: string
  name: string
  description: string
  accent: string
  min_level: number
  tier: number
  build_cost: number
  unlocked: boolean
  affordable: boolean
  capacity: number
}

export interface AquaticState {
  unlocked: boolean
  ability: number
  companion_partner_id: string | null
  companion: OwnedPartner | null
  spots: FishingSpotState[]
  next_spot_level: number | null
  combo_rules: {
    max_layers: number
    draw_bonus_per_layer: number
    rare_weight_per_layer: number
    idle_grace_seconds: number
    decay_seconds: number
  }
  combo_cap: number
  combo: { spot_id: string | null; layers: number; updated_at: number }
  pending_big_catch: PendingBigCatch | null
  codex: FishCodexState
  ponds: PondState[]
  buildable_ponds: BuildablePond[]
  next_pond_level: number | null
  species: PondSpeciesState[]
  feed_slot: FeedSlotState
}


// ------------------------------------------------------------------- 畜牧

export interface AnimalState {
  animal_id: string
  species_id: string
  facility_id: string
  nickname: string
  name: string
  species_name: string
  icon: string
  stage: 'incubating' | 'juvenile' | 'adult'
  stage_cycles: number
  remaining_stage_cycles: number
  remaining_stage_seconds: number
  quality_gene: number
  yield_gene: number
  gene_cap: number
  affection: number
  affection_cap: number
  affection_multiplier: number
  pending_output: Record<string, number>
  pending_total: number
  pending_special: number
  overflow_cap: number
  saturated: boolean
  yield_per_cycle: number
  quality_ability: number
  breeding_cooldown: number
  breeding_cooldown_seconds: number
  cared_today: number
  care_daily_limit: number
  born_at: number
  produce_item: AquaticItemRef | null
}

export interface LivestockSpeciesState {
  id: string
  name: string
  icon: string
  category: 'poultry' | 'mammal'
  min_level: number
  purchase_price: number
  refund_base: number
  growth_cycles: number
  produce_item_id: string
  base_yield: number
  feed_per_cycle: number
  special_item_id: string
  special_chance: number
  breeding: {
    mode: 'incubate' | 'pair'
    feed_units: number
    min_feed_score: number
    gene_sigma: number
    mutation_chance: number
    mutation_bonus: number
    incubate_item_id: string
    incubate_cycles: number
    quality_gene_base: number[]
    cooldown_cycles: number
  }
  produce_item: AquaticItemRef
  special_item: AquaticItemRef | null
  unlock_description: string
  unlocked: boolean
  affordable: boolean
}

export interface LivestockUpgrade {
  level: number
  min_level: number
  capacity: number
  gene_cap: number
  overflow_cycles: number
  feed_slot_capacity_bonus: number
  build_coins: number
  unlocked: boolean
  affordable: boolean
  build_materials: Array<{ item_id: string; quantity: number; item: AquaticItemRef; owned: number }>
}

export interface LivestockFacilityState {
  upgrade: LivestockUpgrade | null
  facility_id: string
  tier: number
  name: string
  description: string
  accent: string
  category: 'poultry' | 'mammal'
  capacity: number
  used: number
  quality_multiplier: number
  overflow_cycles: number
  gene_cap: number
  ability: number
  quality_bonus: number
  feed_multiplier: number
  special_chance_bonus: number
  affection_quality_bonus: number
  trait_effects: Array<Record<string, unknown>>
  stalled: boolean
  pending_partner_ids: string[] | null
  pending_partner: OwnedPartner | null
  swap_open: boolean
  swap_window_seconds: number
  settle_remainder: number
  last_settled_at: number
  next_cycle_seconds: number
  assigned_partners: OwnedPartner[]
  animals: AnimalState[]
  pending_total: number
  pending_special: number
  species: LivestockSpeciesState[]
}

export interface BuildableFacility {
  facility_id: string
  name: string
  description: string
  accent: string
  category: 'poultry' | 'mammal'
  min_level: number
  unlocked: boolean
  capacity: number
  replaces: string | null
  feed_slot_capacity_bonus: number
  build_coins: number
  affordable: boolean
  build_materials: Array<{ item_id: string; quantity: number; item: AquaticItemRef; owned: number }>
}

export interface LivestockState {
  unlocked: boolean
  cycle_seconds: number
  ability: number
  facilities: LivestockFacilityState[]
  buildable_facilities: BuildableFacility[]
  next_facility_level: number | null
  rules: {
    cycle_seconds: number
    quality_gene_coefficient: number
    yield_gene_coefficient: number
    purchase_gene_min: number
    purchase_gene_max: number
    affection_cap: number
    affection_per_care: number
    affection_quality_base: number
    affection_quality_per_point: number
    care_stamina_cost: number
    care_experience: number
    care_daily_limit: number
    refund_gene_coefficient: number
  } | null
}

export interface LivestockDrop {
  item_id: string
  name: string
  icon: string
  quantity: number
  quality: number | null
  quality_name: string | null
  sell_price: number
}

export interface LivestockCollectResult {
  facility_id: string
  animal_id: string | null
  collected: number
  drops: LivestockDrop[]
}

export interface CareResult {
  animal_id: string
  facility_id: string
  stamina_cost: number
  experience: number
  affection_gain: number
  affection_before: number
  affection: number
  affection_cap: number
  cared_today: number
  care_daily_limit: number
  unlocked_levels: number[]
}

export interface AnimalBornResult {
  facility_id: string
  animal: AnimalState
}

export interface FishingDrop {
  item_id: string
  name: string
  icon: string
  quantity: number
  quality: number | null
  quality_name: string | null
  size: number | null
}

export interface CastResult {
  spot_id: string
  duplicate: boolean
  stamina_cost: number
  draws: number
  combo: number
  drops: FishingDrop[]
  experience: number
  codex_discoveries: Array<{ item_id: string; name: string }>
  codex_milestones: Array<{ id: string; name: string; required: number; granted: Reward }>
  applied_effects: Array<Record<string, unknown>>
  big_catch: PendingBigCatch | null
}

export interface BigCatchResult {
  codex_milestones: CastResult['codex_milestones']
  action: string
  success: boolean
  chance: number
  stamina_cost: number
  size?: number
  drops: FishingDrop[]
}

export interface PondHarvestResult {
  pond_id: string
  quantity: number
  stock: number
  quality_ability: number
  generation_before: number
  generation_score: number
  drops: FishingDrop[]
}

export type AchievementTier = 'blue' | 'purple' | 'gold'

export interface AchievementEntry {
  achievement_id: string
  name: string
  description: string
  tier: AchievementTier
  tier_name: string
  reward_maple_flame: number
  current: number
  target: number
  completed: boolean
  completed_at: number | null
  claimed: boolean
  claimed_at: number | null
  claimable: boolean
}

export interface AchievementState {
  completed: number
  total: number
  claimed: number
  claimable: number
  maple_flame_earned: number
  claimable_maple_flame: number
  entries: AchievementEntry[]
}

export interface AchievementUnlock {
  achievement_id: string
  name: string
  tier: AchievementTier
  tier_name: string
  reward_maple_flame: number
}

export interface AchievementClaimResult {
  claimed: Array<{ achievement_id: string; name: string; reward_maple_flame: number }>
  maple_flame: number
}

export type ExplorationKind = 'transport' | 'survey' | 'delve'

export interface EquipmentDefinition {
  slot: 'weapon' | 'accessory'
  attribute: 'strength' | 'agility' | 'intelligence' | null
  damage_dice: string
  attack_bonus: number
  proficiency_bonus: number
  armor_bonus: number
  initiative_bonus: number
  max_hp_bonus: number
  advantage_uses: number
  description: string
}

export interface DelveUseDefinition {
  effect: 'heal'
  dice: string
  flat: number
  quality_bonus: number
}

export interface DelveLoadout {
  weapon_item_id: string
  weapon_name: string
  accessory_item_id: string
  accessory_name: string
  attack_attribute: 'strength' | 'agility' | 'intelligence'
  damage_dice: string
  attack_bonus: number
  proficiency_bonus: number
  armor_bonus: number
  initiative_bonus: number
  max_hp_bonus: number
  advantage_uses: number
}

export interface DelveMemberState {
  max_hp: number
  hp: number
  armor_class: number
  initiative_bonus: number
}

export interface DelveEnemy {
  key: string
  enemy_id: string
  name: string
  icon: string
  max_hp: number
  hp: number
  armor_class: number
  attacks_per_turn: number
  boss: boolean
}

export interface DelveBattleLog {
  round: number
  actor: string
  actor_name: string
  action: 'attack' | 'item' | 'flee' | 'advantage'
  target: string
  target_name: string
  roll: number | null
  rolls: number[]
  modifier: number | null
  total: number | null
  hit: boolean | null
  critical: boolean
  damage: number
  healing: number
  text: string
}

export interface DelveBattleState {
  battle_id: string
  event_id: string
  choice_id: string
  depth: number
  round: number
  can_flee: boolean
  flee_dc: number
  enemies: DelveEnemy[]
  order: Array<{ key: string; is_party: boolean; name: string }>
  turn_index: number
  advantage_ready: Record<string, boolean>
  advantage_uses_left: Record<string, number>
  current_actor: string
  current_actor_is_party: boolean
  current_actor_name: string
  logs: DelveBattleLog[]
}

export interface DelveCarriedItem {
  item_id: string
  quality: number
  quantity: number
  item: ItemDefinition | null
}

export interface DelveBattleActionResult {
  action: string
  outcome: 'ongoing' | 'victory' | 'wiped' | 'fled'
  logs: DelveBattleLog[]
  drops?: ExplorationDrop[]
  completed: boolean
}

export interface ExplorationExpedition {
  id: string
  kind: ExplorationKind
  beta: boolean
  name: string
  description: string
  accent: string
  min_level: number
  entry_fee: number
  max_depth: number
  final_event_id: string
  quality: QualityCurveDefinition
  unlocked: boolean
  affordable: boolean
  /** 活动路线由服务端活动档期下发的蓝字收益说明，平时没有这个字段 */
  leader_note?: string
}

export interface ExplorationChoice {
  id: string
  label: string
  description: string
  route_stamina: number
  action_stamina: number
  battle: { enemy_ids: string[]; can_flee: boolean; flee_dc: number } | null
  check: {
    attribute: ExplorationAttribute
    mode: 'best' | 'sum'
    dice: 'normal' | 'advantage' | 'disadvantage'
    dc: number
  } | null
  check_attribute: ExplorationAttribute | null
  check_mode: 'best' | 'sum' | null
  dice_mode: 'normal' | 'advantage' | 'disadvantage' | null
  modifier: number | null
  actor_partner_ids: string[]
  check_actor_names: string[]
  actor_partner_id: string | null
  actor_options: ExplorationActorOption[]
  success_chance: number
  stamina_cost_min: number
  stamina_cost_max: number
  applied_effects: Array<Record<string, unknown>>
}

export interface ExplorationActorOption {
  partner_id: string
  name: string
  modifier: number | null
  dice_mode: 'normal' | 'advantage' | 'disadvantage' | null
  success_chance: number
  applied_effects: Array<Record<string, unknown>>
}

export interface ExplorationDrop extends ProductionResultSnapshot {
  quality_name: string
  item: ItemDefinition | null
}

export interface ExplorationLog {
  depth: number
  event_id: string
  event_name: string
  choice_id: string
  success: boolean
  degree: ExplorationCheckDegree
  check_attribute: ExplorationAttribute | null
  check_mode: 'best' | 'sum' | null
  dice_mode: 'normal' | 'advantage' | 'disadvantage' | null
  rolls: number[]
  kept_roll: number | null
  modifier: number | null
  total: number | null
  actor_partner_ids: string[]
  text: string
  stamina_cost: number
  rewards: ExplorationDrop[]
  applied_effects: Array<Record<string, unknown>>
}

export type ExplorationCheckDegree = 'automatic_success' | 'critical_failure' | 'failure' | 'success' | 'critical_success'

export interface ExplorationResolutionResult {
  battle_started?: boolean
  outcome?: 'ongoing' | 'victory' | 'wiped'
  event_id: string
  choice_id: string
  success: boolean
  degree: ExplorationCheckDegree
  success_chance: number
  check_attribute: ExplorationAttribute | null
  check_mode: 'best' | 'sum' | null
  dice_mode: 'normal' | 'advantage' | 'disadvantage' | null
  rolls: number[]
  kept_roll: number | null
  modifier: number | null
  total: number | null
  actor_partner_ids: string[]
  text: string
  stamina_cost: number
  drops: ExplorationDrop[]
  completed: boolean
}

export interface ExplorationWithdrawalResult {
  expedition_id: string
  completed: boolean
  depth: number
  stamina_spent: number
  entry_fee: number
  drops: ExplorationDrop[]
  equipment_drops: DelveCarriedItem[]
  returned_items: DelveCarriedItem[]
  guide_leaves?: number
}

export interface ExplorationRun {
  run_id: string
  expedition_id: string
  expedition_kind: ExplorationKind
  status: 'active' | 'completed'
  partner_ids: string[]
  leader_partner_id: string
  exploration_ability: number
  entry_fee: number
  started_at: number
  depth: number
  current_event_id: string
  route_stamina_raw: number
  action_stamina_spent: number
  stamina_spent: number
  next_route_discount: number
  expedition: ExplorationExpedition
  party: Array<OwnedPartner & { loadout: DelveLoadout | null; combat: DelveMemberState | null }>
  carried_items: DelveCarriedItem[]
  pending_fixed_rewards: DelveCarriedItem[]
  battle: DelveBattleState | null
  leader: OwnedPartner | null
  stamina_discount_rate: number
  pending_rewards: ExplorationDrop[]
  current_event: {
    id: string
    name: string
    description: string
    min_depth: number
    max_depth: number
    choices: ExplorationChoice[]
  } | null
  logs: ExplorationLog[]
}

export interface ExplorationState {
  unlocked: boolean
  expeditions: ExplorationExpedition[]
  active_run: ExplorationRun | null
}

export interface GameState {
  facilities: FacilitiesState
  sailing: SailingState | null
  server_time: number
  world: {
    day: string
    season: { id: string; name: string }
    weather: { id: string; name: string; accent: string }
  }
  player: PlayerSummary
  plots: PlotState[]
  next_plot_level: number | null
  gathering_sites: GatheringSiteState[]
  next_gathering_site_level: number | null
  crafting_stations: CraftingStationState[]
  next_crafting_station_level: number | null
  mining_sites: MiningSiteState[]
  next_mining_site_level: number | null
  aquatic: AquaticState
  livestock: LivestockState
  exploration: ExplorationState
  inventory: InventoryItem[]
  task_items: TaskItemState[]
  partners: OwnedPartner[]
  partner_count: number
  partner_growth: PartnerGrowthState
  gacha_pools: GachaPoolState[]
  industry_rules: Record<string, IndustryRules>
  talents: TalentState
  portals: PortalState[]
  commissions: CommissionsState
  monthly_card: MonthlyCardState
  stamina_supply: StaminaSupplyState
  mail: MailSummary
  summer_event: SummerEventState
  achievements: AchievementState
  crops: CropDefinition[]
  shop: ShopEntry[]
}

export interface MonthlyCardState {
  active: boolean
  days_left: number
  max_days: number
  duration_days: number
  expires_on: string
  expires_at: number
  claimable: boolean
  claimed_today: boolean
  next_refresh_at: number
  redeemed_total: number
  activation_maple_flame: number
  daily_maple_flame: number
  daily_item_amount: number
  daily_item: ItemDefinition | null
}

export interface StaminaSupplyState {
  potion_item_id: string
  potion_restore: number
  potion_owned: number
  potion_item: ItemDefinition | null
  purchase_restore: number
  purchase_prices: number[]
  purchase_used_today: number
  purchase_daily_limit: number
  purchase_next_price: number | null
  purchase_resets_at: number
}

export interface AccountState {
  player_id: string
  display_name: string
  bindings: string[]
  state: GameState
}

export interface ActionResult {
  result: Record<string, unknown>
  state: GameState
}

export type IndustryId = 'farming' | 'gathering' | 'mining' | 'aquatic' | 'livestock' | 'crafting' | 'exploration'
export type GrowthCurveId = 'early' | 'linear' | 'late'

export interface PartnerTendency {
  industry: IndustryId
  level_1: number
  level_60: number
}

export interface PartnerArtwork {
  breakthrough: number
  asset_key: string
  width: number
  height: number
  content_type: string
  url?: string | null
  name?: string
  skin_id?: string
}

export interface CropRect {
  x: number
  y: number
  w: number
  h: number
}

export interface AvatarCrop extends CropRect {
  breakthrough: number
}

export interface PartnerDefinition {
  id: string
  name: string
  rarity: 3 | 4 | 5
  standard_recruitable: boolean
  description: string
  growth_curve: GrowthCurveId
  exploration_stats: ExplorationStats
  tendencies: PartnerTendency[]
  trait_codes: string[]
  artworks: PartnerArtwork[]
  avatar_crops: AvatarCrop[]
  ascensions?: PartnerAscension[]
  complete?: boolean
  ability_preview?: Record<string, Record<string, number>>
}

export type ExplorationAttribute = 'strength' | 'agility' | 'intelligence' | 'luck'

export interface ExplorationStats {
  strength: number
  agility: number
  intelligence: number
  luck: number
}

export interface PartnerSelectCandidate {
  id: string
  name: string
  rarity: 3 | 4 | 5
  description: string
  growth_curve_name: string
  artwork: PartnerArtwork | null
  avatar_crop: AvatarCrop | null
  tendencies: Array<{ industry: string; name: string; ability: number }>
  traits: Array<{ code: string; name: string; description: string }>
  companion_marks_granted: number
}

export interface PartnerSelectPayload {
  candidates: PartnerSelectCandidate[]
  eligible_total: number
}

export interface PartnerSelectUseResult {
  partner_id: string
  name: string
  stars: number
  companion_marks_granted: number
}

export interface PartnerAdminOptions {
  industries: Array<{ id: IndustryId; name: string }>
  growth_curves: Array<{ id: GrowthCurveId; name: string }>
  rarities: Array<3 | 4 | 5>
  breakthrough_level_caps: number[]
  cdn: { provider: string; configured: boolean; base_url: string }
  traits: Array<{ code: string; name: string; description: string; implemented: boolean }>
}

export interface PartnerAdminPayload {
  partners: PartnerDefinition[]
  options: PartnerAdminOptions
}

export interface OwnedPartnerTendency extends PartnerTendency {
  name: string
  current_ability: number
  effective_level: number
  effective_ability: number
}

export interface OwnedPartnerTrait {
  code: string
  name: string
  description: string
  implemented: boolean
  phases: string[]
}

export interface OwnedPartner {
  artwork_stage?: number | null
  available_artworks?: PartnerArtwork[]
  partner_id: string
  level: number
  experience: number
  breakthrough: number
  stars: number
  acquired_at: number
  missing: boolean
  name: string
  rarity: 3 | 4 | 5 | null
  description?: string
  growth_curve?: GrowthCurveId
  growth_curve_name?: string
  level_cap?: number
  experience_to_next_level?: number | null
  artwork?: PartnerArtwork | null
  avatar_crop?: AvatarCrop | null
  tendencies?: OwnedPartnerTendency[]
  traits?: OwnedPartnerTrait[]
  exploration_high_stats?: ExplorationAttribute[]
  upgrade_available?: boolean
  breakthrough_available?: boolean
  breakthrough_reason?: string | null
  ascension?: PartnerAscensionState | null
  star_up_available?: boolean
  star_up_cost?: number | null
  assigned_plot_slot: number | null
  assigned_gathering_site_id: string | null
  assigned_refining?: boolean
  assigned_crafting_station_id: string | null
  assigned_mining_site_id: string | null
  locked: boolean
  locked_until: number | null
}

export interface PartnerAscensionItem {
  item_id: string
  quantity: number
  min_quality: number
}

export interface PartnerAscension {
  breakthrough: 1 | 2
  min_player_level?: number
  coins: number
  items: PartnerAscensionItem[]
}

export interface PartnerAscensionState extends PartnerAscension {
  items: Array<PartnerAscensionItem & { name: string; icon: string; owned: number; kind: string }>
  required_partner_level: number
  level_cap: number
  artwork: PartnerArtwork | null
  tendencies: Array<{ industry: string; name: string; current_ability: number; max_ability: number }>
}

export interface TaskItemState {
  id: string
  name: string
  description: string
  icon: string
  effect: string
  value: number
  timing: 'start' | 'active'
  eligible_industries: string[]
  quantity: number
}

export interface PartnerExperienceBook {
  item_id: string
  experience: number
  owned: number
  item: { id: string; name: string; icon: string; kind: string; sell_price: number; has_quality: boolean }
}

export interface PartnerGrowthState {
  level_experience_costs: Record<number, number>
  experience_books: PartnerExperienceBook[]
}

export interface GachaCatalogPartner {
  partner_id: string
  name: string
  rarity: 3 | 4 | 5
  artwork: PartnerArtwork | null
  avatar_crop: AvatarCrop | null
}

export interface GachaPoolBackground {
  asset_key: string
  width: number
  height: number
  content_type: string
  url?: string | null
}

export interface GachaPoolState {
  pool_id: string
  title: string
  unlocked: boolean
  min_level: number
  maple_flame_per_leaf: number
  rarity_probabilities: Record<number, number>
  item_probability: number
  four_star_guarantee: number
  five_star_pity: number
  pulls_until_four_star: number
  pulls_until_five_star: number
  max_pulls_per_player: number | null
  total_pulls: number
  remaining_pulls: number | null
  background: GachaPoolBackground | null
  featured_partner_id: string | null
  featured_rate: number
  featured_guaranteed: boolean
  featured_four_star_partner_id: string | null
  featured_four_star_rate: number
  catalog: GachaCatalogPartner[]
  task_items: Omit<TaskItemState, 'quantity'>[]
}

export interface GachaDrop {
  kind: 'partner' | 'task_item'
  content_id: string
  rarity: number | null
  duplicate: boolean
  quantity: number
  companion_marks: number
}

export interface GachaResult {
  request_id: string
  pool_id: string
  count: 1 | 10
  created_at: number
  results: GachaDrop[]
  replayed: boolean
}

export interface AdminPlayerSummary {
  player_id: string
  display_name: string
  level: number
  experience: number
  coins: number
  maple_flame: number
  owned_partner_ids: string[]
  updated_at: number
}

export interface AdminPlayerResourceGrantResult {
  coins: number
  experience: number
  maple_flame: number
  unlocked_levels: number[]
  player: AdminPlayerSummary
}

export interface AdminPartnerGrantResult {
  owned_partner: {
    partner_id: string
    level: number
    breakthrough: number
    acquired_at: number
  }
  player: AdminPlayerSummary
}

export type StoryAssetKind = 'background' | 'portrait'
export type StoryMode = 'inline' | 'stage'
export type StoryPortraitSlot = 'left' | 'right' | 'center'

export interface StoryAssetLayout {
  scale: number
  offset_x: number
  offset_y: number
}

export interface StoryAsset {
  id: string
  kind?: StoryAssetKind
  name: string
  asset_key: string
  width: number
  height: number
  layouts?: Record<StoryMode, StoryAssetLayout>
  inline_layout?: StoryAssetLayout
  stage_layout?: StoryAssetLayout
  content_type?: string
  created_at?: number
  url?: string
  /** 公共素材库的图，还是自己传上来的图 */
  source?: 'official' | 'upload'
}

export interface StoryUpload {
  id: string
  kind: StoryAssetKind
  name: string
  asset_key: string
  width: number
  height: number
  uploader_name: string
  created_at: number
  mine: boolean
  url?: string
  layout: StoryAssetLayout
}

export interface StoryUploadQuota {
  quota_bytes: number
  used_bytes: number
  window_seconds: number
  max_file_bytes: number
}

/** 新作者第一次打开编辑器时的起手草稿，来自游戏里现成的那段剧情。 */
export interface StoryExample {
  id: string
  title: string
  priority: number
  steps: unknown[]
}

export type StoryBackgroundTransition = 'fade' | 'cut'
export type StoryPortraitTransition = 'fade' | 'slide' | 'cut'

/** 某一步单独覆盖的立绘站位，比素材默认站位放得开。留空表示沿用素材调好的值。 */
export interface StoryStepLayout {
  scale: number
  offset_x: number
  offset_y: number
}

export interface StoryBackgroundStep {
  type: 'background'
  asset_id: string
  asset: StoryAsset | null
  transition?: StoryBackgroundTransition
  duration?: number
}

export interface StoryPortraitStep {
  type: 'portrait'
  slot: StoryPortraitSlot
  visible: boolean
  asset_id: string
  partner_id: string
  breakthrough: number
  asset: StoryAsset | null
  transition?: StoryPortraitTransition
  duration?: number
  flip?: boolean
  layout?: StoryStepLayout | null
}

export interface StoryDialogueStep {
  type: 'dialogue'
  speaker: string
  text: string
  focus: StoryPortraitSlot | 'none'
}

export type StoryStep = StoryBackgroundStep | StoryPortraitStep | StoryDialogueStep

export interface StoryScript {
  id: string
  title: string
  mode: StoryMode
  priority: number
  repeatable: boolean
  trigger_description: string
  rewards: Reward
  steps: StoryStep[]
}

export interface StoryCueResult {
  cue: string
  stories: StoryScript[]
}

export interface StoryResources {
  assets: StoryAsset[]
  partners: { id: string; name: string; artworks: { breakthrough: number; asset_key: string; width: number; height: number; url?: string }[] }[]
  uploads: StoryUpload[]
  upload: StoryUploadQuota
  example: StoryExample | null
}

export interface StoryAdminPayload {
  assets: StoryAsset[]
  scripts: StoryScript[]
  options: {
    kinds: { id: StoryAssetKind; name: string }[]
    trigger_hooks: string[]
    script_directory: string
    cdn: { provider: string; configured: boolean; base_url: string }
  }
}


export interface SailingRun {
  coins: number
  stamina: number
  run_id: string
  route_id: string
  route_name: string
  partner_ids: string[]
  started_at: number
  ready_at: number
  ready: boolean
  trial: boolean
  ability: number
  experience: number
  drops: Array<{ item_id: string; name: string; quantity: number; quality: number; quality_name: string }>
  logs: Array<{ event_id: string; name: string; text: string; success: boolean; roll: number; modifier: number; attribute: string; actor_id: string }>
}

export interface SailingState {
  ship_built: boolean
  construction: { coins: number; materials: Array<{ item_id: string; item_name: string; quantity: number; owned: number }> }
  unlocked: boolean
  min_level: number
  trial_available: boolean
  completed_voyages: number
  routes: Array<{ id: string; name: string; description: string; duration: number; coins: number; stamina: number; required_voyages: number; unlocked: boolean; draws: { base_draws: number; ability_bonus: number; difficulty: number }; equipment_expected_stamina: number; outputs: Array<{ item_id: string; name: string; rarity: 'common' | 'rare' | 'seed' | 'equipment'; weight: number; quantity_min: number; quantity_max: number }> }>
  supplies: Array<{ id: string; name: string; description: string; item_id: string; item_name: string; quantity: number; owned: number }>
  upgrades: Array<{ kind: string; name: string; level: number; coins: number; quantity: number; item_id: string; item_name: string; owned: number; description: string }>
  active_run: SailingRun | null
  last_run: SailingRun | null
  discoveries: Array<{ id: string; name: string; discovered: boolean }>
  collection: Array<{ item_id: string; name: string; quantity: number }>
}


export interface SummerChapter {
  story_id: string
  title: string
  points: number
  cue: string
  unlocked: boolean
  seen: boolean
  previous_seen: boolean
}
export interface SummerSkinOffer {
  id: string
  partner_id: string
  name: string
  display_name: string
  subtitle: string
  asset_key: string
  url?: string
  width: number
  height: number
  unlocked: boolean
  can_claim: boolean
  cost: Array<{ item_id: string; item_name: string; quantity: number; min_quality: number; owned: number }>
}
export interface SummerEventState {
  visible: boolean
  active: boolean
  can_claim: boolean
  name: string
  starts_at: number
  ends_at: number
  claim_ends_at: number
  points: number
  target: number
  milestones: Array<{ points: number; reward: Reward; claimed: boolean; claimable: boolean }>
  chapters: SummerChapter[]
  skins?: SummerSkinOffer[]
}

export interface FacilityMaterial { item_id: string; quantity: number; owned: number; item: AquaticItemRef }
export interface FacilityUpgrade { id: string; name: string; kind: 'farm' | 'feed' | 'refining'; stage: number; min_level: number; coins: number; inputs: FacilityMaterial[]; unlocked: boolean; affordable: boolean }
export interface RefiningRecipe { id: string; name: string; min_level: number; unlocked: boolean; input_quantity: number; input_item: AquaticItemRef; item: AquaticItemRef; output_prices: number[]; options: { quality: number; owned: number; input_value: number; quality_times: number[] }[] }
export interface RefiningTask { task_id: string; input_quality: number; started_at: number; quality_times: number[]; item: AquaticItemRef; max_quality: number }
export interface FacilitiesState { farm_stage: number; feed_stage: number; upgrades: FacilityUpgrade[]; refining: { built: boolean; unlocked: boolean; locked: boolean; assigned_partner: OwnedPartner | null; ability: number; quality_ability: number; max_quality: number; slots: (RefiningTask | null)[]; recipes: RefiningRecipe[] } }
