from __future__ import annotations

import pytest

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import GameContent, load_content
from red_leaf_town.domain import PlayerState, PortalProgressState
from red_leaf_town.domain.economy import add_item
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.partner_content import PartnerCatalog, PartnerDefinition


NOW = 1_700_000_000


def guide_partner(partner_id="fein"):
    return PartnerDefinition.model_validate({
        "id": partner_id,
        "name": "绯恩",
        "rarity": 4,
        "tendencies": [{"industry": "gathering", "level_1": 40, "level_60": 300}],
    })


@pytest.fixture
def game():
    content = load_content().model_copy(deep=True)
    service = GameService(
        content,
        InMemoryPlayerRepository(content),
        clock=lambda: NOW,
        partner_catalog_loader=lambda: PartnerCatalog(partners=[guide_partner()]),
    )
    player = service.ensure_player("portal-sub", "小枫")
    return service, player


def set_level(service, player_id, level):
    total_xp = next(entry.total_xp for entry in service.content.levels if entry.level == level)
    service.repository.update(player_id, lambda state: setattr(state, "experience", total_xp))


def stock(service, player_id, item_id, quantity, quality=0):
    service.repository.update(player_id, lambda state: add_item(state, item_id, quantity, quality))


def portal_of(state, portal_id):
    return next(entry for entry in state["portals"] if entry["portal_id"] == portal_id)


def test_portals_start_locked_until_the_level_is_reached(game):
    service, player = game
    first = portal_of(service.snapshot_by_sub("portal-sub"), "first_gate")

    assert first["unlocked"] is False
    assert first["locked_reason"] == "居民等级达到 2 级"
    assert first["completed"] is False
    assert [entry["delivered"] for entry in first["tributes"]] == [0] * 7

    set_level(service, player.player_id, 2)
    assert portal_of(service.snapshot_by_sub("portal-sub"), "first_gate")["unlocked"] is True


def test_locked_portal_refuses_deliveries(game):
    service, player = game
    stock(service, player.player_id, "carrot", 15)

    with pytest.raises(GameError) as error:
        service.deliver_tribute("portal-sub", "first_gate", "first_gate_carrot", 15)
    assert error.value.code == "portal_locked"
    assert service.repository.get(player.player_id).portals == []


def test_deliveries_accumulate_until_the_tribute_is_filled(game):
    service, player = game
    set_level(service, player.player_id, 2)
    stock(service, player.player_id, "carrot", 25)

    partial = service.deliver_tribute("portal-sub", "first_gate", "first_gate_carrot", 5)["result"]
    assert (partial["delivered"], partial["total_delivered"], partial["required"]) == (5, 5, 15)
    assert partial["tribute_completed"] is False
    assert partial["rewards"] == []

    flame_before = service.snapshot_by_sub("portal-sub")["player"]["maple_flame"]
    # 交多了只收还缺的那些，剩下的留在仓库里。
    filled = service.deliver_tribute("portal-sub", "first_gate", "first_gate_carrot", 99)["result"]
    assert (filled["delivered"], filled["total_delivered"]) == (10, 15)
    assert filled["tribute_completed"] is True
    assert filled["portal_completed"] is False
    assert [entry["source"] for entry in filled["rewards"]] == ["tribute"]
    assert filled["rewards"][0]["maple_flame"] == 150

    state = service.snapshot_by_sub("portal-sub")
    assert state["player"]["maple_flame"] == flame_before + 150
    assert next(entry["quantity"] for entry in state["inventory"] if entry["item_id"] == "carrot") == 10


def test_a_filled_tribute_cannot_be_delivered_again(game):
    service, player = game
    set_level(service, player.player_id, 2)
    stock(service, player.player_id, "carrot", 20)
    service.deliver_tribute("portal-sub", "first_gate", "first_gate_carrot", 15)

    with pytest.raises(GameError) as error:
        service.deliver_tribute("portal-sub", "first_gate", "first_gate_carrot", 1)
    assert error.value.code == "tribute_completed"


def test_delivering_more_than_the_warehouse_holds_is_refused(game):
    service, player = game
    set_level(service, player.player_id, 2)
    stock(service, player.player_id, "carrot", 3)

    with pytest.raises(GameError) as error:
        service.deliver_tribute("portal-sub", "first_gate", "first_gate_carrot", 5)
    assert error.value.code == "resource_insufficient"
    assert service.repository.get(player.player_id).inventory["carrot"] == {0: 3}


def test_completing_every_tribute_settles_the_portal(game):
    service, player = game
    set_level(service, player.player_id, 3)
    tributes = (
        ("first_gate_carrot", "carrot", 15),
        ("first_gate_maple_wood", "maple_wood", 12),
        ("first_gate_tough_fodder", "tough_fodder", 12),
        ("first_gate_woodland_mushroom", "woodland_mushroom", 8),
        ("first_gate_autumn_herb", "autumn_herb", 8),
        ("first_gate_copper_ore", "red_copper_ore", 8),
        ("first_gate_amber_beeswax", "amber_beeswax", 3),
    )
    for tribute_id, item_id, quantity in tributes:
        stock(service, player.player_id, item_id, quantity)

    leaves_before = service.snapshot_by_sub("portal-sub")["player"]["guide_leaves"]

    for tribute_id, item_id, quantity in tributes[:-1]:
        result = service.deliver_tribute("portal-sub", "first_gate", tribute_id, quantity)["result"]
        assert result["tribute_completed"] is True
        assert result["portal_completed"] is False
        assert result["rewards"][0]["maple_flame"] == 150

    last_id, _, last_quantity = tributes[-1]
    finished = service.deliver_tribute("portal-sub", "first_gate", last_id, last_quantity)
    result = finished["result"]

    assert result["portal_completed"] is True
    assert [entry["source"] for entry in result["rewards"]] == ["tribute", "portal"]
    completion = result["rewards"][-1]
    assert completion["guide_leaves"] == 10
    assert completion["items"] == []
    assert result["unlocked_portals"] == []

    state = finished["state"]
    assert portal_of(state, "first_gate")["completed"] is True
    assert state["player"]["guide_leaves"] == leaves_before + 10


def test_tribute_quality_floor_filters_the_warehouse(game):
    service, player = game
    set_level(service, player.player_id, 2)
    # 出厂内容暂不要求品质，这里临时给一项贡品加上品质门槛来测试底层机制。
    herb_tribute = next(
        entry for entry in service.content.portal_map["first_gate"].tributes
        if entry.id == "first_gate_autumn_herb"
    )
    herb_tribute.min_quality = 2

    stock(service, player.player_id, "autumn_herb", 8, 1)
    herb = next(
        entry for entry in portal_of(service.snapshot_by_sub("portal-sub"), "first_gate")["tributes"]
        if entry["id"] == "first_gate_autumn_herb"
    )
    assert (herb["min_quality"], herb["min_quality_name"]) == (2, "良品")
    assert (herb["owned"], herb["deliverable"]) == (0, 0)

    with pytest.raises(GameError) as error:
        service.deliver_tribute("portal-sub", "first_gate", "first_gate_autumn_herb", 1)
    assert error.value.code == "resource_insufficient"
    assert "良品以上的秋露草" in error.value.message

    stock(service, player.player_id, "autumn_herb", 3, 4)
    stock(service, player.player_id, "autumn_herb", 5, 2)
    delivered = service.deliver_tribute("portal-sub", "first_gate", "first_gate_autumn_herb", 6)["result"]

    # 够格的品质里先扣最低的，臻品只用来补差额。
    assert delivered["consumed"] == [
        {"item_id": "autumn_herb", "quality": 2, "quantity": 5},
        {"item_id": "autumn_herb", "quality": 4, "quantity": 1},
    ]
    inventory = service.repository.get(player.player_id).inventory["autumn_herb"]
    assert inventory == {1: 8, 4: 2}


def test_completion_reward_can_hand_out_talent_points(game):
    service, player = game
    set_level(service, player.player_id, 2)
    # 出厂的全完成奖励暂不发天赋点，这里临时改一下来测试底层机制。
    service.content.portal_map["first_gate"].completion_reward.talent_points = 1
    tributes = (
        ("first_gate_carrot", "carrot", 15),
        ("first_gate_maple_wood", "maple_wood", 12),
        ("first_gate_tough_fodder", "tough_fodder", 12),
        ("first_gate_woodland_mushroom", "woodland_mushroom", 8),
        ("first_gate_autumn_herb", "autumn_herb", 8),
        ("first_gate_copper_ore", "red_copper_ore", 8),
        ("first_gate_amber_beeswax", "amber_beeswax", 3),
    )
    for tribute_id, item_id, quantity in tributes:
        stock(service, player.player_id, item_id, quantity)
    for tribute_id, _, quantity in tributes[:-1]:
        service.deliver_tribute("portal-sub", "first_gate", tribute_id, quantity)

    before = service.snapshot_by_sub("portal-sub")["talents"]["available_points"]
    last_id, _, last_quantity = tributes[-1]
    finished = service.deliver_tribute("portal-sub", "first_gate", last_id, last_quantity)

    assert finished["result"]["rewards"][-1]["talent_points"] == 1
    assert service.repository.get(player.player_id).bonus_talent_points == 1
    # 传送门发的天赋点和升级得到的进同一个池子。
    assert finished["state"]["talents"]["available_points"] >= before + 1


def test_reward_can_make_a_partner_join(game):
    service, player = game
    set_level(service, player.player_id, 2)
    stock(service, player.player_id, "carrot", 15)
    service.content.portal_map["first_gate"].tributes[0].reward.partner_ids = ["fein"]

    result = service.deliver_tribute("portal-sub", "first_gate", "first_gate_carrot", 15)["result"]

    assert result["rewards"][0]["partners"] == [{"partner_id": "fein", "name": "绯恩"}]
    assert [entry.partner_id for entry in service.repository.get(player.player_id).owned_partners] == ["fein"]


def test_unknown_portal_or_tribute_is_rejected(game):
    service, _ = game
    for portal_id, tribute_id in (("nope", "first_gate_carrot"), ("first_gate", "nope")):
        with pytest.raises(GameError) as error:
            service.deliver_tribute("portal-sub", portal_id, tribute_id, 1)
        assert error.value.code == "tribute_not_found"


def test_delivery_quantity_must_be_positive(game):
    service, player = game
    set_level(service, player.player_id, 2)
    with pytest.raises(GameError) as error:
        service.deliver_tribute("portal-sub", "first_gate", "first_gate_carrot", 0)
    assert error.value.code == "invalid_quantity"


def portal_payload(portal_id="alpha", prerequisites=(), **overrides):
    return {
        "id": portal_id,
        "name": portal_id,
        "accent": "#8aa6e0",
        "prerequisites": list(prerequisites),
        "tributes": [{"id": f"{portal_id}_tribute", "item_id": "carrot", "quantity": 1}],
        **overrides,
    }


def content_with_portals(portals):
    payload = load_content().model_dump()
    payload["portals"] = portals
    return GameContent.model_validate(payload)


def test_portal_prerequisites_cannot_form_a_cycle():
    with pytest.raises(ValueError, match="cycle"):
        content_with_portals([
            portal_payload("alpha", ["beta"]),
            portal_payload("beta", ["alpha"]),
        ])


def test_portal_cannot_require_itself():
    with pytest.raises(ValueError):
        content_with_portals([portal_payload("alpha", ["alpha"])])


def test_portal_rejects_unknown_references():
    with pytest.raises(ValueError, match="unknown prerequisites"):
        content_with_portals([portal_payload("alpha", ["ghost"])])
    with pytest.raises(ValueError, match="unknown item"):
        content_with_portals([portal_payload(
            "alpha",
            tributes=[{"id": "alpha_tribute", "item_id": "ghost", "quantity": 1}],
        )])
    with pytest.raises(ValueError, match="rewards an unknown item"):
        content_with_portals([portal_payload(
            "alpha",
            completion_reward={"items": [{"item_id": "ghost", "quantity": 1}]},
        )])


def test_quality_floor_needs_an_item_that_can_have_quality():
    with pytest.raises(ValueError, match="quality"):
        content_with_portals([portal_payload(
            "alpha",
            tributes=[{"id": "alpha_tribute", "item_id": "carrot_seed", "quantity": 1, "min_quality": 2}],
        )])


def test_original_portals_keep_their_conditions():
    content = load_content()

    assert {"first_gate", "second_gate"} <= set(content.portal_map)
    first_gate = content.portal_map["first_gate"]
    assert first_gate.prerequisites == []
    assert len(first_gate.tributes) == 7
    assert all(tribute.min_quality == 0 for tribute in first_gate.tributes)
    assert all(tribute.reward.maple_flame == 150 for tribute in first_gate.tributes)
    assert first_gate.completion_reward.guide_leaves == 10


def test_second_gate_asks_for_ten_tributes_across_every_industry():
    content = load_content()
    second_gate = content.portal_map["second_gate"]

    assert second_gate.min_level == 12
    assert second_gate.prerequisites == ["first_gate"]
    assert [(tribute.item_id, tribute.quantity, tribute.min_quality) for tribute in second_gate.tributes] == [
        ("egg", 5, 2),
        ("pumpkin", 5, 3),
        ("milk", 5, 0),
        ("pond_crucian", 5, 3),
        ("silver_star_moss", 3, 0),
        ("giant_stream_carp", 1, 0),
        ("refined_fodder", 10, 0),
        ("peach_berry", 1, 0),
        ("partner_notes_large", 1, 0),
        ("hunters_charm", 1, 0),
    ]
    # 每一项 150 枫火，收齐再给 10 张同行叶，正好一次十连。
    assert all(tribute.reward.maple_flame == 150 for tribute in second_gate.tributes)
    assert second_gate.completion_reward.guide_leaves == 10
    assert second_gate.completion_reward.maple_flame == 0


def test_second_gate_takes_quality_free_gear_and_notes(game):
    service, player = game
    set_level(service, player.player_id, 12)
    service.repository.update(
        player.player_id,
        lambda state: state.portals.append(PortalProgressState(portal_id="first_gate", completed_at=NOW)),
    )
    stock(service, player.player_id, "hunters_charm", 1)
    stock(service, player.player_id, "partner_notes_large", 1)

    charm = service.deliver_tribute("portal-sub", "second_gate", "second_gate_hunters_charm", 1)["result"]
    notes = service.deliver_tribute("portal-sub", "second_gate", "second_gate_partner_notes_large", 1)["result"]

    assert charm["tribute_completed"] is True
    assert charm["rewards"][0]["maple_flame"] == 150
    assert notes["tribute_completed"] is True
    saved = service.repository.get(player.player_id)
    assert not saved.inventory.get("hunters_charm")
    assert not saved.inventory.get("partner_notes_large")


def test_second_gate_stays_locked_until_the_first_one_is_done(game):
    service, player = game
    set_level(service, player.player_id, 12)
    second = portal_of(service.snapshot_by_sub("portal-sub"), "second_gate")

    assert second["unlocked"] is False
    assert second["locked_reason"] == "先完成第一传送门"
    assert second["tribute_count"] == 10

    stock(service, player.player_id, "silver_star_moss", 3)
    with pytest.raises(GameError) as error:
        service.deliver_tribute("portal-sub", "second_gate", "second_gate_silver_star_moss", 3)
    assert error.value.code == "portal_locked"

    service.repository.update(
        player.player_id,
        lambda state: state.portals.append(PortalProgressState(portal_id="first_gate", completed_at=NOW)),
    )
    unlocked = portal_of(service.snapshot_by_sub("portal-sub"), "second_gate")

    assert unlocked["unlocked"] is True
    assert unlocked["locked_reason"] is None


def test_shipped_portal_rewards_only_reference_known_partners():
    from red_leaf_town.partner_content import load_partner_catalog

    known = set(load_partner_catalog().partner_map)
    for portal in load_content().portals:
        rewards = [portal.completion_reward, *[tribute.reward for tribute in portal.tributes]]
        for reward in rewards:
            assert not set(reward.partner_ids) - known


def test_legacy_save_migrates_to_schema_eleven():
    player = PlayerState.model_validate({
        "schema_version": 10,
        "player_id": "player-1",
        "oauth_sub": "sub-1",
        "display_name": "小枫",
        "stamina_updated_at": 0,
        "created_at": 0,
        "updated_at": 0,
    })

    assert player.schema_version == PlayerState.model_fields["schema_version"].default
    assert player.portals == []
    assert player.bonus_talent_points == 0


def test_portal_progress_survives_a_round_trip(game):
    service, player = game
    set_level(service, player.player_id, 2)
    stock(service, player.player_id, "carrot", 5)
    service.deliver_tribute("portal-sub", "first_gate", "first_gate_carrot", 5)

    stored = service.repository.get(player.player_id)
    restored = PlayerState.model_validate_json(stored.model_dump_json())
    progress = restored.portals[0]
    assert progress.portal_id == "first_gate"
    assert progress.tribute("first_gate_carrot").delivered == 5
    assert progress.tribute("first_gate_maple_wood").delivered == 0
