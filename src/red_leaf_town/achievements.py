from __future__ import annotations

from red_leaf_town.content import AchievementDefinition, GameContent
from red_leaf_town.domain.models import AchievementCompletionState, PlayerState, ProductionResultSnapshot


TIER_NAMES = {"blue": "蓝色", "purple": "紫色", "gold": "金色"}


def record_production_collection(
    player: PlayerState,
    industry: str,
    content_id: str,
    results: list[ProductionResultSnapshot],
) -> None:
    stats = player.achievement_stats
    stats.production_collections[industry] = stats.production_collections.get(industry, 0) + 1
    if industry == "mining" and content_id not in stats.mined_task_ids:
        stats.mined_task_ids.append(content_id)
    if industry == "gathering":
        stats.gathering_items[content_id] = sorted(
            set(stats.gathering_items.get(content_id, [])) | {r.item_id for r in results if r.quantity > 0}
        )
    if industry == "farming" and content_id not in stats.harvested_crop_ids:
        stats.harvested_crop_ids.append(content_id)
    if industry == "crafting" and content_id not in stats.crafted_recipe_ids:
        stats.crafted_recipe_ids.append(content_id)
    stats.max_production_quality = max(
        stats.max_production_quality,
        max((result.quality for result in results), default=0),
    )


def evaluate_achievements(player: PlayerState, content: GameContent, now: int) -> list[dict]:
    completed = {entry.achievement_id for entry in player.achievements}
    newly_completed: list[dict] = []
    for definition in content.achievements:
        if definition.id in completed:
            continue
        current, target = achievement_progress(player, definition, content)
        if current < target:
            continue
        player.achievements.append(
            AchievementCompletionState(achievement_id=definition.id, completed_at=now),
        )
        completed.add(definition.id)
        newly_completed.append({
            "achievement_id": definition.id,
            "name": definition.name,
            "tier": definition.tier,
            "tier_name": TIER_NAMES[definition.tier],
            "reward_maple_flame": definition.reward_maple_flame,
        })
    return newly_completed


def reconcile_legacy_auto_rewards(player: PlayerState, content: GameContent) -> None:
    if player.achievement_auto_rewards_reconciled:
        return
    definitions = content.achievement_map
    for progress in player.achievements:
        definition = definitions.get(progress.achievement_id)
        if definition is None or progress.claimed_at:
            continue
        reward = definition.reward_maple_flame
        if player.maple_flame >= reward:
            player.maple_flame -= reward
        else:
            # 奖励已经被消费时不能把货币扣成负数；视作已经领取，避免再次发放。
            progress.claimed_at = progress.completed_at
    player.achievement_auto_rewards_reconciled = True


def achievement_snapshot(player: PlayerState, content: GameContent) -> dict:
    completed = {entry.achievement_id: entry for entry in player.achievements}
    entries = []
    for definition in content.achievements:
        current, target = achievement_progress(player, definition, content)
        progress = completed.get(definition.id)
        completed_at = progress.completed_at if progress else 0
        claimed_at = progress.claimed_at if progress else 0
        entries.append({
            "achievement_id": definition.id,
            "name": definition.name,
            "description": definition.description,
            "tier": definition.tier,
            "tier_name": TIER_NAMES[definition.tier],
            "reward_maple_flame": definition.reward_maple_flame,
            "current": min(current, target),
            "target": target,
            "completed": bool(completed_at),
            "completed_at": completed_at or None,
            "claimed": bool(claimed_at),
            "claimed_at": claimed_at or None,
            "claimable": bool(completed_at and not claimed_at),
        })
    completed_entries = [entry for entry in entries if entry["completed"]]
    claimed_entries = [entry for entry in entries if entry["claimed"]]
    claimable_entries = [entry for entry in entries if entry["claimable"]]
    return {
        "completed": len(completed_entries),
        "total": len(entries),
        "claimed": len(claimed_entries),
        "claimable": len(claimable_entries),
        "maple_flame_earned": sum(entry["reward_maple_flame"] for entry in claimed_entries),
        "claimable_maple_flame": sum(entry["reward_maple_flame"] for entry in claimable_entries),
        "entries": entries,
    }


def achievement_progress(
    player: PlayerState,
    definition: AchievementDefinition,
    content: GameContent,
) -> tuple[int, int]:
    hook = definition.condition.hook
    params = definition.condition.params
    stats = player.achievement_stats

    if hook == "facility_stage":
        stages = {"farm": player.farm_upgrade_stage, "feed": player.feed_upgrade_stage,
                  "refining": int(player.refining.built)}
        return stages[str(params["kind"])], int(params["stage"])
    if hook == "pond_built":
        return int(any(p.pond_id == params["pond_id"] for p in player.ponds)), 1
    if hook == "mining_tasks":
        required = set(params["task_ids"])
        return len(required & set(stats.mined_task_ids)), len(required)
    if hook == "refining_batches":
        minimum = int(params.get("min_quality", 1))
        return sum(count for quality, count in stats.refining_batches.items() if quality >= minimum), int(params["count"])
    if hook == "gathering_tasks":
        required = set(params["task_ids"])
        return len(required & set(stats.gathering_items)), len(required)
    if hook == "gathering_items":
        required = set(params["item_ids"])
        return len(required & set(stats.gathering_items.get(str(params["task_id"]), []))), len(required)
    if hook in {"livestock_items", "animals_bred_species"}:
        required = set(params["item_ids" if hook == "livestock_items" else "species_ids"])
        recorded = stats.livestock_item_ids if hook == "livestock_items" else stats.bred_species_ids
        return len(required & set(recorded)), len(required)
    if hook == "facilities_tier":
        required = set(params["facility_ids"])
        built = {f.facility_id for f in player.livestock_facilities if f.tier >= int(params["tier"])}
        return len(required & built), len(required)
    if hook == "sailing_ship":
        return int(player.sailing.ship_built), 1
    if hook == "sailing_voyages":
        return player.sailing.completed_voyages, int(params["count"])
    if hook == "sailing_routes":
        required = set(params["route_ids"])
        recorded = set(stats.sailing_route_ids)
        if player.sailing.last_run:
            recorded.add(player.sailing.last_run.route_id)
        return len(required & recorded), len(required)
    if hook == "sailing_upgrades":
        return min(player.sailing.cargo_level, player.sailing.nets_level), int(params["level"])
    if hook == "sailing_items":
        recorded = {item_id for item_id, count in player.sailing.collected_items.items() if count > 0}
        return len(set(params["item_ids"]) & recorded), int(params["count"])
    if hook in {"exploration_completed", "delve_completed"}:
        recorded = stats.completed_expedition_ids if hook == "exploration_completed" else stats.completed_delve_ids
        return int(str(params["expedition_id"]) in recorded), 1
    if hook == "delve_wins":
        return stats.delve_wins.get(str(params["expedition_id"]), 0), int(params["count"])
    if hook == "story_seen":
        return int(str(params["story_id"]) in player.seen_story_ids), 1
    if hook == "production_collections":
        target = int(params.get("count", 1))
        return stats.production_collections.get(str(params["industry"]), 0), target
    if hook == "partners_owned":
        return len(player.owned_partners), int(params["count"])
    if hook == "player_level":
        return player.level, int(params["level"])
    if hook == "crops_harvested":
        required = {str(entry) for entry in params["crop_ids"]}
        return len(required & set(stats.harvested_crop_ids)), len(required)
    if hook == "recipes_crafted":
        required = {str(entry) for entry in params["recipe_ids"]}
        return len(required & set(stats.crafted_recipe_ids)), len(required)
    if hook == "commissions_completed":
        current = stats.own_commissions_completed if params.get("own_only") else stats.commissions_completed
        return current, int(params["count"])
    if hook == "talents_unlocked":
        return len(player.talent_nodes), int(params["count"])
    if hook == "pond_harvested":
        return stats.pond_harvested.get(str(params["species_id"]), 0), int(params["count"])
    if hook == "fish_codex_entries":
        return len(player.fish_codex.entries), int(params["count"])
    if hook == "big_catch_caught":
        big_catch_ids = {
            spot.big_catch.item_id
            for spot in content.fishing_spots
            if spot.big_catch is not None
        }
        return int(any(entry.item_id in big_catch_ids for entry in player.fish_codex.entries)), 1
    if hook == "max_production_quality":
        return stats.max_production_quality, int(params["quality"])
    if hook == "animals_bred":
        return stats.animals_bred, int(params["count"])
    if hook == "animals_cared":
        return stats.animals_cared, int(params["count"])
    if hook == "livestock_specials":
        return stats.livestock_specials, int(params["count"])
    if hook == "animals_at_max_affection":
        cap = content.livestock.affection_cap if content.livestock else 100
        return sum(1 for animal in player.animals if animal.affection >= cap), int(params["count"])
    if hook == "livestock_gene":
        gene = int(params["gene"])
        current = sum(
            1 for animal in player.animals
            if animal.quality_gene >= gene and animal.yield_gene >= gene
        )
        return current, int(params.get("count", 1))
    if hook == "portal_completed":
        portal_id = str(params["portal_id"])
        return int(any(entry.portal_id == portal_id and entry.completed_at for entry in player.portals)), 1
    raise ValueError(f"unsupported achievement hook: {hook}")
