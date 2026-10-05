from __future__ import annotations

import random

import pytest

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import load_content
from pydantic import ValidationError

from red_leaf_town.domain import PlayerState, QQIdentity
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.partner_content import PartnerCatalog, PartnerDefinition


class Clock:
    def __init__(self, now: int = 1_700_000_000):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds: int):
        self.now += seconds


@pytest.fixture
def game():
    content = load_content()
    repository = InMemoryPlayerRepository(content)
    clock = Clock()
    service = GameService(content, repository, clock=clock, rng=random.Random(7))
    player = service.ensure_player("oauth-sub-1", "枫糖")
    return service, repository, clock, player


def test_oauth_account_has_exactly_one_player(game):
    service, repository, _, first = game
    second = service.ensure_player("oauth-sub-1", "新昵称")
    assert second.player_id == first.player_id
    assert repository.get_by_sub("oauth-sub-1").display_name == "新昵称"
    assert len(repository.players) == 1


def test_world_weather_is_authoritative_and_rolls_over_by_server_day(game):
    service, _, clock, _ = game
    first = service.snapshot_by_sub("oauth-sub-1")["world"]
    clock.advance(24 * 3600)
    second = service.snapshot_by_sub("oauth-sub-1")["world"]

    assert first["season"] == {"id": "autumn", "name": "秋季"}
    assert first["day"] != second["day"]
    assert first["weather"]["id"] != second["weather"]["id"]
    assert second["weather"]["name"] in {"晴朗", "多云", "小雨", "山风"}


def test_buy_plant_wait_harvest_and_sell_is_a_closed_loop(game):
    service, repository, clock, _ = game
    bought = service.buy("oauth-sub-1", "carrot_seed", 1)
    assert bought["state"]["player"]["coins"] == 70
    assert bought["state"]["inventory"][0]["item_id"] == "carrot_seed"

    planted = service.plant("oauth-sub-1", 0, "carrot")
    assert planted["state"]["player"]["stamina"] == 20
    assert planted["state"]["plots"][0]["remaining_seconds"] == 10_800

    with pytest.raises(GameError, match="还没有成熟"):
        service.harvest("oauth-sub-1", 0)

    clock.advance(10_800)
    harvested = service.harvest("oauth-sub-1", 0)
    reward = harvested["result"]
    assert reward["item_id"] == "carrot"
    assert 2 <= reward["quantity"] <= 4
    assert sum(drop["quantity"] for drop in reward["drops"]) == reward["quantity"]
    assert harvested["state"]["plots"][0]["empty"] is True
    assert [entry["achievement_id"] for entry in reward["achievements"]] == ["first_harvest"]
    assert harvested["state"]["player"]["maple_flame"] == 0
    assert harvested["state"]["achievements"]["claimable"] == 1

    claimed = service.claim_achievement("oauth-sub-1", "first_harvest")
    assert claimed["result"]["maple_flame"] == 50
    assert claimed["state"]["player"]["maple_flame"] == 50

    for drop in reward["drops"]:
        sold = service.sell("oauth-sub-1", "carrot", drop["quantity"], drop["quality"])
    assert sold["state"]["player"]["coins"] > 74
    assert repository.get_by_sub("oauth-sub-1").inventory.get("carrot", 0) == 0


def test_locked_plot_and_crop_are_enforced(game):
    service, _, _, _ = game
    service.buy("oauth-sub-1", "carrot_seed", 1)
    with pytest.raises(GameError, match="土地尚未解锁"):
        service.plant("oauth-sub-1", 3, "carrot")
    with pytest.raises(GameError, match="达到 5 级"):
        service.buy("oauth-sub-1", "wheat_seed", 1)


def test_story_only_tutorial_crop_is_fast_and_free(game):
    service, repository, clock, player = game
    repository.update(player.player_id, lambda state: setattr(state, "inventory", {"orange_berry_seed": {0: 1}}))

    planted = service.plant("oauth-sub-1", 0, "orange_berry")
    assert planted["result"]["final_duration"] == 30
    assert planted["state"]["player"]["stamina"] == 20

    clock.advance(30)
    harvested = service.harvest("oauth-sub-1", 0)
    assert harvested["result"]["item_id"] == "orange_berry"
    assert harvested["result"]["quantity"] == 1
    assert repository.get(player.player_id).inventory.get("orange_berry_seed") is None


def test_stamina_recovers_lazily(game):
    service, repository, clock, player = game

    def drain(state):
        state.stamina = 10
        state.stamina_updated_at = clock.now

    repository.update(player.player_id, drain)
    clock.advance(service.content.stamina.restore_seconds * 3 + 20)
    state = service.snapshot_by_sub("oauth-sub-1")
    assert state["player"]["stamina"] == 13


def test_binding_uses_adapter_bot_and_openid_not_qq_number(game):
    service, _, _, player = game
    code = service.create_binding_code("oauth-sub-1")
    official_identity = QQIdentity(platform="QQ", bot_id="102042640", subject="opaque-openid")
    bound = service.bind_identity(code, official_identity)
    assert bound.player_id == player.player_id
    assert service.snapshot_by_identity(official_identity)["player"]["player_id"] == player.player_id

    another_bot = QQIdentity(platform="QQ", bot_id="another-bot", subject="opaque-openid")
    with pytest.raises(GameError, match="尚未绑定"):
        service.snapshot_by_identity(another_bot)


def test_old_player_save_migrates_to_empty_partner_warehouse():
    player = PlayerState.model_validate({
        "schema_version": 1,
        "player_id": "legacy-player",
        "oauth_sub": "legacy-sub",
        "display_name": "旧居民",
        "stamina_updated_at": 1,
        "created_at": 1,
        "updated_at": 1,
    })
    assert player.schema_version == PlayerState.model_fields["schema_version"].default
    assert player.owned_partners == []


def test_schema_two_spirit_fields_migrate_to_partner_fields():
    player = PlayerState.model_validate({
        "schema_version": 2,
        "player_id": "legacy-spirit-player",
        "oauth_sub": "legacy-spirit-sub",
        "display_name": "旧伙伴居民",
        "stamina_updated_at": 1,
        "created_at": 1,
        "updated_at": 1,
        "owned_spirits": [{"spirit_id": "maple_sprite", "acquired_at": 2}],
    })
    assert player.schema_version == PlayerState.model_fields["schema_version"].default
    assert player.owned_partners[0].partner_id == "maple_sprite"
    assert "owned_spirits" not in player.model_dump()


def test_schema_three_plots_migrate_to_partner_assignment_structure():
    player = PlayerState.model_validate({
        "schema_version": 3,
        "player_id": "legacy-plot-player",
        "oauth_sub": "legacy-plot-sub",
        "display_name": "旧农场居民",
        "stamina_updated_at": 1,
        "created_at": 1,
        "updated_at": 1,
        "plots": [{"slot": 0}],
    })
    assert player.schema_version == PlayerState.model_fields["schema_version"].default
    assert player.plots[0].assigned_partner_ids == []
    assert player.plots[0].task_snapshot is None


def test_schema_sixteen_rebalances_partner_progress_and_active_task_snapshots_once():
    started_at = 1_700_000_000
    player = PlayerState.model_validate({
        "schema_version": 16,
        "player_id": "legacy-partner-experience",
        "oauth_sub": "legacy-partner-experience-sub",
        "display_name": "旧伙伴经验居民",
        "stamina_updated_at": started_at,
        "created_at": started_at,
        "updated_at": started_at,
        "owned_partners": [{
            "partner_id": "maple_sprite",
            "level": 12,
            "experience": 45,
            "breakthrough": 0,
            "stars": 4,
            "acquired_at": started_at,
        }],
        "mining_sites": [{
            "site_id": "copper_foothill",
            "assigned_partner_ids": ["maple_sprite"],
            "task_snapshot": {
                "rule_version": 1,
                "industry": "mining",
                "content_id": "mine_red_copper",
                "production_slot_id": "mining:site:copper_foothill",
                "started_at": started_at,
                "ready_at": started_at + 15 * 60,
                "assigned_partner_ids": ["maple_sprite"],
                "partner_snapshots": [{
                    "partner_id": "maple_sprite",
                    "level": 12,
                    "effective_level": 12,
                    "breakthrough": 0,
                    "ability": 40,
                }],
                "character_ability": 0,
                "total_ability": 40,
                "time_efficiency": 1,
                "base_duration": 15 * 60,
                "final_duration": 15 * 60,
                "produce_item_id": "red_copper_ore",
                "yield_min": 3,
                "yield_max": 4,
                "harvest_xp": 18,
            },
        }],
    })

    partner = player.owned_partners[0]
    task = player.mining_sites[0].task_snapshot
    assert player.schema_version == PlayerState.model_fields["schema_version"].default
    assert (partner.level, partner.experience) == (3, 0)
    assert (task.partner_snapshots[0].level, task.partner_snapshots[0].effective_level) == (2, 2)
    assert task.rule_version == 2
    assert task.stamina_cost == 1
    assert task.base_duration == task.final_duration == 12 * 60
    assert task.ready_at == started_at + 12 * 60

    reloaded = PlayerState.model_validate(player.model_dump())
    assert reloaded.owned_partners[0].model_dump() == partner.model_dump()
    assert reloaded.mining_sites[0].task_snapshot.model_dump() == task.model_dump()


def test_player_save_rejects_duplicate_owned_partners():
    payload = {
        "player_id": "duplicate-player",
        "oauth_sub": "duplicate-sub",
        "display_name": "重复居民",
        "stamina_updated_at": 1,
        "created_at": 1,
        "updated_at": 1,
        "owned_partners": [
            {"partner_id": "maple_sprite", "acquired_at": 1},
            {"partner_id": "maple_sprite", "acquired_at": 2},
        ],
    }
    with pytest.raises(ValidationError, match="same partner"):
        PlayerState.model_validate(payload)


def test_player_save_rejects_partner_assigned_to_multiple_plots():
    payload = {
        "player_id": "duplicate-assignment-player",
        "oauth_sub": "duplicate-assignment-sub",
        "display_name": "重复驻场居民",
        "stamina_updated_at": 1,
        "created_at": 1,
        "updated_at": 1,
        "owned_partners": [{"partner_id": "maple_sprite", "acquired_at": 1}],
        "plots": [
            {"slot": 0, "assigned_partner_ids": ["maple_sprite"]},
            {"slot": 1, "assigned_partner_ids": ["maple_sprite"]},
        ],
    }
    with pytest.raises(ValidationError, match="more than one production slot"):
        PlayerState.model_validate(payload)


def test_admin_grants_unique_partner_and_snapshot_resolves_details(game):
    service, repository, clock, player = game
    definition = PartnerDefinition.model_validate({
        "id": "maple_sprite",
        "name": "枫糖",
        "rarity": 4,
        "growth_curve": "linear",
        "tendencies": [{"industry": "farming", "level_1": 18, "level_60": 96}],
        "trait_codes": ["1"],
        "avatar_crops": [
            {"breakthrough": stage, "x": 0, "y": 0, "w": 1, "h": 1}
            for stage in range(3)
        ],
    })
    catalog = PartnerCatalog(partners=[definition])
    service.partner_catalog_loader = lambda: catalog

    result = service.admin_grant_partner(player.player_id, "maple_sprite")
    assert result["player"]["owned_partner_ids"] == ["maple_sprite"]
    assert repository.get(player.player_id).owned_partners[0].level == 1
    assert service.admin_search_players("枫糖")[0]["player_id"] == player.player_id

    partner = service.snapshot_by_sub("oauth-sub-1")["partners"][0]
    assert partner["name"] == "枫糖"
    assert partner["level"] == 1
    assert partner["level_cap"] == 20
    assert partner["tendencies"][0]["current_ability"] > 0
    assert partner["upgrade_available"] is (partner["level"] < partner["level_cap"])

    with pytest.raises(GameError) as duplicate:
        service.admin_grant_partner(player.player_id, "maple_sprite")
    assert duplicate.value.status == 409


def farming_partner(partner_id="farm_partner", level_1=40, level_60=120):
    return PartnerDefinition.model_validate({
        "id": partner_id,
        "name": "田野助手",
        "rarity": 3,
        "growth_curve": "linear",
        "tendencies": [{"industry": "farming", "level_1": level_1, "level_60": level_60}],
        "avatar_crops": [
            {"breakthrough": stage, "x": 0, "y": 0, "w": 1, "h": 1}
            for stage in range(3)
        ],
    })


def test_partner_assignment_creates_immutable_farming_task_snapshot(game):
    service, repository, clock, player = game
    service.content = service.content.model_copy(deep=True)
    catalog = PartnerCatalog(partners=[farming_partner()])
    service.partner_catalog_loader = lambda: catalog
    service.admin_grant_partner(player.player_id, "farm_partner")
    assigned = service.assign_partner("oauth-sub-1", 0, "farm_partner")
    assert assigned["state"]["plots"][0]["assigned_partner_ids"] == ["farm_partner"]

    service.buy("oauth-sub-1", "carrot_seed", 1)
    planted = service.plant("oauth-sub-1", 0, "carrot")
    task = planted["state"]["plots"][0]["task_snapshot"]
    assert task["assigned_partner_ids"] == ["farm_partner"]
    assert task["partner_snapshots"][0]["ability"] > 0
    assert task["total_ability"] == 40
    assert task["base_duration"] == 10_800
    assert task["final_duration"] == 7_200
    assert planted["state"]["plots"][0]["assignment_locked"] is True

    with pytest.raises(GameError) as locked:
        service.assign_partner("oauth-sub-1", 0, "")
    assert locked.value.status == 409

    service.content.crop_map["carrot"].yield_min = 99
    service.content.crop_map["carrot"].yield_max = 99
    clock.advance(task["final_duration"])
    moved = service.assign_partner("oauth-sub-1", 1, "farm_partner")
    assert moved["state"]["plots"][0]["assigned_partner_ids"] == []
    assert moved["state"]["plots"][1]["assigned_partner_ids"] == ["farm_partner"]
    harvested = service.harvest("oauth-sub-1", 0)
    assert 2 <= harvested["result"]["quantity"] <= 4


def test_partner_assignment_enforces_tendency_ownership_and_capacity(game):
    service, _, _, player = game
    gatherer = PartnerDefinition.model_validate({
        **farming_partner("gather_partner").model_dump(),
        "tendencies": [{"industry": "gathering", "level_1": 10, "level_60": 80}],
    })
    catalog = PartnerCatalog(partners=[farming_partner("farm_one"), farming_partner("farm_two"), gatherer])
    service.partner_catalog_loader = lambda: catalog

    with pytest.raises(GameError, match="还没有"):
        service.assign_partner("oauth-sub-1", 0, "farm_one")
    for partner_id in ("farm_one", "farm_two", "gather_partner"):
        service.admin_grant_partner(player.player_id, partner_id)
    with pytest.raises(GameError, match="没有农作倾向"):
        service.assign_partner("oauth-sub-1", 0, "gather_partner")

    service.assign_partner("oauth-sub-1", 0, "farm_one")
    with pytest.raises(GameError, match="编制已满"):
        service.assign_partner("oauth-sub-1", 1, "farm_two")
    moved = service.assign_partner("oauth-sub-1", 1, "farm_one")
    assert moved["state"]["plots"][0]["assigned_partner_ids"] == []
    assert moved["state"]["plots"][1]["assigned_partner_ids"] == ["farm_one"]


@pytest.mark.parametrize("entrypoint", ["web", "qq"])
@pytest.mark.parametrize("old_level,experience", [
    (17, 33500), (18, 50250), (19, 75400), (20, 113100), (20, 150000),
])
def test_resident_curve_preserves_experience_and_persists_recalculated_level(
    game, entrypoint, old_level, experience,
):
    service, repository, _, player = game
    identity = QQIdentity(platform="QQ", bot_id="curve-test", subject="curve-player")
    service.bind_identity(service.create_binding_code("oauth-sub-1"), identity)

    def legacy_state(state):
        state.level = old_level
        state.experience = experience

    repository.update(player.player_id, legacy_state)
    snapshot = (
        service.snapshot_by_identity(identity) if entrypoint == "qq"
        else service.snapshot_by_sub("oauth-sub-1")
    )
    expected = service.content.level_for_xp(experience)
    expected_level = expected.level
    next_definition = next((x for x in service.content.levels if x.level == expected_level + 1), None)
    next_xp = next_definition.total_xp if next_definition else None
    assert snapshot["player"]["level"] == expected_level
    assert snapshot["player"]["experience"] == experience
    assert snapshot["player"]["next_level_xp"] == next_xp
    assert snapshot["player"]["stamina_cap"] == expected.stamina_cap
    assert len(snapshot["plots"]) == expected.plot_slots
    saved = repository.get(player.player_id)
    assert (saved.level, saved.experience) == (expected_level, experience)
    assert service.snapshot_by_sub("oauth-sub-1")["player"] == snapshot["player"]


def test_resident_curve_thresholds_and_level_boundaries():
    content = load_content()
    for previous, current in zip(content.levels, content.levels[1:]):
        assert content.level_for_xp(current.total_xp - 1).level == previous.level
        assert content.level_for_xp(current.total_xp).level == current.level
    assert content.level_for_xp(content.levels[-1].total_xp + 1) == content.levels[-1]
