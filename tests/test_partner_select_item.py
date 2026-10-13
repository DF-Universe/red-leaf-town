from __future__ import annotations

import asyncio
import functools
import random

import pytest
from quart import Quart

from game_host.jwt import AUD_RED_LEAF_TOWN, subject_encode
from red_leaf_town.application import GameError, GameService
from red_leaf_town.application.service import PARTNER_SELECT_IDS
from red_leaf_town.content import load_content
from red_leaf_town.domain.economy import add_item
from red_leaf_town.infrastructure import InMemoryCommissionBoard, InMemoryMailbox, InMemoryPlayerRepository
from red_leaf_town.partner_content import load_partner_catalog
from red_leaf_town.runtime import set_service
from red_leaf_town.web.routes import COOKIE_NAME, create_blueprint

INVITATION_ITEM_ID = "partner_invitation"


class Clock:
    def __init__(self):
        self.now = 1_700_000_000

    def __call__(self):
        return self.now


@pytest.fixture
def game():
    content = load_content().model_copy(deep=True)
    repository = InMemoryPlayerRepository(content)
    clock = Clock()
    service = GameService(content, repository, clock=clock, rng=random.Random(24))
    player = service.ensure_player("select-sub", "自选居民")
    return service, repository, player


def give_invitation(repository, player):
    repository.update(player.player_id, lambda state: add_item(state, INVITATION_ITEM_ID, 1))


def owns(repository, player_id, partner_id: str) -> bool:
    saved = repository.get(player_id)
    return any(owned.partner_id == partner_id for owned in saved.owned_partners)


def marks(repository, player_id) -> int:
    return repository.get(player_id).companion_marks


def test_candidates_are_unowned_partners_from_the_hardcoded_roster(game):
    service, repository, player = game
    catalog = load_partner_catalog()
    illustrated = {partner.id for partner in catalog.partners if partner.artwork_for(0) is not None}

    payload = service.partner_select_candidates("select-sub")
    candidates = {entry["id"] for entry in payload["candidates"]}

    assert payload["eligible_total"] == len(PARTNER_SELECT_IDS)
    # 名单刻意写死，新伙伴要人工确认后才加入；这里只要求名单里的人都画好了立绘。
    assert PARTNER_SELECT_IDS <= illustrated
    assert candidates == PARTNER_SELECT_IDS
    for entry in payload["candidates"]:
        assert entry["artwork"]["asset_key"]
        assert entry["avatar_crop"] is not None
        assert entry["companion_marks_granted"] == {3: 2000, 4: 1500, 5: 0}[entry["rarity"]]

    repository.update(player.player_id, lambda state: add_item(state, INVITATION_ITEM_ID, 1))
    result = service.use_partner_select_item("select-sub", INVITATION_ITEM_ID, "manlong")
    assert result["result"]["partner_id"] == "manlong"
    remaining = {entry["id"] for entry in service.partner_select_candidates("select-sub")["candidates"]}
    assert "manlong" not in remaining
    assert "manlong_2" in remaining
    assert "xiao_xingyun" not in remaining
    assert "ze_feiyang" not in remaining


def test_use_consumes_item_and_grants_mark_compensation_by_rarity(game):
    service, repository, player = game
    give_invitation(repository, player)
    before = marks(repository, player.player_id)

    result = service.use_partner_select_item("select-sub", INVITATION_ITEM_ID, "manlong")
    assert result["result"] == {
        "partner_id": "manlong",
        "name": "慢龙",
        "stars": 3,
        "companion_marks_granted": 2000,
    }
    assert owns(repository, player.player_id, "manlong")
    assert marks(repository, player.player_id) == before + 2000
    saved = repository.get(player.player_id)
    assert INVITATION_ITEM_ID not in saved.inventory
    snapshot = result["state"]
    assert any(entry["partner_id"] == "manlong" for entry in snapshot["partners"])
    assert snapshot["player"]["companion_marks"] == before + 2000

    repository.update(player.player_id, lambda state: add_item(state, INVITATION_ITEM_ID, 1))
    result = service.use_partner_select_item("select-sub", INVITATION_ITEM_ID, "banniang")
    assert result["result"]["companion_marks_granted"] == 1500

    repository.update(player.player_id, lambda state: add_item(state, INVITATION_ITEM_ID, 1))
    result = service.use_partner_select_item("select-sub", INVITATION_ITEM_ID, "guqi")
    assert result["result"]["companion_marks_granted"] == 0


def test_use_rejects_unknown_partners_items_and_duplicates(game):
    service, repository, player = game
    give_invitation(repository, player)

    with pytest.raises(GameError, match="邀约名单"):
        service.use_partner_select_item("select-sub", INVITATION_ITEM_ID, "xiao_xingyun")

    with pytest.raises(GameError, match="不能使用"):
        service.use_partner_select_item("select-sub", "maple_wood", "manlong")

    service.use_partner_select_item("select-sub", INVITATION_ITEM_ID, "manlong")
    repository.update(player.player_id, lambda state: add_item(state, INVITATION_ITEM_ID, 1))
    with pytest.raises(GameError, match="已经"):
        service.use_partner_select_item("select-sub", INVITATION_ITEM_ID, "manlong")

    assert owns(repository, player.player_id, "manlong")


def test_use_requires_the_item_in_inventory(game):
    service, repository, player = game
    with pytest.raises(GameError, match="数量不足"):
        service.use_partner_select_item("select-sub", INVITATION_ITEM_ID, "manlong")


def runs(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


@pytest.fixture
def route_service():
    content = load_content()
    game = GameService(
        content,
        InMemoryPlayerRepository(content),
        commission_board=InMemoryCommissionBoard(),
        mailbox=InMemoryMailbox(),
        clock=lambda: 1_700_000_000,
    )
    game.ensure_player("route-sub", "小枫")
    set_service(game)
    yield game
    set_service(None)


@pytest.fixture
def client(route_service):
    app = Quart(__name__)
    app.register_blueprint(create_blueprint())
    return app.test_client()


def authenticate(client, sub="route-sub"):
    client.set_cookie("localhost", COOKIE_NAME, subject_encode(sub, AUD_RED_LEAF_TOWN))


@runs
async def test_partner_select_routes(client, route_service):
    authenticate(client)
    player = route_service.repository.get_by_sub("route-sub")
    route_service.repository.update(player.player_id, lambda state: add_item(state, INVITATION_ITEM_ID, 1))

    listed = await client.get("/api/red-leaf-town/partner-select/candidates")
    assert listed.status_code == 200
    body = (await listed.get_json())["data"]
    assert body["eligible_total"] == len(PARTNER_SELECT_IDS)
    assert any(entry["id"] == "manlong" and "url" in entry["artwork"] for entry in body["candidates"])

    used = await client.post(
        f"/api/red-leaf-town/inventory/{INVITATION_ITEM_ID}/use",
        json={"partner_id": "manlong"},
    )
    assert used.status_code == 200
    data = (await used.get_json())["data"]
    assert data["result"]["partner_id"] == "manlong"
    assert data["result"]["companion_marks_granted"] == 2000
    assert any(entry["partner_id"] == "manlong" for entry in data["state"]["partners"])

    rejected = await client.post(
        f"/api/red-leaf-town/inventory/{INVITATION_ITEM_ID}/use",
        json={"partner_id": "xiao_xingyun"},
    )
    assert rejected.status_code == 400
