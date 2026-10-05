from __future__ import annotations

import asyncio
import functools
import io

import pytest
from quart import Quart
from PIL import Image
from werkzeug.datastructures import FileStorage

from private.libraries.jwt import AUD_RED_LEAF_TOWN, subject_encode
from red_leaf_town.application import GameService
from red_leaf_town.content import load_content
from red_leaf_town.domain import QQIdentity
from red_leaf_town.domain.economy import add_item
from red_leaf_town.infrastructure import (
    InMemoryCommissionBoard,
    InMemoryMailbox,
    InMemoryPlayerRepository,
    InMemoryRedemptionCodes,
)
from red_leaf_town.runtime import set_service
from red_leaf_town.partner_content import load_partner_catalog
from red_leaf_town.web.routes import COOKIE_NAME, create_blueprint


def runs(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


@pytest.fixture
def service():
    content = load_content()
    game = GameService(
        content,
        InMemoryPlayerRepository(content),
        commission_board=InMemoryCommissionBoard(),
        mailbox=InMemoryMailbox(),
        redemption_codes=InMemoryRedemptionCodes(),
        clock=lambda: 1_700_000_000,
    )
    game.ensure_player("route-sub", "小枫")
    set_service(game)
    yield game
    set_service(None)


@pytest.fixture
def client(service):
    app = Quart(__name__)
    app.register_blueprint(create_blueprint())
    return app.test_client()


def authenticate(client, sub="route-sub"):
    client.set_cookie("localhost", COOKIE_NAME, subject_encode(sub, AUD_RED_LEAF_TOWN))


def valid_partner_payload():
    return {
        "id": "maple_sprite",
        "name": "枫糖",
        "rarity": 4,
        "standard_recruitable": False,
        "description": "擅长农作的伙伴",
        "growth_curve": "linear",
        "tendencies": [{"industry": "farming", "level_1": 16, "level_60": 92}],
        "trait_codes": ["1", "2"],
        "artworks": [],
        "avatar_crop": {"source_breakthrough": 0, "x": 0, "y": 0, "w": 100, "h": 100},
    }


@pytest.fixture
def admin_client(service, tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"admin_subs": ["test-admin-sub"]}', encoding="utf-8")
    monkeypatch.setattr("red_leaf_town.config.CONFIG_PATH", config_path)
    catalog_path = tmp_path / "partners.json"
    content_path = tmp_path / "game.json"
    content_path.write_text(f"{load_content().model_dump_json(indent=2)}\n", encoding="utf-8")
    service.partner_catalog_loader = lambda: load_partner_catalog(catalog_path)
    app = Quart(f"{__name__}.admin")
    app.register_blueprint(create_blueprint(game_content_path=content_path, partner_catalog_path=catalog_path))
    return app.test_client()


def admin_headers(client):
    client.set_cookie("localhost", COOKIE_NAME, subject_encode("test-admin-sub", AUD_RED_LEAF_TOWN))
    return {"X-Requested-With": "XMLHttpRequest"}


@runs
async def test_state_requires_login(client):
    response = await client.get("/api/red-leaf-town/state")
    assert response.status_code == 401
    assert (await response.get_json())["code"] == "unauthorized"


@runs
async def test_state_exposes_achievement_catalog(client):
    authenticate(client)
    response = await client.get("/api/red-leaf-town/state")
    state = (await response.get_json())["data"]

    assert response.status_code == 200
    assert state["achievements"]["total"] == len(load_content().achievements)
    assert len(state["achievements"]["entries"]) == state["achievements"]["total"]
    assert {entry["tier"] for entry in state["achievements"]["entries"]} == {"blue", "purple", "gold"}


@runs
async def test_achievement_reward_must_be_claimed_from_achievement_api(client, service):
    player = service.repository.get_by_sub("route-sub")
    service.repository.update(player.player_id, lambda state: state.seen_story_ids.append("opening_arrival"))
    authenticate(client)

    state_response = await client.get("/api/red-leaf-town/state")
    state = (await state_response.get_json())["data"]
    entry = next(item for item in state["achievements"]["entries"] if item["achievement_id"] == "arrival_from_beyond")
    assert (entry["completed"], entry["claimable"], entry["claimed"]) == (True, True, False)
    assert state["player"]["maple_flame"] == 0

    claimed_response = await client.post("/api/red-leaf-town/achievements/arrival_from_beyond/claim")
    claimed = (await claimed_response.get_json())["data"]
    assert claimed_response.status_code == 200
    assert claimed["result"]["maple_flame"] == 50
    assert claimed["state"]["player"]["maple_flame"] == 50
    assert claimed["state"]["achievements"]["claimable"] == 0

    replayed = await client.post("/api/red-leaf-town/achievements/arrival_from_beyond/claim")
    assert replayed.status_code == 409
    assert (await replayed.get_json())["code"] == "achievement_claimed"


@runs
async def test_livestock_api_builds_buys_and_collects(client, service):
    authenticate(client)
    player = service.repository.get_by_sub("route-sub")
    service.repository.update(player.player_id, lambda state: setattr(state, "coins", 50_000))
    service.repository.update(
        player.player_id,
        lambda state: setattr(state, "experience", next(
            entry.total_xp for entry in service.content.levels if entry.level == 8
        )),
    )
    service.repository.update(player.player_id, lambda state: add_item(state, "maple_wood", 30))

    built = await client.post("/api/red-leaf-town/livestock/facilities/coop_1/build")
    assert built.status_code == 200
    facilities = (await built.get_json())["data"]["state"]["livestock"]["facilities"]
    # 散养地在同一次请求里被鸡舍回收。
    assert [entry["facility_id"] for entry in facilities] == ["coop_1"]

    bought = await client.post(
        "/api/red-leaf-town/livestock/facilities/coop_1/buy",
        json={"species_id": "chicken"},
    )
    assert bought.status_code == 200
    assert (await bought.get_json())["data"]["result"]["animal"]["stage"] == "juvenile"

    empty = await client.post("/api/red-leaf-town/livestock/facilities/coop_1/collect", json={})
    assert empty.status_code == 409

    animal_id = service.repository.get(player.player_id).animals[0].animal_id
    service.repository.update(
        player.player_id,
        lambda state: setattr(state, "stamina", 10),
    )
    cared = await client.post(f"/api/red-leaf-town/livestock/animals/{animal_id}/care")
    assert cared.status_code == 200
    assert (await cared.get_json())["data"]["result"]["affection"] == 8


@runs
async def test_shop_and_plant_api(client):
    authenticate(client)
    bought = await client.post("/api/red-leaf-town/shop/buy", json={"shop_id": "carrot_seed", "quantity": 2})
    assert bought.status_code == 200
    assert (await bought.get_json())["data"]["state"]["player"]["coins"] == 60

    planted = await client.post("/api/red-leaf-town/plots/0/plant", json={"crop_id": "carrot"})
    body = await planted.get_json()
    assert planted.status_code == 200
    assert body["data"]["state"]["plots"][0]["crop_id"] == "carrot"
    assert len(body["data"]["state"]["plots"][0]["task_snapshot"]["quality_parameters"]["probabilities"]) == 5

    cancelled = await client.post("/api/red-leaf-town/tasks/cancel", json={"industry": "farming", "slot_id": "0"})
    cancelled_body = await cancelled.get_json()
    assert cancelled.status_code == 200
    assert cancelled_body["data"]["state"]["plots"][0]["empty"] is True
    seed_entry = next(
        item for item in cancelled_body["data"]["state"]["inventory"] if item["item_id"] == "carrot_seed"
    )
    assert seed_entry["quantity"] == 2


@runs
async def test_hidden_transport_exploration_api_starts_resolves_and_withdraws(client, service):
    player = service.repository.get_by_sub("route-sub")
    service.admin_grant_partner(player.player_id, "aishen")

    def prepare(state):
        state.experience = service.content.levels[-1].total_xp
        state.coins = 5000
        state.stamina = 50

    service.repository.update(player.player_id, prepare)
    authenticate(client)

    started = await client.post(
        "/api/red-leaf-town/exploration/red_maple_hinterland/start",
        json={"partner_ids": ["aishen"], "leader_partner_id": "aishen"},
    )
    started_body = (await started.get_json())["data"]
    assert started.status_code == 200
    assert started_body["state"]["player"]["coins"] == 4000
    assert started_body["state"]["exploration"]["active_run"]["current_event"]["id"] == "windfallen_timber"

    resolved = await client.post(
        "/api/red-leaf-town/exploration/current/resolve",
        json={"choice_id": "clear_edges"},
    )
    resolved_body = (await resolved.get_json())["data"]
    assert resolved.status_code == 200
    assert resolved_body["result"]["drops"][0]["item_id"] == "maple_wood"
    assert resolved_body["state"]["exploration"]["active_run"]["depth"] == 1

    withdrawn = await client.post("/api/red-leaf-town/exploration/current/withdraw")
    withdrawn_body = (await withdrawn.get_json())["data"]
    assert withdrawn.status_code == 200
    assert withdrawn_body["state"]["exploration"]["active_run"] is None
    assert any(item["item_id"] == "maple_wood" for item in withdrawn_body["state"]["inventory"])


@runs
async def test_delve_api_gates_on_the_beta_list_and_runs_a_battle_turn(client, service, monkeypatch):
    from red_leaf_town.domain.economy import add_item

    # 灵果草甸已经外放，这里临时把它标回内测，测的是接口上的灰度门。
    service.content.exploration_expedition_map["spiritfruit_meadow"].beta = True
    player = service.repository.get_by_sub("route-sub")
    for partner_id in ("aishen", "gujian_miao", "leilei"):
        service.admin_grant_partner(player.player_id, partner_id)

    def prepare(state):
        state.experience = service.content.levels[-1].total_xp
        state.coins = 20_000
        state.stamina = 60
        add_item(state, "red_copper_greatsword", 3)

    service.repository.update(player.player_id, prepare)
    authenticate(client)
    payload = {
        "partner_ids": ["aishen", "gujian_miao", "leilei"],
        "leader_partner_id": "aishen",
        "loadout": {
            partner_id: {"weapon_item_id": "red_copper_greatsword"}
            for partner_id in ("aishen", "gujian_miao", "leilei")
        },
    }

    monkeypatch.delenv("RED_LEAF_TOWN_BETA_PLAYERS", raising=False)
    blocked = await client.post("/api/red-leaf-town/exploration/spiritfruit_meadow/start", json=payload)
    assert blocked.status_code == 400
    assert "内测" in (await blocked.get_json())["message"]

    monkeypatch.setenv("RED_LEAF_TOWN_BETA_PLAYERS", player.player_id)
    started = await client.post("/api/red-leaf-town/exploration/spiritfruit_meadow/start", json=payload)
    started_body = (await started.get_json())["data"]
    assert started.status_code == 200
    run = started_body["state"]["exploration"]["active_run"]
    assert run["party"][0]["combat"]["hp"] > 0

    battle_choice = next(
        (choice["id"] for choice in run["current_event"]["choices"] if choice.get("battle")),
        "",
    )
    if not battle_choice:
        return
    resolved = await client.post(
        "/api/red-leaf-town/exploration/current/resolve",
        json={"choice_id": battle_choice},
    )
    battle = (await resolved.get_json())["data"]["state"]["exploration"]["active_run"]["battle"]
    assert resolved.status_code == 200
    assert battle is not None

    acted = await client.post(
        "/api/red-leaf-town/exploration/current/battle/act",
        json={"action": "attack", "target": battle["enemies"][0]["key"]},
    )
    acted_body = (await acted.get_json())["data"]
    assert acted.status_code == 200
    assert acted_body["result"]["outcome"] in ("ongoing", "victory")
    assert acted_body["result"]["logs"]


@runs
async def test_gacha_and_partner_growth_apis_commit_domain_actions(client, service):
    from red_leaf_town.domain.economy import add_item

    player = service.repository.get_by_sub("route-sub")

    def prepare(state):
        state.experience = service.content.levels[-1].total_xp
        add_item(state, next(iter(service.content.partner_growth.experience_books)), 1)

    service.repository.update(player.player_id, prepare)
    service.admin_grant_partner(player.player_id, "xiang_hanyang")
    authenticate(client)

    pulled = await client.post(
        "/api/red-leaf-town/gacha/pull",
        json={"count": 10, "request_id": "route-recruit-request", "pool_id": "standard-1"},
    )
    replayed = await client.post(
        "/api/red-leaf-town/gacha/pull",
        json={"count": 10, "request_id": "route-recruit-request", "pool_id": "standard-1"},
    )
    pulled_body = (await pulled.get_json())["data"]
    replayed_body = (await replayed.get_json())["data"]

    assert pulled.status_code == replayed.status_code == 200
    assert replayed_body["result"]["replayed"] is True
    assert pulled_body["result"]["results"] == replayed_body["result"]["results"]

    book_id = next(iter(service.content.partner_growth.experience_books))
    trained = await client.post(
        "/api/red-leaf-town/partners/xiang_hanyang/train",
        json={"item_id": book_id, "quantity": 1},
    )
    assert trained.status_code == 200
    assert (await trained.get_json())["data"]["result"]["level"] > 1


@runs
async def test_state_lists_story_crop_without_putting_its_seed_in_shop(client):
    authenticate(client)
    response = await client.get("/api/red-leaf-town/state")
    state = (await response.get_json())["data"]

    assert "orange_berry" in {crop["id"] for crop in state["crops"]}
    assert "orange_berry_seed" not in {entry["item_id"] for entry in state["shop"]}


@runs
async def test_sell_api_accepts_exact_quality_bucket(client, service):
    from red_leaf_town.domain.economy import add_item

    player = service.repository.get_by_sub("route-sub")
    service.repository.update(player.player_id, lambda state: add_item(state, "carrot", 2, 3))
    authenticate(client)
    response = await client.post(
        "/api/red-leaf-town/inventory/carrot/sell",
        json={"quantity": 1, "quality": 3},
    )
    body = await response.get_json()
    assert response.status_code == 200
    assert body["data"]["result"]["quality_name"] == "上品"
    assert body["data"]["result"]["unit_price"] == 15


@runs
async def test_gathering_partner_start_and_talent_apis(client, service):
    player = service.repository.get_by_sub("route-sub")
    service.admin_grant_partner(player.player_id, "fein")
    authenticate(client)

    assigned = await client.put(
        "/api/red-leaf-town/gathering/sites/maple_forest/partner",
        json={"partner_id": "fein"},
    )
    assert assigned.status_code == 200
    assert (await assigned.get_json())["data"]["state"]["gathering_sites"][0]["assigned_partner_ids"] == ["fein"]

    started = await client.post(
        "/api/red-leaf-town/gathering/sites/maple_forest/start",
        json={"task_id": "collect_maple_wood"},
    )
    body = await started.get_json()
    assert started.status_code == 200
    assert body["data"]["state"]["gathering_sites"][0]["task_snapshot"]["industry"] == "gathering"
    assert body["data"]["state"]["partners"][0]["locked"] is True

    def finish_gathering(state):
        task = state.gathering_sites[0].task_snapshot
        task.started_at = 1_700_000_000 - task.final_duration
        task.ready_at = 1_700_000_000

    service.repository.update(player.player_id, finish_gathering)
    ready_response = await client.get("/api/red-leaf-town/state")
    ready_site = (await ready_response.get_json())["data"]["gathering_sites"][0]
    pool_ids = {"maple_wood", "woodland_mushroom", "maple_resin", "amber_beeswax"}
    assert ready_site["task_results"]
    assert {result["item_id"] for result in ready_site["task_results"]} <= pool_ids

    collected = await client.post("/api/red-leaf-town/gathering/sites/maple_forest/collect")
    collected_body = await collected.get_json()
    assert collected.status_code == 200
    assert {drop["item_id"] for drop in collected_body["data"]["result"]["drops"]} <= pool_ids
    assert collected_body["data"]["state"]["gathering_sites"][0]["task_results"] == []

    service.repository.update(player.player_id, lambda state: setattr(state, "experience", 20))
    unlocked = await client.post("/api/red-leaf-town/talents/gathering_roster_1/unlock")
    unlocked_body = await unlocked.get_json()
    assert unlocked.status_code == 200
    assert unlocked_body["data"]["state"]["industry_rules"]["gathering"]["partner_capacity"] == 2


@runs
async def test_crafting_recipe_hook_and_start_api(client, service):
    from red_leaf_town.domain.economy import add_item

    player = service.repository.get_by_sub("route-sub")
    service.repository.update(player.player_id, lambda state: setattr(state, "experience", 60))
    service.repository.update(player.player_id, lambda state: add_item(state, "maple_wood", 2, 1))
    authenticate(client)

    state_response = await client.get("/api/red-leaf-town/state")
    station = (await state_response.get_json())["data"]["crafting_stations"][0]
    recipe = next(entry for entry in station["recipes"] if entry["id"] == "saw_maple_plank")
    assert recipe["unlocked"] is True
    assert recipe["unlock_condition"]["hook"] == "player_level"

    locked = await client.post(
        "/api/red-leaf-town/crafting/stations/town_workbench/start",
        json={"recipe_id": "pickle_carrot"},
    )
    assert locked.status_code == 409
    assert (await locked.get_json())["code"] == "recipe_locked"

    started = await client.post(
        "/api/red-leaf-town/crafting/stations/town_workbench/start",
        json={"recipe_id": "saw_maple_plank"},
    )
    body = await started.get_json()
    assert started.status_code == 200
    assert body["data"]["state"]["crafting_stations"][0]["task_snapshot"]["industry"] == "crafting"
    assert body["data"]["result"]["consumed_inputs"] == [
        {"item_id": "maple_wood", "quality": 1, "quantity": 2},
    ]


@runs
async def test_mining_start_api_allows_player_without_partner(client, service):
    player = service.repository.get_by_sub("route-sub")
    service.repository.update(player.player_id, lambda state: setattr(state, "experience", 20))
    authenticate(client)

    started = await client.post(
        "/api/red-leaf-town/mining/sites/copper_foothill/start",
        json={"task_id": "mine_red_copper"},
    )
    body = await started.get_json()
    assert started.status_code == 200
    site = body["data"]["state"]["mining_sites"][0]
    assert site["task_snapshot"]["industry"] == "mining"
    assert site["task_snapshot"]["assigned_partner_ids"] == []


@runs
async def test_invalid_action_returns_structured_error(client):
    authenticate(client)
    response = await client.post("/api/red-leaf-town/plots/0/plant", json={"crop_id": "carrot"})
    assert response.status_code == 400
    body = await response.get_json()
    assert body == {"code": "resource_insufficient", "message": "物品数量不足"}


@runs
async def test_binding_code_api(client):
    authenticate(client)
    response = await client.post("/api/red-leaf-town/account/binding-code")
    body = await response.get_json()
    assert response.status_code == 200
    assert body["data"]["command"].startswith("绑定红叶镇 ")
    assert body["data"]["expires_in"] == 600


@runs
async def test_oauth_callback_creates_one_player_and_sets_isolated_cookie(client, service, monkeypatch):
    import private.libraries.df_oauth as oauth

    async def complete_login(code, state):
        return {
            "app": "red_leaf_town",
            "sub": "new-oauth-sub",
            "username": "maple",
            "nickname": "红枫",
            "next": "/red-leaf-town/",
        }

    monkeypatch.setattr(oauth, "complete_login", complete_login)
    response = await client.get("/api/oauth/red-leaf-town/callback?code=code&state=state")
    assert response.status_code == 302
    assert response.headers["Location"] == "/red-leaf-town/"
    assert COOKIE_NAME in response.headers.get("Set-Cookie", "")
    first = service.repository.get_by_sub("new-oauth-sub")
    assert first and first.display_name == "红枫"

    await client.get("/api/oauth/red-leaf-town/callback?code=code&state=state")
    second = service.repository.get_by_sub("new-oauth-sub")
    assert second.player_id == first.player_id


@runs
async def test_partner_admin_requires_admin_session(admin_client):
    response = await admin_client.get("/api/red-leaf-town/admin/partners")
    assert response.status_code == 403
    assert (await response.get_json())["code"] == "forbidden"


@runs
async def test_crop_admin_updates_content_and_live_service(admin_client, service):
    forbidden = await admin_client.get("/api/red-leaf-town/admin/crops")
    assert forbidden.status_code == 403

    listed = await admin_client.get("/api/red-leaf-town/admin/crops", headers=admin_headers(admin_client))
    payload = (await listed.get_json())["data"]
    carrot = next(crop for crop in payload["crops"] if crop["id"] == "carrot")
    tutorial = next(crop for crop in payload["crops"] if crop["id"] == "orange_berry")
    assert carrot["chart_enabled"] is True
    assert tutorial["seed_price"] is None

    updated = await admin_client.put(
        "/api/red-leaf-town/admin/crops",
        json={"crops": [{
            "id": "carrot",
            "seed_price": 9,
            "produce_sell_price": 11,
            "growth_seconds": 7200,
            "quality": {
                "thresholds": [5, 25, 45, 160],
                "width": 10,
                "miracle_probability_cap": 0.002,
                "miracle_eligible": True,
            },
        }]},
        headers=admin_headers(admin_client),
    )
    assert updated.status_code == 200
    assert service.content.crop_map["carrot"].growth_seconds == 7200
    assert service.content.item_map["carrot"].sell_price == 11
    assert service.content.shop_map["carrot_seed"].price == 9

    invalid = await admin_client.put(
        "/api/red-leaf-town/admin/crops",
        json={"crops": [{"id": "carrot", "quality": {"thresholds": [5, 5, 45, 160], "width": 10}}]},
        headers=admin_headers(admin_client),
    )
    assert invalid.status_code == 400
    assert (await invalid.get_json())["code"] == "invalid_crop_balance"


@runs
async def test_partner_admin_crud(admin_client):
    created = await admin_client.post(
        "/api/red-leaf-town/admin/partners",
        json=valid_partner_payload(),
        headers=admin_headers(admin_client),
    )
    assert created.status_code == 201
    created_data = (await created.get_json())["data"]
    assert created_data["complete"] is False
    assert created_data["standard_recruitable"] is False

    duplicate = await admin_client.post(
        "/api/red-leaf-town/admin/partners",
        json=valid_partner_payload(),
        headers=admin_headers(admin_client),
    )
    assert duplicate.status_code == 409

    payload = valid_partner_payload()
    payload["name"] = "枫糖糖"
    payload["growth_curve"] = "late"
    updated = await admin_client.put(
        "/api/red-leaf-town/admin/partners/maple_sprite",
        json=payload,
        headers=admin_headers(admin_client),
    )
    assert updated.status_code == 200

    listed = await admin_client.get("/api/red-leaf-town/admin/partners", headers=admin_headers(admin_client))
    body = await listed.get_json()
    assert body["data"]["partners"][0]["name"] == "枫糖糖"
    assert body["data"]["partners"][0]["standard_recruitable"] is False
    assert body["data"]["options"]["breakthrough_level_caps"] == [20, 40, 60]
    assert "configured" in body["data"]["options"]["cdn"]
    assert [trait["code"] for trait in body["data"]["options"]["traits"][:4]] == ["1", "2", "3", "4"]

    deleted = await admin_client.delete(
        "/api/red-leaf-town/admin/partners/maple_sprite",
        headers=admin_headers(admin_client),
    )
    assert deleted.status_code == 200


@runs
async def test_admin_searches_player_and_grants_partner_once(admin_client):
    created = await admin_client.post(
        "/api/red-leaf-town/admin/partners",
        json=valid_partner_payload(),
        headers=admin_headers(admin_client),
    )
    assert created.status_code == 201

    searched = await admin_client.get(
        "/api/red-leaf-town/admin/players",
        query_string={"q": "小枫"},
        headers=admin_headers(admin_client),
    )
    player = (await searched.get_json())["data"][0]
    assert player["owned_partner_ids"] == []

    granted = await admin_client.post(
        f"/api/red-leaf-town/admin/players/{player['player_id']}/partners",
        json={"partner_id": "maple_sprite"},
        headers=admin_headers(admin_client),
    )
    assert granted.status_code == 200
    assert (await granted.get_json())["data"]["player"]["owned_partner_ids"] == ["maple_sprite"]

    duplicate = await admin_client.post(
        f"/api/red-leaf-town/admin/players/{player['player_id']}/partners",
        json={"partner_id": "maple_sprite"},
        headers=admin_headers(admin_client),
    )
    assert duplicate.status_code == 409

    authenticate(admin_client)
    assigned = await admin_client.put(
        "/api/red-leaf-town/plots/0/partners",
        json={"partner_id": "maple_sprite"},
    )
    assert assigned.status_code == 200
    assert (await assigned.get_json())["data"]["state"]["plots"][0]["assigned_partner_ids"] == ["maple_sprite"]

    await admin_client.post(
        "/api/red-leaf-town/shop/buy",
        json={"shop_id": "carrot_seed", "quantity": 1},
    )
    planted = await admin_client.post(
        "/api/red-leaf-town/plots/0/plant",
        json={"crop_id": "carrot"},
    )
    task = (await planted.get_json())["data"]["state"]["plots"][0]["task_snapshot"]
    assert task["assigned_partner_ids"] == ["maple_sprite"]
    assert task["final_duration"] < task["base_duration"]

    locked = await admin_client.put(
        "/api/red-leaf-town/plots/0/partners",
        json={"partner_id": ""},
    )
    assert locked.status_code == 409
    assert (await locked.get_json())["code"] == "partner_assignment_locked"

    state = await admin_client.get("/api/red-leaf-town/state")
    partner = (await state.get_json())["data"]["partners"][0]
    assert partner["partner_id"] == "maple_sprite"
    assert partner["upgrade_available"] is (partner["level"] < partner["level_cap"])


@runs
async def test_partner_artwork_uploads_through_cdn_provider(admin_client, monkeypatch):
    from src.libraries import cdn_client

    await admin_client.post(
        "/api/red-leaf-town/admin/partners",
        json=valid_partner_payload(),
        headers=admin_headers(admin_client),
    )
    uploaded = {}

    def upload_bytes_at(path, data, content_type):
        uploaded.update(path=path, data=data, content_type=content_type)
        return True

    monkeypatch.setattr(cdn_client, "upload_bytes_at", upload_bytes_at)
    monkeypatch.setattr(cdn_client, "cdn_url_at", lambda path: f"https://cdn.example/{path}")

    image = io.BytesIO()
    Image.new("RGB", (1200, 1600), "#a04030").save(image, format="PNG")
    response = await admin_client.post(
        "/api/red-leaf-town/admin/partners/maple_sprite/artworks/0",
        files={"file": FileStorage(stream=io.BytesIO(image.getvalue()), filename="portrait.png", content_type="image/png")},
        form={"x": "150", "y": "0", "w": "900", "h": "1600"},
        headers=admin_headers(admin_client),
    )
    body = await response.get_json()
    assert response.status_code == 200
    assert uploaded["path"].startswith("red-leaf-town/partners/maple_sprite/breakthrough-0-")
    assert uploaded["path"].endswith(".webp")
    assert uploaded["content_type"] == "image/webp"
    with Image.open(io.BytesIO(uploaded["data"])) as converted:
        assert converted.format == "WEBP"
        assert converted.size == (900, 1600)
    assert body["data"]["artworks"][0]["url"].startswith("https://cdn.example/")
    assert body["data"]["artworks"][0]["content_type"] == "image/webp"
    assert body["data"]["avatar_crops"] == [
        {"breakthrough": 0, "x": 0, "y": 350, "w": 900, "h": 900},
        {"breakthrough": 1, "x": 0, "y": 0, "w": 1, "h": 1},
        {"breakthrough": 2, "x": 0, "y": 0, "w": 1, "h": 1},
    ]


@runs
async def test_partner_artwork_center_crops_non_nine_sixteen_image(admin_client, monkeypatch):
    from src.libraries import cdn_client

    await admin_client.post(
        "/api/red-leaf-town/admin/partners",
        json=valid_partner_payload(),
        headers=admin_headers(admin_client),
    )
    uploaded = {}
    monkeypatch.setattr(
        cdn_client,
        "upload_bytes_at",
        lambda path, data, content_type: uploaded.update(path=path, data=data, content_type=content_type) or True,
    )
    monkeypatch.setattr(cdn_client, "cdn_url_at", lambda path: f"https://cdn.example/{path}")
    image = io.BytesIO()
    Image.new("RGB", (2000, 2000), "white").save(image, format="PNG")
    response = await admin_client.post(
        "/api/red-leaf-town/admin/partners/maple_sprite/artworks/0",
        files={"file": FileStorage(stream=io.BytesIO(image.getvalue()), filename="square.png", content_type="image/png")},
        headers=admin_headers(admin_client),
    )
    assert response.status_code == 200
    with Image.open(io.BytesIO(uploaded["data"])) as converted:
        assert converted.size == (1080, 1920)


@runs
async def test_partner_artwork_rejects_invalid_manual_crop_before_upload(admin_client, monkeypatch):
    from src.libraries import cdn_client

    await admin_client.post(
        "/api/red-leaf-town/admin/partners",
        json=valid_partner_payload(),
        headers=admin_headers(admin_client),
    )
    monkeypatch.setattr(cdn_client, "upload_bytes_at", lambda *args: pytest.fail("invalid crop must not upload"))
    image = io.BytesIO()
    Image.new("RGB", (1000, 1000), "white").save(image, format="PNG")
    response = await admin_client.post(
        "/api/red-leaf-town/admin/partners/maple_sprite/artworks/0",
        files={"file": FileStorage(stream=io.BytesIO(image.getvalue()), filename="square.png", content_type="image/png")},
        form={"x": "0", "y": "0", "w": "100", "h": "100"},
        headers=admin_headers(admin_client),
    )
    assert response.status_code == 400
    assert (await response.get_json())["code"] == "invalid_artwork_crop"


@runs
async def test_admin_can_delete_a_player(admin_client, service):
    player = service.repository.get_by_sub("route-sub")
    code = service.create_binding_code("route-sub")
    identity = QQIdentity(platform="onebot", bot_id="bot-1", subject="qq-1")
    service.bind_identity(code, identity)

    response = await admin_client.delete(
        f"/api/red-leaf-town/admin/players/{player.player_id}",
        headers=admin_headers(admin_client),
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert body["data"]["player"]["display_name"] == "小枫"
    assert service.repository.get(player.player_id) is None
    assert service.repository.get_by_sub("route-sub") is None
    assert service.repository.player_id_for_identity(identity) is None

    missing = await admin_client.delete(
        f"/api/red-leaf-town/admin/players/{player.player_id}",
        headers=admin_headers(admin_client),
    )
    assert missing.status_code == 404
    assert (await missing.get_json())["code"] == "player_not_found"


@runs
async def test_player_delete_requires_admin_session(admin_client, service):
    player = service.repository.get_by_sub("route-sub")
    response = await admin_client.delete(f"/api/red-leaf-town/admin/players/{player.player_id}")
    assert response.status_code == 403
    assert service.repository.get(player.player_id) is not None


@runs
async def test_deleted_player_starts_over_on_the_next_login(admin_client, service):
    player = service.repository.get_by_sub("route-sub")
    service.mark_story_seen("route-sub", "fein_farm_greeting")
    await admin_client.delete(
        f"/api/red-leaf-town/admin/players/{player.player_id}",
        headers=admin_headers(admin_client),
    )

    recreated = service.ensure_player("route-sub", "小枫")
    assert recreated.player_id != player.player_id
    assert recreated.seen_story_ids == []
    assert recreated.owned_partners == []


@runs
async def test_tribute_delivery_api(client, service):
    from red_leaf_town.domain.economy import add_item

    player = service.repository.get_by_sub("route-sub")
    service.repository.update(player.player_id, lambda state: setattr(state, "experience", 20))
    service.repository.update(player.player_id, lambda state: add_item(state, "carrot", 15))
    authenticate(client)

    response = await client.post(
        "/api/red-leaf-town/portals/first_gate/tributes/first_gate_carrot/deliver",
        json={"quantity": 15},
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert body["data"]["result"]["tribute_completed"] is True
    assert body["data"]["result"]["rewards"][0]["maple_flame"] == 150
    portal = next(
        entry for entry in body["data"]["state"]["portals"] if entry["portal_id"] == "first_gate"
    )
    assert portal["completed_tribute_count"] == 1
    assert portal["completed"] is False


@runs
async def test_tribute_delivery_api_rejects_a_locked_portal(client, service):
    authenticate(client)
    response = await client.post(
        "/api/red-leaf-town/portals/first_gate/tributes/first_gate_carrot/deliver",
        json={"quantity": 1},
    )
    body = await response.get_json()

    assert response.status_code == 409
    assert body["code"] == "portal_locked"


@runs
async def test_tribute_delivery_api_requires_login(client):
    response = await client.post(
        "/api/red-leaf-town/portals/first_gate/tributes/first_gate_carrot/deliver",
        json={"quantity": 1},
    )
    assert response.status_code == 401


@runs
async def test_commission_board_api_lists_the_public_pool(client, service):
    authenticate(client)
    helper = service.ensure_player("helper-sub", "枫二")
    commission = service.snapshot_by_sub("helper-sub")["commissions"]["commission"]
    service.forward_commission("helper-sub")

    response = await client.get("/api/red-leaf-town/commissions/board")
    body = await response.get_json()

    assert response.status_code == 200
    assert body["code"] == 0
    assert [entry["commission_id"] for entry in body["data"]["entries"]] == [commission["commission_id"]]
    assert body["data"]["remaining_takes"] == service.content.commissions.daily_take_limit
    assert helper.player_id not in str(body["data"]["entries"])


@runs
async def test_commission_submit_api_pays_the_reward(client, service):
    authenticate(client)
    player = service.repository.get_by_sub("route-sub")
    commission = service.snapshot_by_sub("route-sub")["commissions"]["commission"]
    service.repository.update(
        player.player_id,
        lambda state: add_item(state, commission["item_id"], commission["quantity"]),
    )

    response = await client.post("/api/red-leaf-town/commissions/submit")
    body = await response.get_json()

    assert response.status_code == 200
    assert body["data"]["result"]["maple_flame"] == commission["reward_maple_flame"]
    assert body["data"]["state"]["commissions"]["commission"]["status"] == "completed"


@runs
async def test_commission_forward_and_withdraw_api_round_trip(client, service):
    authenticate(client)

    forwarded = await client.post("/api/red-leaf-town/commissions/forward")
    forwarded_body = await forwarded.get_json()
    assert forwarded.status_code == 200
    assert forwarded_body["data"]["state"]["commissions"]["commission"]["status"] == "forwarded"

    withdrawn = await client.post("/api/red-leaf-town/commissions/withdraw")
    withdrawn_body = await withdrawn.get_json()
    assert withdrawn.status_code == 200
    assert withdrawn_body["data"]["state"]["commissions"]["commission"]["status"] == "open"


@runs
async def test_commission_take_api_refuses_an_unknown_commission(client, service):
    authenticate(client)
    response = await client.post("/api/red-leaf-town/commissions/deadbeefdeadbeef/take")
    body = await response.get_json()

    assert response.status_code == 404
    assert body["code"] == "commission_not_found"


@runs
async def test_commission_apis_require_login(client):
    for path in (
        "/api/red-leaf-town/commissions/submit",
        "/api/red-leaf-town/commissions/forward",
        "/api/red-leaf-town/commissions/withdraw",
    ):
        assert (await client.post(path)).status_code == 401
    assert (await client.get("/api/red-leaf-town/commissions/board")).status_code == 401


@runs
async def test_mail_apis_read_and_claim_a_global_letter(client, service):
    service.admin_send_mail({
        "scope": "global",
        "title": "红叶镇邮局开张",
        "sender": "镇长",
        "body": "山下的信件从今天起直接送到你手上。",
        "attachments": {"coins": 500},
    })
    authenticate(client)

    listed = await client.get("/api/red-leaf-town/mail")
    body = (await listed.get_json())["data"]
    assert listed.status_code == 200
    assert body["unread"] == 1 and body["unclaimed"] == 1
    entry = body["entries"][0]
    assert entry["sender"] == "镇长" and entry["claimable"] is True

    read = await client.post(f"/api/red-leaf-town/mail/{entry['mail_id']}/read")
    assert (await read.get_json())["data"]["state"]["mail"]["unread"] == 0

    claimed = await client.post(f"/api/red-leaf-town/mail/{entry['mail_id']}/claim")
    claimed_body = (await claimed.get_json())["data"]
    assert claimed.status_code == 200
    assert claimed_body["result"]["granted"]["coins"] == 500
    assert claimed_body["state"]["mail"]["unclaimed"] == 0

    again = await client.post(f"/api/red-leaf-town/mail/{entry['mail_id']}/claim")
    assert again.status_code == 409
    assert (await again.get_json())["code"] == "mail_already_claimed"


@runs
async def test_mail_apis_require_login(client):
    assert (await client.get("/api/red-leaf-town/mail")).status_code == 401
    assert (await client.post("/api/red-leaf-town/mail/deadbeefcafe/claim")).status_code == 401


@runs
async def test_admin_mail_send_list_and_withdraw(admin_client, service):
    player = service.repository.get_by_sub("route-sub")
    sent = await admin_client.post(
        "/api/red-leaf-town/admin/mail",
        json={
            "scope": "player",
            "recipient_id": player.player_id,
            "title": "给小枫的信",
            "sender": "白璃",
            "body": "记得来取你的东西。",
            "attachments": {"items": [{"item_id": "carrot_seed", "quantity": 2}]},
        },
        headers=admin_headers(admin_client),
    )
    mail = (await sent.get_json())["data"]["mail"]
    assert sent.status_code == 200
    assert mail["recipient_name"] == "小枫"
    assert mail["attachments"]["items"][0]["name"]

    listed = await admin_client.get(
        f"/api/red-leaf-town/admin/mail?scope=player&player_id={player.player_id}",
        headers=admin_headers(admin_client),
    )
    assert [entry["mail_id"] for entry in (await listed.get_json())["data"]["entries"]] == [mail["mail_id"]]

    withdrawn = await admin_client.delete(
        f"/api/red-leaf-town/admin/mail/{mail['mail_id']}?recipient_id={player.player_id}",
        headers=admin_headers(admin_client),
    )
    assert withdrawn.status_code == 200
    assert service.mailbox_repository.list_for_player(player.player_id) == []


@runs
async def test_admin_mail_requires_admin_session(admin_client):
    assert (await admin_client.get("/api/red-leaf-town/admin/mail")).status_code == 403
    assert (await admin_client.post("/api/red-leaf-town/admin/mail", json={})).status_code == 403


@runs
async def test_fishing_and_feed_slot_api(client, service):
    authenticate(client)
    player = service.repository.get_by_sub("route-sub")
    service.admin_grant_resources(player.player_id, 0, 2000, 0)
    service.repository.update(player.player_id, lambda state: setattr(state, "stamina", 30))

    cast = await client.post(
        "/api/red-leaf-town/fishing/spots/town_creek/cast",
        json={"request_id": "route-cast-1"},
    )
    body = (await cast.get_json())["data"]
    assert cast.status_code == 200
    assert body["result"]["experience"] == 18
    assert body["state"]["aquatic"]["combo"]["layers"] == 1

    repeated = await client.post(
        "/api/red-leaf-town/fishing/spots/town_creek/cast",
        json={"request_id": "route-cast-1"},
    )
    assert (await repeated.get_json())["data"]["result"]["duplicate"] is True

    service.repository.update(player.player_id, lambda state: add_item(state, "meadow_hay", 6, 0))
    deposited = await client.post(
        "/api/red-leaf-town/feed-slot/deposit",
        json={"item_id": "meadow_hay", "quality": 0, "count": 6},
    )
    feed_slot = (await deposited.get_json())["data"]["state"]["aquatic"]["feed_slot"]
    assert deposited.status_code == 200
    assert feed_slot["units"] == 24
    assert feed_slot["quality_score"] == 5

    dumped = await client.post("/api/red-leaf-town/feed-slot/dump")
    assert (await dumped.get_json())["data"]["state"]["aquatic"]["feed_slot"]["units"] == 0


@runs
async def test_feed_deposit_completes_a_legacy_stalled_livestock_cycle(client, service):
    authenticate(client)
    player = service.repository.get_by_sub("route-sub")

    def prepare(state):
        state.experience = service.content.level_definition(12).total_xp
        state.coins = 30_000
        add_item(state, "maple_wood", 30, 1)
        add_item(state, "meadow_hay", 2, 0)

    service.repository.update(player.player_id, prepare)
    service.build_livestock_facility("route-sub", "coop_1")
    service.buy_animal("route-sub", "coop_1", "chicken")

    def stall(state):
        state.animals[0].stage = "adult"
        state.livestock_facilities[0].stalled = True
        state.livestock_facilities[0].settle_remainder = 123
        state.feed_slot.units = 0

    service.repository.update(player.player_id, stall)
    response = await client.post(
        "/api/red-leaf-town/feed-slot/deposit",
        json={"item_id": "meadow_hay", "quality": 0, "count": 2},
    )
    assert response.status_code == 200
    state = (await response.get_json())["data"]["state"]
    coop = state["livestock"]["facilities"][0]
    assert coop["stalled"] is False
    assert coop["animals"][0]["pending_total"] == 1
    assert state["aquatic"]["feed_slot"]["units"] == 0
    refreshed = service.snapshot_by_sub("route-sub")["livestock"]["facilities"][0]
    assert refreshed["animals"][0]["pending_total"] == 1
    assert refreshed["stalled"] is False


@runs
async def test_pond_api(client, service):
    authenticate(client)
    player = service.repository.get_by_sub("route-sub")
    service.admin_grant_resources(player.player_id, 3000, 2000, 0)
    service.repository.update(player.player_id, lambda state: add_item(state, "fish_fry", 6, 0))

    built = await client.post("/api/red-leaf-town/ponds/pond_1/build")
    assert built.status_code == 200
    assert (await built.get_json())["data"]["result"]["build_cost"] == 3000

    stocked = await client.post(
        "/api/red-leaf-town/ponds/pond_1/stock",
        json={"species_id": "crucian", "quantity": 6},
    )
    body = (await stocked.get_json())["data"]
    assert stocked.status_code == 200
    assert body["state"]["aquatic"]["ponds"][0]["fry_total"] == 6
    assert body["state"]["aquatic"]["ponds"][0]["stock"] == 0

    empty = await client.post("/api/red-leaf-town/ponds/pond_1/harvest", json={"quantity": 1})
    assert empty.status_code == 400
    assert (await empty.get_json())["code"] == "pond_empty"

    def grow_up(state):
        pond = state.ponds[0]
        pond.stock = sum(batch.count for batch in pond.fry)
        pond.fry = []

    service.repository.update(player.player_id, grow_up)

    harvested = await client.post("/api/red-leaf-town/ponds/pond_1/harvest", json={"quantity": 2})
    harvest_body = (await harvested.get_json())["data"]
    assert harvested.status_code == 200
    assert harvest_body["result"]["stock"] == 4
    assert sum(drop["quantity"] for drop in harvest_body["result"]["drops"]) == 2

    too_many = await client.post("/api/red-leaf-town/ponds/pond_1/harvest", json={"quantity": 99})
    assert too_many.status_code == 400
    assert (await too_many.get_json())["code"] == "invalid_quantity"


@runs
async def test_redemption_code_admin_generates_and_lists(admin_client, service):
    forbidden = await admin_client.post("/api/red-leaf-town/admin/redemption-codes", json={"count": 3})
    assert forbidden.status_code == 403

    created = await admin_client.post(
        "/api/red-leaf-town/admin/redemption-codes",
        json={"count": 3, "batch": "首发"},
        headers=admin_headers(admin_client),
    )
    codes = (await created.get_json())["data"]["codes"]
    assert len(codes) == 3

    listed = await admin_client.get("/api/red-leaf-town/admin/redemption-codes", headers=admin_headers(admin_client))
    records = (await listed.get_json())["data"]
    assert {record["code"] for record in records} == set(codes)
    assert all(record["batch"] == "首发" for record in records)


@runs
async def test_monthly_card_redeem_and_claim_over_http(client, service):
    authenticate(client)
    code = service.generate_redemption_codes(1, batch="http")[0]

    redeemed = await client.post("/api/red-leaf-town/monthly-card/redeem", json={"code": code})
    assert redeemed.status_code == 200
    card = (await redeemed.get_json())["data"]["state"]["monthly_card"]
    assert card["days_left"] == 30 and card["claimable"] is True

    claimed = await client.post("/api/red-leaf-town/monthly-card/claim")
    payload = (await claimed.get_json())["data"]
    assert payload["result"]["maple_flame"] == 50
    assert payload["state"]["stamina_supply"]["potion_owned"] == 2

    again = await client.post("/api/red-leaf-town/monthly-card/claim")
    assert again.status_code == 409


@runs
async def test_redeeming_a_bad_code_over_http(client, service):
    authenticate(client)
    response = await client.post("/api/red-leaf-town/monthly-card/redeem", json={"code": "NOPE"})
    assert response.status_code == 404
    assert (await response.get_json())["code"] == "invalid_code"


@runs
async def test_stamina_supply_endpoints(client, service):
    authenticate(client)
    player = service.repository.get_by_sub("route-sub")
    service.repository.update(player.player_id, lambda entry: setattr(entry, "maple_flame", 300))
    cap = service.snapshot_by_sub("route-sub")["player"]["stamina_cap"]

    bought = await client.post("/api/red-leaf-town/stamina/purchase")
    payload = (await bought.get_json())["data"]
    assert payload["result"]["maple_flame_spent"] == 50
    assert payload["state"]["player"]["stamina"] == cap + 40

    dry = await client.post("/api/red-leaf-town/stamina/potion")
    assert dry.status_code == 400

    service.repository.update(player.player_id, lambda entry: add_item(entry, "feien_tonic", 1))
    used = await client.post("/api/red-leaf-town/stamina/potion")
    assert (await used.get_json())["data"]["state"]["player"]["stamina"] == cap + 80


@runs
async def test_sailing_api_requires_login(client):
    for endpoint in ('build', 'start', 'collect', 'cancel', 'upgrade'):
        response = await client.post(f'/api/red-leaf-town/sailing/{endpoint}', json={})
        assert response.status_code == 401


@runs
async def test_sailing_api_beta_gate_and_bad_payloads(client, service, monkeypatch):
    authenticate(client)
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS', 'nobody')
    response = await client.post('/api/red-leaf-town/sailing/start', json={
        'route_id': 'reed_bay', 'partner_ids': ['missing'], 'request_id': 'beta-test',
    })
    assert response.status_code == 403
    assert (await response.get_json())['code'] == 'content_locked'
    for endpoint in ('start', 'collect', 'cancel', 'upgrade'):
        response = await client.post(f'/api/red-leaf-town/sailing/{endpoint}', json=['invalid'])
        assert response.status_code == 400
    response = await client.post('/api/red-leaf-town/sailing/start', json={'partner_ids': 'wrong'})
    assert response.status_code == 400


@runs
async def test_sailing_api_full_trial_and_upgrade(client, service, monkeypatch):
    authenticate(client)
    player = service.repository.get_by_sub('route-sub')
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS', player.player_id)
    partner = service.partner_catalog_loader().partners[0]
    service.admin_grant_partner(player.player_id, partner.id)
    def prepare(p):
        p.experience = service.content.level_definition(16).total_xp
        p.coins = 20_000
        p.stamina = 50
        add_item(p, 'maple_plank', 10, 1)
        add_item(p, 'composite_plank', 20, 1)
        add_item(p, 'voyage_sail', 1, 1)
    service.repository.update(player.player_id, prepare)
    response = await client.post('/api/red-leaf-town/sailing/build', json={})
    assert response.status_code == 200
    assert (await response.get_json())['data']['state']['sailing']['ship_built']
    response = await client.post('/api/red-leaf-town/sailing/build', json={})
    assert (await response.get_json())['data']['result']['duplicate']
    assert service.repository.get(player.player_id).coins == 10_000
    response = await client.post('/api/red-leaf-town/sailing/upgrade', json={'kind': 'cargo', 'expected_level': 0})
    assert response.status_code == 200
    assert (await response.get_json())['data']['result']['level'] == 1
    response = await client.post('/api/red-leaf-town/sailing/start', json={
        'route_id': 'reed_bay', 'partner_ids': [partner.id], 'request_id': 'web-trial',
    })
    assert response.status_code == 200
    run = (await response.get_json())['data']['state']['sailing']['active_run']
    assert run['trial'] and not run['drops']
    response = await client.post('/api/red-leaf-town/sailing/collect', json={'run_id': run['run_id']})
    assert response.status_code == 409
    service.clock = lambda: run['ready_at']
    response = await client.post('/api/red-leaf-town/sailing/collect', json={'run_id': run['run_id']})
    assert response.status_code == 200
    data = (await response.get_json())['data']
    assert data['result']['drops']
    assert data['state']['sailing']['completed_voyages'] == 1
    assert data['state']['sailing']['active_run'] is None
    response = await client.post('/api/red-leaf-town/sailing/collect', json={'run_id': run['run_id']})
    assert (await response.get_json())['data']['result']['duplicate']


@runs
async def test_crafting_queue_api_reservation_partial_collection_and_cancel(client, service):
    player = service.repository.get_by_sub("route-sub")

    def prepare(state):
        state.experience = 60
        state.inventory["maple_wood"] = {1: 6}
        state.task_items["harvest_knot"] = 2

    service.repository.update(player.player_id, prepare)
    authenticate(client)
    response = await client.post(
        "/api/red-leaf-town/crafting/stations/town_workbench/start",
        json={"recipe_id": "saw_maple_plank", "quantity": 3, "task_item_id": "harvest_knot"},
    )
    assert response.status_code == 200
    data = (await response.get_json())["data"]
    station = data["state"]["crafting_stations"][0]
    assert station["queued_count"] == 2
    assert data["result"]["task_items_reserved"] == 2
    service.clock = lambda: station["task_snapshot"]["ready_at"]
    response = await client.post("/api/red-leaf-town/crafting/stations/town_workbench/collect")
    assert response.status_code == 200
    data = (await response.get_json())["data"]
    assert data["result"]["quantity"] == 2
    assert data["state"]["crafting_stations"][0]["queued_count"] == 1
    cancelled = service.cancel_task("route-sub", "crafting", "town_workbench")
    assert cancelled["result"]["refunded_stamina"] == 2
    assert cancelled["result"]["refunded_task_items"] == {}
    assert cancelled["state"]["crafting_stations"][0]["empty"]


@runs
async def test_beta_livestock_upgrade_and_duck_incubation_api(client, service, monkeypatch):
    authenticate(client)
    player = service.repository.get_by_sub('route-sub')
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS', player.player_id)
    def prepare(p):
        p.experience = service.content.level_definition(14).total_xp
        p.coins = 30_000
        p.feed_slot.units = 100
        for item, quantity in [('maple_wood',30),('maple_plank',30),('red_copper_ore',20),('rope',6),('reed_mat',4),('duck_egg',1)]:
            add_item(p, item, quantity, 1)
    service.repository.update(player.player_id, prepare)
    assert (await client.post('/api/red-leaf-town/livestock/facilities/coop_1/build', json={})).status_code == 200
    path = '/api/red-leaf-town/livestock/facilities/coop_1/upgrade'
    assert (await client.post(path, json={'target_tier': '2'})).status_code == 400
    response = await client.post(path, json={'target_tier': 2})
    assert response.status_code == 200
    assert (await response.get_json())['data']['result']['tier'] == 2
    response = await client.post(path, json={'target_tier': 2})
    assert (await response.get_json())['data']['result']['duplicate']
    response = await client.post('/api/red-leaf-town/livestock/facilities/coop_1/incubate', json={'species_id':'duck','quality':1})
    assert response.status_code == 200
    assert (await response.get_json())['data']['result']['animal']['species_id'] == 'duck'


@runs
async def test_partner_artwork_selection_requires_login_and_returns_cdn_urls(client, service, monkeypatch):
    from src.libraries import cdn_client

    monkeypatch.setattr(cdn_client, "cdn_url_at", lambda path: f"https://cdn.example/{path}")
    path = "/api/red-leaf-town/partners/bai_li/artwork"
    assert (await client.post(path, json={"artwork_stage": 0})).status_code == 401
    authenticate(client)
    player = service.repository.get_by_sub("route-sub")
    service.admin_grant_partner(player.player_id, "bai_li")
    response = await client.post(path, json={"artwork_stage": 0})
    assert response.status_code == 200
    data = (await response.get_json())["data"]
    partner = next(p for p in data["state"]["partners"] if p["partner_id"] == "bai_li")
    assert partner["artwork_stage"] == partner["avatar_crop"]["breakthrough"] == 0
    assert partner["artwork"]["url"]
    assert partner["available_artworks"][0]["url"]
    assert (await client.post(path, json={"artwork_stage": 1})).status_code == 409
    assert (await client.post(path, json={"artwork_stage": "bad"})).status_code == 409


@runs
async def test_logout_clears_session_and_returns_oauth_logout(client):
    from http.cookies import SimpleCookie
    authenticate(client)
    response = await client.post('/api/red-leaf-town/logout?next=https://untrusted.example/')
    assert response.status_code == 200
    assert (await response.get_json())['data']['logout_url'] == 'https://auth.diving-fish.com/logout'
    cookie = SimpleCookie()
    for value in response.headers.getlist('Set-Cookie'):
        cookie.load(value)
    assert cookie[COOKIE_NAME].value == ''
    assert cookie[COOKIE_NAME]['max-age'] == '0'
    assert (await client.get('/api/red-leaf-town/state')).status_code == 401


@runs
async def test_sailing_cancel_api_refunds_and_rejects_stale_or_arrived_runs(client, service):
    authenticate(client)
    player = service.repository.get_by_sub('route-sub')
    partner = service.partner_catalog_loader().partners[0]
    service.admin_grant_partner(player.player_id, partner.id)
    def prepare(p):
        p.experience = service.content.level_definition(16).total_xp
        p.coins = 20000
        p.stamina = 40
        p.sailing.ship_built = True
        p.inventory['pickled_carrot'] = {1: 1, 3: 2}
    service.repository.update(player.player_id, prepare)
    path = '/api/red-leaf-town/sailing/cancel'
    for payload in ({}, {'run_id': ''}, {'run_id': ' '}, {'run_id': 123}, []):
        assert (await client.post(path, json=payload)).status_code == 400
    assert (await client.post(path, json={'run_id': 'missing'})).status_code == 404
    before = service.repository.get(player.player_id)
    response = await client.post('/api/red-leaf-town/sailing/start', json={
        'route_id': 'reed_bay', 'partner_ids': [partner.id], 'supply_id': 'ration', 'request_id': 'cancel-trial',
    })
    run = (await response.get_json())['data']['state']['sailing']['active_run']
    authenticate(client, 'another-sub')
    service.ensure_player('another-sub', '另一位居民')
    service.repository.update(service.repository.get_by_sub('another-sub').player_id, prepare)
    assert (await client.post(path, json={'run_id': run['run_id']})).status_code == 404
    authenticate(client)
    response = await client.post(path, json={'run_id': run['run_id']})
    assert response.status_code == 200
    data = (await response.get_json())['data']
    assert data['state']['sailing']['active_run'] is None
    after = service.repository.get(player.player_id)
    assert after.coins == before.coins and after.inventory == before.inventory
    assert after.stamina == before.stamina
    assert (await client.post(path, json={'run_id': run['run_id']})).status_code == 404
    assert service.repository.get(player.player_id).coins == before.coins
    response = await client.post('/api/red-leaf-town/sailing/start', json={
        'route_id': 'reed_bay', 'partner_ids': [partner.id], 'request_id': 'arrived-trial',
    })
    arrived = (await response.get_json())['data']['state']['sailing']['active_run']
    service.clock = lambda: arrived['ready_at']
    assert (await client.post(path, json={'run_id': arrived['run_id']})).status_code == 409
    assert (await client.post('/api/red-leaf-town/sailing/collect', json={'run_id': arrived['run_id']})).status_code == 200
