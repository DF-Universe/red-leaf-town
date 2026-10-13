from __future__ import annotations

import asyncio
import functools
import io
import json
import re

import pytest
from quart import Quart
from PIL import Image
from werkzeug.datastructures import FileStorage

from game_host.jwt import AUD_RED_LEAF_TOWN, subject_encode
from red_leaf_town.application import GameService
from red_leaf_town.content import load_content
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.runtime import get_service, set_service
from red_leaf_town.partner_content import load_partner_catalog
from red_leaf_town.story_assets import load_story_asset_catalog
from red_leaf_town.story_content import load_story_catalog
from red_leaf_town.story_uploads import load_story_upload_index
from red_leaf_town.web.routes import COOKIE_NAME, create_blueprint


def runs(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


WELCOME_SCRIPT = {
    "id": "welcome",
    "title": "第一次进农场",
    "trigger": {"hook": "cue", "params": {"cue": "view:farm"}},
    "steps": [
        {"type": "dialogue", "speaker": "枫糖", "text": "这块地空了一整个冬天啦。"},
    ],
}


UPLOAD_TEST_QUOTA = 4096


@pytest.fixture
def story_paths(tmp_path):
    asset_path = tmp_path / "story_assets.json"
    asset_path.write_text(json.dumps({"schema_version": 1, "assets": []}), encoding="utf-8")
    script_dir = tmp_path / "story"
    script_dir.mkdir()
    (script_dir / "welcome.json").write_text(json.dumps(WELCOME_SCRIPT), encoding="utf-8")
    partner_path = tmp_path / "partners.json"
    upload_path = tmp_path / "story_uploads.json"
    load_story_asset_catalog.cache_clear()
    load_story_catalog.cache_clear()
    load_story_upload_index.cache_clear()
    yield asset_path, script_dir, partner_path, upload_path
    load_story_asset_catalog.cache_clear()
    load_story_catalog.cache_clear()
    load_story_upload_index.cache_clear()


@pytest.fixture
def client(story_paths, monkeypatch, tmp_path):
    asset_path, script_dir, partner_path, upload_path = story_paths
    config_path = tmp_path / "config.json"
    config_path.write_text('{"admin_subs": ["test-admin-sub"]}', encoding="utf-8")
    monkeypatch.setattr("red_leaf_town.config.CONFIG_PATH", config_path)
    content = load_content()
    service = GameService(
        content,
        InMemoryPlayerRepository(content),
        clock=lambda: 1_700_000_000,
        partner_catalog_loader=lambda: load_partner_catalog(partner_path),
        story_catalog_loader=lambda: load_story_catalog(script_dir, asset_path, partner_path),
        story_asset_loader=lambda: load_story_asset_catalog(asset_path),
    )
    service.ensure_player("story-sub", "小枫")
    set_service(service)
    app = Quart(__name__)
    app.register_blueprint(create_blueprint(
        partner_catalog_path=partner_path,
        story_asset_path=asset_path,
        story_script_dir=script_dir,
        story_upload_path=upload_path,
        upload_quota_bytes=UPLOAD_TEST_QUOTA,
    ))
    yield app.test_client()
    set_service(None)


def authenticate(client, sub="story-sub"):
    client.set_cookie("localhost", COOKIE_NAME, subject_encode(sub, AUD_RED_LEAF_TOWN))


def admin_headers(client):
    client.set_cookie("localhost", COOKIE_NAME, subject_encode("test-admin-sub", AUD_RED_LEAF_TOWN))
    return {"X-Requested-With": "XMLHttpRequest"}


@runs
async def test_cue_requires_login(client):
    response = await client.post("/api/red-leaf-town/story/cue", json={"cue": "view:farm"})
    assert response.status_code == 401


@runs
async def test_cue_returns_story_then_stops_after_seen(client):
    authenticate(client)
    response = await client.post("/api/red-leaf-town/story/cue", json={"cue": "view:farm"})
    body = await response.get_json()
    assert response.status_code == 200
    assert [entry["id"] for entry in body["data"]["stories"]] == ["welcome"]
    assert body["data"]["stories"][0]["mode"] == "inline"

    seen = await client.post("/api/red-leaf-town/story/welcome/seen")
    assert seen.status_code == 200
    assert (await seen.get_json())["data"]["result"]["story_id"] == "welcome"

    again = await client.post("/api/red-leaf-town/story/cue", json={"cue": "view:farm"})
    assert (await again.get_json())["data"]["stories"] == []


@runs
async def test_cue_ignores_unrelated_signal(client):
    authenticate(client)
    response = await client.post("/api/red-leaf-town/story/cue", json={"cue": "view:mining"})
    assert (await response.get_json())["data"]["stories"] == []


@runs
async def test_malformed_cue_is_rejected(client):
    authenticate(client)
    response = await client.post("/api/red-leaf-town/story/cue", json={"cue": "VIEW FARM"})
    assert response.status_code == 400
    assert (await response.get_json())["code"] == "invalid_story_cue"


@runs
async def test_unknown_story_seen_returns_404(client):
    authenticate(client)
    response = await client.post("/api/red-leaf-town/story/nope/seen")
    assert response.status_code == 404


@runs
async def test_admin_story_payload_requires_admin_session(client):
    response = await client.get("/api/red-leaf-town/admin/story")
    assert response.status_code == 403


@runs
async def test_admin_story_payload_lists_scripts(client):
    response = await client.get("/api/red-leaf-town/admin/story", headers=admin_headers(client))
    body = await response.get_json()
    assert response.status_code == 200
    assert [entry["id"] for entry in body["data"]["scripts"]] == ["welcome"]
    assert body["data"]["assets"] == []
    assert [entry["id"] for entry in body["data"]["options"]["kinds"]] == ["background", "portrait"]
    assert "blends" not in body["data"]["options"]
    assert "cue" in body["data"]["options"]["trigger_hooks"]


def png_bytes(size, color="#a04030", mode="RGB"):
    image = io.BytesIO()
    Image.new(mode, size, color).save(image, format="PNG")
    return image.getvalue()


@runs
async def test_story_asset_upload_transcodes_and_registers(client, monkeypatch):
    from game_host import cdn_client

    uploaded = {}
    monkeypatch.setattr(cdn_client, "upload_bytes_at", lambda path, data, content_type: uploaded.update(
        path=path, data=data, content_type=content_type) or True)
    monkeypatch.setattr(cdn_client, "cdn_url_at", lambda path: f"https://cdn.example/{path}")

    response = await client.post(
        "/api/red-leaf-town/admin/story/assets",
        files={"file": FileStorage(stream=io.BytesIO(png_bytes((3200, 1800))), filename="gate.png", content_type="image/png")},
        form={"id": "autumn_gate", "name": "镇口", "kind": "background"},
        headers=admin_headers(client),
    )
    body = await response.get_json()
    assert response.status_code == 201
    assert uploaded["path"].startswith("red-leaf-town/story/background/autumn_gate-")
    assert uploaded["content_type"] == "image/webp"
    with Image.open(io.BytesIO(uploaded["data"])) as converted:
        assert converted.format == "WEBP"
        assert converted.size == (2560, 1440)
    assert body["data"]["width"] == 2560
    assert body["data"]["url"].startswith("https://cdn.example/")

    listed = await client.get("/api/red-leaf-town/admin/story", headers=admin_headers(client))
    assert [entry["id"] for entry in (await listed.get_json())["data"]["assets"]] == ["autumn_gate"]


@runs
async def test_portrait_upload_keeps_alpha(client, monkeypatch):
    from game_host import cdn_client

    uploaded = {}
    monkeypatch.setattr(cdn_client, "upload_bytes_at", lambda path, data, content_type: uploaded.update(data=data) or True)
    monkeypatch.setattr(cdn_client, "cdn_url_at", lambda path: f"https://cdn.example/{path}")

    response = await client.post(
        "/api/red-leaf-town/admin/story/assets",
        files={"file": FileStorage(
            stream=io.BytesIO(png_bytes((900, 1600), color=(160, 64, 48, 128), mode="RGBA")),
            filename="maple.png",
            content_type="image/png",
        )},
        form={"id": "maple_smile", "name": "枫糖 微笑", "kind": "portrait"},
        headers=admin_headers(client),
    )
    assert response.status_code == 201
    with Image.open(io.BytesIO(uploaded["data"])) as converted:
        assert converted.mode in ("RGBA", "RGBa")


@runs
async def test_asset_id_must_be_a_slug(client, monkeypatch):
    from game_host import cdn_client

    monkeypatch.setattr(cdn_client, "upload_bytes_at", lambda path, data, content_type: True)
    response = await client.post(
        "/api/red-leaf-town/admin/story/assets",
        files={"file": FileStorage(stream=io.BytesIO(png_bytes((640, 360))), filename="gate.png", content_type="image/png")},
        form={"id": "Autumn Gate", "name": "镇口", "kind": "background"},
        headers=admin_headers(client),
    )
    assert response.status_code == 400
    assert (await response.get_json())["code"] == "invalid_asset"


@runs
async def test_portrait_layout_is_saved_on_the_asset(client, monkeypatch):
    from game_host import cdn_client

    monkeypatch.setattr(cdn_client, "upload_bytes_at", lambda path, data, content_type: True)
    monkeypatch.setattr(cdn_client, "cdn_url_at", lambda path: f"https://cdn.example/{path}")
    await client.post(
        "/api/red-leaf-town/admin/story/assets",
        files={"file": FileStorage(stream=io.BytesIO(png_bytes((900, 1600))), filename="maple.png", content_type="image/png")},
        form={"id": "maple_smile", "name": "枫糖", "kind": "portrait"},
        headers=admin_headers(client),
    )

    updated = await client.patch(
        "/api/red-leaf-town/admin/story/assets/maple_smile",
        json={
            "inline_layout": {"scale": 1.35, "offset_x": -0.12, "offset_y": 0.06},
            "stage_layout": {"scale": 3.2, "offset_x": 0, "offset_y": 0},
        },
        headers=admin_headers(client),
    )
    body = await updated.get_json()
    assert updated.status_code == 200
    assert body["data"]["inline_layout"] == {"scale": 1.35, "offset_x": -0.12, "offset_y": 0.06}
    assert body["data"]["stage_layout"]["scale"] == 3.2

    listed = await client.get("/api/red-leaf-town/admin/story", headers=admin_headers(client))
    assert (await listed.get_json())["data"]["assets"][0]["inline_layout"]["scale"] == 1.35

    rejected = await client.patch(
        "/api/red-leaf-town/admin/story/assets/maple_smile",
        json={"inline_layout": {"scale": 5}},
        headers=admin_headers(client),
    )
    assert rejected.status_code == 400

    missing = await client.patch(
        "/api/red-leaf-town/admin/story/assets/nope",
        json={"scale": 1.2},
        headers=admin_headers(client),
    )
    assert missing.status_code == 404


@runs
async def test_asset_delete_blocked_while_referenced(client, monkeypatch, story_paths):
    from game_host import cdn_client

    asset_path, script_dir, *_ = story_paths
    monkeypatch.setattr(cdn_client, "upload_bytes_at", lambda path, data, content_type: True)
    monkeypatch.setattr(cdn_client, "cdn_url_at", lambda path: f"https://cdn.example/{path}")
    await client.post(
        "/api/red-leaf-town/admin/story/assets",
        files={"file": FileStorage(stream=io.BytesIO(png_bytes((640, 360))), filename="gate.png", content_type="image/png")},
        form={"id": "autumn_gate", "name": "镇口", "kind": "background"},
        headers=admin_headers(client),
    )
    (script_dir / "opening.json").write_text(json.dumps({
        "id": "opening",
        "title": "抵达红叶镇",
        "trigger": {"hook": "player_level", "params": {"level": 2}},
        "steps": [
            {"type": "background", "asset_id": "autumn_gate"},
            {"type": "dialogue", "speaker": "枫糖", "text": "欢迎来到红叶镇。"},
        ],
    }), encoding="utf-8")
    reloaded = await client.post("/api/red-leaf-town/admin/story/reload", headers=admin_headers(client))
    assert (await reloaded.get_json())["data"]["script_count"] == 2

    blocked = await client.delete("/api/red-leaf-town/admin/story/assets/autumn_gate", headers=admin_headers(client))
    assert blocked.status_code == 409

    (script_dir / "opening.json").unlink()
    await client.post("/api/red-leaf-town/admin/story/reload", headers=admin_headers(client))
    removed = await client.delete("/api/red-leaf-town/admin/story/assets/autumn_gate", headers=admin_headers(client))
    assert removed.status_code == 200


@runs
async def test_broken_script_reports_invalid_content(client, story_paths):
    _, script_dir, *_ = story_paths
    (script_dir / "broken.json").write_text(json.dumps({
        "id": "broken",
        "title": "坏剧本",
        "trigger": {"hook": "cue", "params": {"cue": "view:farm"}},
        "steps": [{"type": "background", "asset_id": "missing_asset"}, {"type": "dialogue", "text": "……"}],
    }), encoding="utf-8")
    await client.post("/api/red-leaf-town/admin/story/reload", headers=admin_headers(client))
    response = await client.get("/api/red-leaf-town/admin/story", headers=admin_headers(client))
    assert response.status_code == 400
    assert (await response.get_json())["code"] == "invalid_story_content"


def stub_cdn(monkeypatch):
    from game_host import cdn_client

    monkeypatch.setattr(cdn_client, "upload_bytes_at", lambda path, data, content_type: True)
    monkeypatch.setattr(cdn_client, "cdn_url_at", lambda path: f"https://cdn.example/{path}")


async def upload_asset(client, asset_id, kind="background", size=(1600, 900), **form):
    return await client.post(
        "/api/red-leaf-town/admin/story/assets",
        files={"file": FileStorage(
            stream=io.BytesIO(png_bytes(size)),
            filename=f"{asset_id}.png",
            content_type="image/png",
        )},
        form={"id": asset_id, "kind": kind, **form},
        headers=admin_headers(client),
    )


@runs
async def test_upload_refuses_to_replace_an_asset_without_the_overwrite_flag(client, monkeypatch):
    stub_cdn(monkeypatch)
    first = await upload_asset(client, "autumn_gate", name="镇口")
    assert first.status_code == 201
    original_key = (await first.get_json())["data"]["asset_key"]

    conflict = await upload_asset(client, "autumn_gate", name="别的图")
    body = await conflict.get_json()
    assert conflict.status_code == 409
    assert body["code"] == "asset_exists"

    listed = await client.get("/api/red-leaf-town/admin/story", headers=admin_headers(client))
    assets = (await listed.get_json())["data"]["assets"]
    assert [(entry["id"], entry["name"], entry["asset_key"]) for entry in assets] == [
        ("autumn_gate", "镇口", original_key),
    ]


@runs
async def test_overwrite_replaces_the_image_and_keeps_the_portrait_layout(client, monkeypatch):
    stub_cdn(monkeypatch)
    await upload_asset(client, "maple_smile", kind="portrait", size=(900, 1600), name="枫糖")
    await client.patch(
        "/api/red-leaf-town/admin/story/assets/maple_smile",
        json={"inline_layout": {"scale": 2.4, "offset_x": -0.1, "offset_y": 0.3}},
        headers=admin_headers(client),
    )

    replaced = await upload_asset(
        client,
        "maple_smile",
        kind="portrait",
        size=(450, 800),
        overwrite="true",
    )
    body = await replaced.get_json()

    assert replaced.status_code == 201
    assert body["data"]["width"] == 450
    # 没填名字就沿用原来的，调好的站位也不会被上传重置。
    assert body["data"]["name"] == "枫糖"
    assert body["data"]["inline_layout"] == {"scale": 2.4, "offset_x": -0.1, "offset_y": 0.3}


@runs
async def test_overwrite_cannot_change_the_asset_kind(client, monkeypatch):
    stub_cdn(monkeypatch)
    await upload_asset(client, "autumn_gate", name="镇口")

    swapped = await upload_asset(
        client,
        "autumn_gate",
        kind="portrait",
        size=(900, 1600),
        overwrite="true",
    )
    body = await swapped.get_json()

    assert swapped.status_code == 409
    assert body["code"] == "asset_kind_mismatch"


@runs
async def test_overwrite_fills_in_a_placeholder_asset(client, monkeypatch, story_paths):
    stub_cdn(monkeypatch)
    asset_path, *_ = story_paths
    asset_path.write_text(
        json.dumps({"schema_version": 1, "assets": [
            {"id": "portal_hollow", "kind": "background", "name": "TODO 传送门空地"},
        ]}),
        encoding="utf-8",
    )
    load_story_asset_catalog.cache_clear()

    filled = await upload_asset(client, "portal_hollow", overwrite="true")
    body = await filled.get_json()

    assert filled.status_code == 201
    assert body["data"]["asset_key"].startswith("red-leaf-town/story/background/portal_hollow-")
    assert body["data"]["name"] == "TODO 传送门空地"
    assert body["data"]["url"].startswith("https://cdn.example/")


@runs
async def test_story_resources_requires_login(client):
    response = await client.get("/api/red-leaf-town/story/resources")
    assert response.status_code == 401


@runs
async def test_story_resources_lists_assets_and_partner_artworks(client, story_paths):
    """剧情编辑器要给社区用，所以素材清单只要登录就能读，不需要管理员 Token。"""
    asset_path, _script_dir, partner_path, _upload_path = story_paths
    asset_path.write_text(json.dumps({
        "schema_version": 1,
        "assets": [
            {
                "id": "autumn_gate",
                "kind": "background",
                "name": "镇口 · 黄昏",
                "asset_key": "red-leaf-town/story/background/autumn_gate.webp",
                "width": 1920,
                "height": 1080,
                "content_type": "image/webp",
            },
            {
                "id": "maple_smile",
                "kind": "portrait",
                "name": "枫糖 微笑",
                "asset_key": "red-leaf-town/story/portrait/maple_smile.webp",
                "width": 900,
                "height": 1600,
                "content_type": "image/webp",
                "inline_layout": {"scale": 2.4, "offset_x": -0.1, "offset_y": 0.2},
            },
            {"id": "todo_shrine", "kind": "background", "name": "TODO 神社"},
        ],
    }), encoding="utf-8")
    partner_path.write_text(json.dumps({
        "partners": [
            {
                "id": "fein",
                "name": "绯恩",
                "rarity": 4,
                "tendencies": [{"industry": "gathering", "level_1": 40, "level_60": 300}],
                "artworks": [{
                    "breakthrough": 0,
                    "asset_key": "red-leaf-town/partners/fein/breakthrough-0.webp",
                    "width": 900,
                    "height": 1600,
                    "content_type": "image/webp",
                }],
            },
            {
                "id": "nobody",
                "name": "还没画",
                "rarity": 3,
                "tendencies": [{"industry": "farming", "level_1": 40, "level_60": 300}],
            },
        ],
    }), encoding="utf-8")
    load_story_asset_catalog.cache_clear()
    load_partner_catalog.cache_clear()

    authenticate(client)
    response = await client.get("/api/red-leaf-town/story/resources")
    body = await response.get_json()
    assert response.status_code == 200

    # 占位素材没有图，编辑器选了也画不出来，所以不列出来。
    assert [entry["id"] for entry in body["data"]["assets"]] == ["autumn_gate", "maple_smile"]
    portrait = body["data"]["assets"][1]
    assert portrait["layouts"]["inline"]["scale"] == 2.4
    assert portrait["layouts"]["stage"]["scale"] == 1.0

    # 没有插画的伙伴当不了立绘。
    assert [entry["id"] for entry in body["data"]["partners"]] == ["fein"]
    assert body["data"]["partners"][0]["artworks"][0]["breakthrough"] == 0

    load_partner_catalog.cache_clear()


async def upload_image(client, size=(320, 180), kind="background", name="社区背景"):
    return await client.post(
        "/api/red-leaf-town/story/uploads",
        files={"file": FileStorage(io.BytesIO(png_bytes(size)), filename="shot.png")},
        form={"kind": kind, "name": name},
    )


@runs
async def test_story_upload_requires_login(client):
    response = await client.post("/api/red-leaf-town/story/uploads", form={"kind": "background"})
    assert response.status_code == 401


@runs
async def test_story_upload_transcodes_and_records_uploader(client, monkeypatch, story_paths):
    stub_cdn(monkeypatch)
    authenticate(client)
    response = await upload_image(client)
    body = await response.get_json()
    assert response.status_code == 201
    assert body["data"]["id"].startswith("up_")
    assert body["data"]["mine"] is True
    assert body["data"]["url"].startswith("https://cdn.example/")

    # 本地只留索引，图在 CDN 上；上传者记在索引里但不外发。
    _, _, _, upload_path = story_paths
    load_story_upload_index.cache_clear()
    record = load_story_upload_index(upload_path).uploads[0]
    assert record.uploader_sub == "story-sub"
    assert record.content_type == "image/webp"
    assert record.asset_key.endswith(".webp")
    assert record.source_bytes > 0
    assert "uploader_sub" not in body["data"]


@runs
async def test_story_upload_id_is_a_valid_script_asset_id(client, monkeypatch):
    """导出的剧本会直接引用这个 ID，格式必须过得了 StoryScript 的校验。"""
    stub_cdn(monkeypatch)
    authenticate(client)
    body = await (await upload_image(client)).get_json()
    assert re.fullmatch(r"^[a-z][a-z0-9_-]{1,63}$", body["data"]["id"])


@runs
async def test_story_upload_quota_is_enforced_within_the_hour(client, monkeypatch):
    stub_cdn(monkeypatch)
    authenticate(client)
    # 额度在 fixture 里压到 4 KB。PNG 压缩后的大小不好预判，所以一直传到被挡住为止。
    codes = []
    for index in range(30):
        codes.append((await upload_image(client, name=f"第 {index} 张")).status_code)
        if codes[-1] == 429:
            break
    assert codes[0] == 201, "第一张应该能传上去"
    assert codes[-1] == 429, f"额度没有拦住：{codes}"

    blocked = await upload_image(client)
    assert (await blocked.get_json())["code"] == "upload_quota_exceeded"


@runs
async def test_uploads_are_private_to_their_uploader(client, monkeypatch):
    stub_cdn(monkeypatch)
    authenticate(client)
    mine = (await (await upload_image(client, name="我的图")).get_json())["data"]["id"]

    get_service().ensure_player("other-sub", "别人")
    authenticate(client, "other-sub")
    listed = (await (await client.get("/api/red-leaf-town/story/resources")).get_json())["data"]
    assert listed["uploads"] == []
    # 也不能借着 ID 删掉别人的图
    assert (await client.delete(f"/api/red-leaf-town/story/uploads/{mine}")).status_code == 403

    authenticate(client)
    own = (await (await client.get("/api/red-leaf-town/story/resources")).get_json())["data"]["uploads"]
    assert [entry["id"] for entry in own] == [mine]
    assert (await client.delete(f"/api/red-leaf-town/story/uploads/{mine}")).status_code == 200


@runs
async def test_resources_report_remaining_quota(client, monkeypatch):
    stub_cdn(monkeypatch)
    authenticate(client)
    before = (await (await client.get("/api/red-leaf-town/story/resources")).get_json())["data"]["upload"]
    assert before["used_bytes"] == 0
    assert before["quota_bytes"] == UPLOAD_TEST_QUOTA
    await upload_image(client)
    after = (await (await client.get("/api/red-leaf-town/story/resources")).get_json())["data"]["upload"]
    assert after["used_bytes"] > 0


@runs
async def test_resources_offer_an_example_script_for_new_authors(client):
    authenticate(client)
    example = (await (await client.get("/api/red-leaf-town/story/resources")).get_json())["data"]["example"]
    assert example["id"] == "demo_welcome"
    assert example["title"].endswith("（示例）")
    assert example["steps"][0]["type"] == "dialogue"


@runs
async def test_upload_owner_can_tune_default_layout(client, monkeypatch, story_paths):
    stub_cdn(monkeypatch)
    authenticate(client)
    created = (await (await upload_image(client, size=(180, 320), kind="portrait", name="小孩")).get_json())["data"]
    assert created["layout"] == {"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0}

    response = await client.patch(
        f"/api/red-leaf-town/story/uploads/{created['id']}",
        json={"name": "小孩 · 站正了", "layout": {"scale": 2.4, "offset_x": -0.2, "offset_y": 0.35}},
    )
    body = await response.get_json()
    assert response.status_code == 200
    assert body["data"]["name"] == "小孩 · 站正了"
    assert body["data"]["layout"] == {"scale": 2.4, "offset_x": -0.2, "offset_y": 0.35}

    _, _, _, upload_path = story_paths
    load_story_upload_index.cache_clear()
    assert load_story_upload_index(upload_path).uploads[0].layout.scale == 2.4


@runs
async def test_layout_out_of_range_is_rejected(client, monkeypatch):
    stub_cdn(monkeypatch)
    authenticate(client)
    created = (await (await upload_image(client, kind="portrait")).get_json())["data"]
    response = await client.patch(
        f"/api/red-leaf-town/story/uploads/{created['id']}",
        json={"layout": {"scale": 9.0, "offset_x": 0, "offset_y": 0}},
    )
    assert response.status_code == 400


@runs
async def test_cannot_tune_someone_elses_upload(client, monkeypatch):
    stub_cdn(monkeypatch)
    authenticate(client)
    created = (await (await upload_image(client, kind="portrait")).get_json())["data"]

    get_service().ensure_player("other-sub", "别人")
    authenticate(client, "other-sub")
    response = await client.patch(
        f"/api/red-leaf-town/story/uploads/{created['id']}",
        json={"layout": {"scale": 2.0, "offset_x": 0, "offset_y": 0}},
    )
    assert response.status_code == 404


def shared_script(asset_id):
    return {
        **WELCOME_SCRIPT,
        "steps": [{"type": "background", "asset_id": asset_id}, *WELCOME_SCRIPT["steps"]],
        "rewards": {"coins": 99999},
    }


@runs
async def test_share_requires_login_and_missing_share_is_404(client):
    assert (await client.post("/api/red-leaf-town/story/shares", json={})).status_code == 401
    assert (await client.get("/api/red-leaf-town/story/shares/invalid")).status_code == 404
    assert (await client.get("/api/red-leaf-town/story/shares/" + "a" * 64)).status_code == 404


@runs
async def test_share_snapshot_public_and_survives_upload_removal(client, monkeypatch):
    stub_cdn(monkeypatch)
    authenticate(client)
    uploaded = (await (await upload_image(client)).get_json())["data"]
    unused = (await (await upload_image(client)).get_json())["data"]
    raw = shared_script(uploaded["id"])
    raw["metadata"] = {"community_assets": [{"asset_key": "evil"}]}
    response = await client.post("/api/red-leaf-town/story/shares", json=raw)
    assert response.status_code == 201
    share_id = (await response.get_json())["data"]["id"]
    repeated = await client.post("/api/red-leaf-town/story/shares", json=raw)
    assert (await repeated.get_json())["data"]["id"] == share_id
    await client.delete(f"/api/red-leaf-town/story/uploads/{uploaded['id']}")
    client.delete_cookie("localhost", COOKIE_NAME)
    response = await client.get(f"/api/red-leaf-town/story/shares/{share_id}")
    assert response.status_code == 200
    script = (await response.get_json())["data"]
    assert script["steps"][0]["asset"]["url"].startswith("https://cdn.example/")
    assert script["steps"][0]["asset"]["asset_key"] == uploaded["asset_key"]
    assert script["rewards"]["empty"] is True
    serialized = json.dumps(script)
    assert unused["id"] not in serialized
    assert "story-sub" not in serialized
    assert "evil" not in serialized


@runs
async def test_share_rejects_foreign_missing_and_wrong_kind_assets(client, monkeypatch):
    stub_cdn(monkeypatch)
    authenticate(client)
    uploaded = (await (await upload_image(client)).get_json())["data"]
    authenticate(client, "other-sub")
    assert (await client.post("/api/red-leaf-town/story/shares", json=shared_script(uploaded["id"]))).status_code == 400
    authenticate(client)
    portrait = (await (await upload_image(client, kind="portrait")).get_json())["data"]
    for asset_id in (portrait["id"], "missing_asset"):
        assert (await client.post("/api/red-leaf-town/story/shares", json=shared_script(asset_id))).status_code == 400
    for raw in ([], {}, WELCOME_SCRIPT, {**shared_script(uploaded["id"]), "steps": []}):
        assert (await client.post("/api/red-leaf-town/story/shares", json=raw)).status_code == 400
