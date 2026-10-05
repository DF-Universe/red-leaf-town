from __future__ import annotations

import hashlib
import io
import os
import time
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from quart import Blueprint, jsonify, make_response, redirect, request, send_from_directory
from pydantic import ValidationError

from red_leaf_town.config import get_admin_subs
from red_leaf_town.application import GameError
from red_leaf_town.content import (
    DEFAULT_CONTENT_PATH,
    CropDefinition,
    GameContent,
    ItemDefinition,
    ShopEntry,
    load_content,
    save_content,
)
from red_leaf_town.runtime import get_service
from red_leaf_town.partner_content import (
    BREAKTHROUGH_LEVEL_CAPS,
    DEFAULT_PARTNER_CONTENT_PATH,
    GROWTH_CURVE_NAMES,
    INDUSTRY_NAMES,
    PartnerCatalog,
    PartnerDefinition,
    PartnerArtwork,
    load_partner_catalog,
    save_partner_catalog,
)
from red_leaf_town.partner_traits import partner_trait_catalog
from red_leaf_town.story_assets import (
    DEFAULT_STORY_ASSET_PATH,
    STORY_ASSET_KIND_NAMES,
    StoryAsset,
    StoryAssetCatalog,
    StoryAssetLayout,
    load_story_asset_catalog,
    save_story_asset_catalog,
)
from red_leaf_town.story_content import DEFAULT_STORY_SCRIPT_DIR, load_story_catalog
from red_leaf_town.story_uploads import (
    DEFAULT_STORY_UPLOAD_PATH,
    UPLOAD_QUOTA_BYTES,
    UPLOAD_WINDOW_SECONDS,
    StoryUpload,
    append_story_upload,
    load_story_upload_index,
    new_upload_id,
    remove_story_upload,
    update_story_upload,
    used_quota,
)
from red_leaf_town.story_shares import build_share, load_share, save_share
from red_leaf_town.story_triggers import story_trigger_hook_codes


HOME_PATH = "/red-leaf-town/"
# 内测站。前端另外构建一份 base=/red-leaf-town-beta/ 的产物，后端和线上共用同一套数据。
BETA_HOME_PATH = "/red-leaf-town-beta/"
COOKIE_NAME = "divingfish_red_leaf_town_token"
COOKIE_MAX_AGE = 86400 * 30
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
FRONTEND_DIST_BETA = Path(__file__).resolve().parents[3] / "frontend" / "dist-beta"
MAX_PARTNER_ARTWORK_SIZE = 12 * 1024 * 1024
MAX_PARTNER_ARTWORK_PIXELS = 60_000_000
MAX_PARTNER_ARTWORK_UNIT = 120
PARTNER_ARTWORK_INPUT_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_STORY_ASSET_SIZE = 12 * 1024 * 1024
MAX_STORY_ASSET_PIXELS = 60_000_000
MAX_STORY_ASSET_EDGE = {"background": 2560, "portrait": 1920}


def _safe_next(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate.startswith((HOME_PATH, BETA_HOME_PATH)):
        return ""
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return ""
    return candidate


def _error(message: str, status: int = 400, code: str = "request_error"):
    return jsonify({"code": code, "message": message}), status


def _is_admin_request() -> bool:
    from private.libraries.admin_auth import is_admin_request
    from private.libraries.jwt import AUD_RED_LEAF_TOWN

    return is_admin_request(COOKIE_NAME, AUD_RED_LEAF_TOWN, get_admin_subs())


def _admin_error():
    return _error("无权限", 403, "forbidden")


def _validation_error(error: ValidationError):
    message = "; ".join(item["msg"] for item in error.errors())
    return _error(message, 400, "invalid_partner")


def _largest_nine_sixteen_crop(width: int, height: int) -> tuple[int, int, int, int]:
    unit = min(width // 9, height // 16)
    if unit < 1:
        raise ValueError("图片尺寸太小，无法裁剪为 9:16")
    crop_width = unit * 9
    crop_height = unit * 16
    return (
        (width - crop_width) // 2,
        (height - crop_height) // 2,
        crop_width,
        crop_height,
    )


def _parse_artwork_crop(form, width: int, height: int) -> tuple[int, int, int, int]:
    values = [str(form.get(key, "")).strip() for key in ("x", "y", "w", "h")]
    if not any(values):
        return _largest_nine_sixteen_crop(width, height)
    if not all(values):
        raise ValueError("裁剪参数必须同时包含 x、y、w、h")
    try:
        x, y, crop_width, crop_height = (int(value) for value in values)
    except ValueError as exc:
        raise ValueError("裁剪参数必须是整数") from exc
    if x < 0 or y < 0 or crop_width <= 0 or crop_height <= 0:
        raise ValueError("裁剪参数超出允许范围")
    if x + crop_width > width or y + crop_height > height:
        raise ValueError("裁剪框不能超出原图边界")
    if crop_width * 16 != crop_height * 9:
        raise ValueError("插画裁剪框必须是 9:16 比例")
    return x, y, crop_width, crop_height


def _prepare_partner_artwork(data: bytes, form) -> tuple[bytes, int, int]:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as source:
        image_format = str(source.format or "").upper()
        if image_format not in PARTNER_ARTWORK_INPUT_FORMATS:
            raise ValueError("仅支持 JPG、PNG 和 WebP 图片")
        image = ImageOps.exif_transpose(source)
        image.load()
    width, height = image.size
    if width * height > MAX_PARTNER_ARTWORK_PIXELS:
        raise ValueError("图片像素总量不能超过 6000 万")
    x, y, crop_width, crop_height = _parse_artwork_crop(form, width, height)
    image = image.crop((x, y, x + crop_width, y + crop_height))

    unit = min(crop_width // 9, MAX_PARTNER_ARTWORK_UNIT)
    output_width, output_height = unit * 9, unit * 16
    if image.size != (output_width, output_height):
        image = image.resize((output_width, output_height), Image.Resampling.LANCZOS)
    has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
    image = image.convert("RGBA" if has_alpha else "RGB")
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=88, method=6)
    return output.getvalue(), output_width, output_height


def _prepare_story_image(data: bytes, kind: str) -> tuple[bytes, int, int]:
    """剧情素材不裁剪，只做格式校验、长边限制和 WebP 转码，保留立绘的透明通道。"""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as source:
        image_format = str(source.format or "").upper()
        if image_format not in PARTNER_ARTWORK_INPUT_FORMATS:
            raise ValueError("仅支持 JPG、PNG 和 WebP 图片")
        image = ImageOps.exif_transpose(source)
        image.load()
    width, height = image.size
    if width * height > MAX_STORY_ASSET_PIXELS:
        raise ValueError("图片像素总量不能超过 6000 万")
    max_edge = MAX_STORY_ASSET_EDGE[kind]
    if max(width, height) > max_edge:
        scale = max_edge / max(width, height)
        width, height = max(1, round(width * scale)), max(1, round(height * scale))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
    image = image.convert("RGBA" if has_alpha else "RGB")
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=88, method=6)
    return output.getvalue(), width, height


def _default_avatar_crop(breakthrough: int, width: int, height: int) -> dict:
    side = min(width, height)
    return {
        "breakthrough": breakthrough,
        "x": (width - side) // 2,
        "y": (height - side) // 2,
        "w": side,
        "h": side,
    }


def _form_flag(form, key: str) -> bool:
    return str(form.get(key, "")).strip().lower() in ("1", "true", "on", "yes")


def _attach_cdn_urls(value):
    from src.libraries import cdn_client

    if isinstance(value, dict):
        asset_key = value.get("asset_key")
        if asset_key and "url" not in value:
            value["url"] = cdn_client.cdn_url_at(str(asset_key))
        for nested in value.values():
            _attach_cdn_urls(nested)
    elif isinstance(value, list):
        for nested in value:
            _attach_cdn_urls(nested)
    return value


def _oauth_error(message: str):
    return redirect(f"{HOME_PATH}?oauth_error={quote(str(message))}")


def _session_subject() -> str:
    from private.libraries.jwt import AUD_RED_LEAF_TOWN, decode

    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return ""
    payload = decode(token, audience=AUD_RED_LEAF_TOWN)
    return str(payload.get("sub", "") or "").strip()


def login_required(handler):
    @wraps(handler)
    async def wrapped(*args, **kwargs):
        subject = _session_subject()
        if not subject:
            return _error("未登录", 401, "unauthorized")
        return await handler(subject, *args, **kwargs)

    return wrapped


def create_blueprint(
    *,
    game_content_path: str | Path = DEFAULT_CONTENT_PATH,
    partner_catalog_path: str | Path = DEFAULT_PARTNER_CONTENT_PATH,
    story_asset_path: str | Path = DEFAULT_STORY_ASSET_PATH,
    story_script_dir: str | Path = DEFAULT_STORY_SCRIPT_DIR,
    story_upload_path: str | Path = DEFAULT_STORY_UPLOAD_PATH,
    upload_quota_bytes: int = UPLOAD_QUOTA_BYTES,
) -> Blueprint:
    blueprint = Blueprint("red_leaf_town", __name__)
    content_path = Path(game_content_path)
    catalog_path = Path(partner_catalog_path)
    story_asset_catalog_path = Path(story_asset_path)
    story_script_path = Path(story_script_dir)
    story_upload_index_path = Path(story_upload_path)

    def story_catalog():
        return load_story_catalog(story_script_path, story_asset_catalog_path, catalog_path)

    def story_admin_payload() -> dict:
        from src.libraries import cdn_client

        assets = load_story_asset_catalog(story_asset_catalog_path)
        return {
            "assets": [asset.model_dump() for asset in sorted(assets.assets, key=lambda entry: entry.id)],
            "scripts": get_service().story_scripts(),
            "options": {
                "kinds": [{"id": key, "name": value} for key, value in STORY_ASSET_KIND_NAMES.items()],
                "trigger_hooks": story_trigger_hook_codes(),
                "script_directory": str(story_script_path),
                "cdn": {
                    "provider": cdn_client.active_provider(),
                    "configured": cdn_client.is_configured(),
                    "base_url": cdn_client.cdn_base_url(),
                },
            },
        }

    def serialize_partner(partner: PartnerDefinition) -> dict:
        from src.libraries import cdn_client

        data = partner.model_dump()
        for artwork in data["artworks"]:
            artwork["url"] = cdn_client.cdn_url_at(artwork["asset_key"])
        data["complete"] = partner.complete
        data["ability_preview"] = {
            tendency.industry: {
                str(level): partner.ability_at(tendency.industry, level, partner.rarity)
                for level in (1, 20, 40, 60)
            }
            for tendency in partner.tendencies
        }
        return data

    def admin_payload(catalog: PartnerCatalog) -> dict:
        from src.libraries import cdn_client

        return {
            "partners": [serialize_partner(partner) for partner in catalog.partners],
            "options": {
                "industries": [{"id": key, "name": value} for key, value in INDUSTRY_NAMES.items()],
                "growth_curves": [{"id": key, "name": value} for key, value in GROWTH_CURVE_NAMES.items()],
                "rarities": [3, 4, 5],
                "breakthrough_level_caps": list(BREAKTHROUGH_LEVEL_CAPS),
                "cdn": {
                    "provider": cdn_client.active_provider(),
                    "configured": cdn_client.is_configured(),
                    "base_url": cdn_client.cdn_base_url(),
                },
                "traits": [
                    {
                        "code": trait.code,
                        "name": trait.name,
                        "description": trait.description,
                        "implemented": trait.implemented,
                    }
                    for trait in partner_trait_catalog()
                ],
            },
        }

    def crop_admin_payload(content: GameContent) -> dict:
        shop_by_seed = {entry.item_id: entry for entry in content.shop}
        items = content.item_map
        return {
            "schema_version": content.schema_version,
            "quality_grades": [grade.model_dump() for grade in content.quality.grades],
            "crops": [
                {
                    **crop.model_dump(),
                    "seed_price": shop_by_seed[crop.seed_item_id].price if crop.seed_item_id in shop_by_seed else None,
                    "shop_id": shop_by_seed[crop.seed_item_id].id if crop.seed_item_id in shop_by_seed else None,
                    "produce_sell_price": items[crop.produce_item_id].sell_price,
                    "chart_enabled": crop.seed_item_id in shop_by_seed,
                }
                for crop in content.crops
            ],
        }

    def update_crop_balance(content: GameContent, records: object) -> GameContent:
        if not isinstance(records, list) or not records:
            raise ValueError("crops must be a non-empty list")
        if any(not isinstance(record, dict) for record in records):
            raise ValueError("each crop update must be an object")
        ids = [str(record.get("id", "")) for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("crop updates must have unique ids")

        crop_map = content.crop_map
        item_map = content.item_map
        shop_by_seed = {entry.item_id: entry for entry in content.shop}
        crop_fields = {
            "growth_seconds",
            "minimum_duration_seconds",
            "time_difficulty",
            "yield_min",
            "yield_max",
            "stamina_cost",
            "plant_xp",
            "harvest_xp",
            "quality",
        }
        updated_crops = {crop.id: crop for crop in content.crops}
        updated_items = {item.id: item for item in content.items}
        updated_shop = {entry.id: entry for entry in content.shop}

        for record in records:
            crop_id = str(record.get("id", ""))
            current = crop_map.get(crop_id)
            if current is None:
                raise ValueError(f"unknown crop: {crop_id}")
            crop_data = current.model_dump()
            crop_data.update({field: record[field] for field in crop_fields if field in record})
            updated_crops[crop_id] = CropDefinition.model_validate(crop_data)

            if "produce_sell_price" in record:
                item = item_map[current.produce_item_id]
                updated_items[item.id] = ItemDefinition.model_validate({
                    **item.model_dump(),
                    "sell_price": record["produce_sell_price"],
                })
            if "seed_price" in record and record["seed_price"] is not None:
                shop_entry = shop_by_seed.get(current.seed_item_id)
                if shop_entry is None:
                    raise ValueError(f"crop {crop_id} does not have a purchasable seed")
                updated_shop[shop_entry.id] = ShopEntry.model_validate({
                    **shop_entry.model_dump(),
                    "price": record["seed_price"],
                })

        payload = content.model_dump()
        payload["crops"] = [updated_crops[crop.id].model_dump() for crop in content.crops]
        payload["items"] = [updated_items[item.id].model_dump() for item in content.items]
        payload["shop"] = [updated_shop[entry.id].model_dump() for entry in content.shop]
        return GameContent.model_validate(payload)

    def replace_partner(catalog: PartnerCatalog, partner: PartnerDefinition) -> PartnerCatalog:
        records = [entry for entry in catalog.partners if entry.id != partner.id]
        records.append(partner)
        records.sort(key=lambda entry: entry.id)
        return PartnerCatalog(schema_version=catalog.schema_version, partners=records)

    @blueprint.errorhandler(GameError)
    async def game_error(error: GameError):
        return _error(error.message, error.status, error.code)

    @blueprint.get("/api/red-leaf-town/health")
    async def health():
        return jsonify({"code": 0, "message": "ok"})

    @blueprint.get("/api/red-leaf-town/admin/partners")
    async def admin_partner_list():
        if not _is_admin_request():
            return _admin_error()
        return jsonify({"code": 0, "data": admin_payload(load_partner_catalog(catalog_path))})

    @blueprint.get("/api/red-leaf-town/admin/crops")
    async def admin_crop_list():
        if not _is_admin_request():
            return _admin_error()
        return jsonify({"code": 0, "data": crop_admin_payload(load_content(content_path))})

    @blueprint.put("/api/red-leaf-town/admin/crops")
    async def admin_crop_update():
        if not _is_admin_request():
            return _admin_error()
        payload = await request.get_json(silent=True) or {}
        try:
            content = update_crop_balance(load_content(content_path), payload.get("crops"))
        except (ValidationError, ValueError) as exc:
            message = "; ".join(item["msg"] for item in exc.errors()) if isinstance(exc, ValidationError) else str(exc)
            return _error(message, 400, "invalid_crop_balance")
        save_content(content, content_path)
        service = get_service()
        service.content = content
        if hasattr(service.repository, "content"):
            service.repository.content = content
        return jsonify({"code": 0, "data": crop_admin_payload(content)})

    @blueprint.post("/api/red-leaf-town/admin/partners")
    async def admin_partner_create():
        if not _is_admin_request():
            return _admin_error()
        payload = await request.get_json(silent=True) or {}
        try:
            partner = PartnerDefinition.model_validate(payload)
        except ValidationError as exc:
            return _validation_error(exc)
        catalog = load_partner_catalog(catalog_path)
        if partner.id in catalog.partner_map:
            return _error("伙伴 ID 已存在", 409, "partner_exists")
        catalog = replace_partner(catalog, partner)
        save_partner_catalog(catalog, catalog_path)
        return jsonify({"code": 0, "data": serialize_partner(partner)}), 201

    @blueprint.put("/api/red-leaf-town/admin/partners/<string:partner_id>")
    async def admin_partner_update(partner_id: str):
        if not _is_admin_request():
            return _admin_error()
        catalog = load_partner_catalog(catalog_path)
        if partner_id not in catalog.partner_map:
            return _error("伙伴不存在", 404, "partner_not_found")
        payload = await request.get_json(silent=True) or {}
        payload["id"] = partner_id
        try:
            partner = PartnerDefinition.model_validate(payload)
        except ValidationError as exc:
            return _validation_error(exc)
        catalog = replace_partner(catalog, partner)
        save_partner_catalog(catalog, catalog_path)
        return jsonify({"code": 0, "data": serialize_partner(partner)})

    @blueprint.delete("/api/red-leaf-town/admin/partners/<string:partner_id>")
    async def admin_partner_delete(partner_id: str):
        if not _is_admin_request():
            return _admin_error()
        catalog = load_partner_catalog(catalog_path)
        if partner_id not in catalog.partner_map:
            return _error("伙伴不存在", 404, "partner_not_found")
        updated = PartnerCatalog(
            schema_version=catalog.schema_version,
            partners=[partner for partner in catalog.partners if partner.id != partner_id],
        )
        save_partner_catalog(updated, catalog_path)
        return jsonify({"code": 0, "message": "已删除"})

    @blueprint.post("/api/red-leaf-town/admin/partners/<string:partner_id>/artworks/<int:breakthrough>")
    async def admin_partner_upload_artwork(partner_id: str, breakthrough: int):
        if not _is_admin_request():
            return _admin_error()
        if breakthrough not in (0, 1, 2):
            return _error("突破阶段必须是 0、1 或 2", 400, "invalid_breakthrough")
        catalog = load_partner_catalog(catalog_path)
        current = catalog.partner_map.get(partner_id)
        if current is None:
            return _error("伙伴不存在", 404, "partner_not_found")
        files = await request.files
        uploaded = files.get("file")
        if uploaded is None or not uploaded.filename:
            return _error("未选择图片", 400, "file_required")
        data = uploaded.read()
        if not data:
            return _error("图片内容为空", 400, "empty_file")
        if len(data) > MAX_PARTNER_ARTWORK_SIZE:
            return _error("图片不能超过 12 MB", 413, "file_too_large")
        try:
            form = await request.form
            webp_data, width, height = _prepare_partner_artwork(data, form)
        except ValueError as exc:
            return _error(str(exc), 400, "invalid_artwork_crop")
        except Exception:
            return _error("无法识别图片内容", 400, "invalid_image")

        content_type = "image/webp"
        digest = hashlib.sha256(webp_data).hexdigest()[:16]
        asset_key = f"red-leaf-town/partners/{partner_id}/breakthrough-{breakthrough}-{digest}.webp"
        artwork = PartnerArtwork(
            breakthrough=breakthrough,
            asset_key=asset_key,
            width=width,
            height=height,
            content_type=content_type,
        )
        payload = current.model_dump()
        payload["artworks"] = [
            entry.model_dump() for entry in current.artworks if entry.breakthrough != breakthrough
        ] + [artwork.model_dump()]
        payload["artworks"].sort(key=lambda entry: entry["breakthrough"])
        payload["avatar_crops"] = [
            entry.model_dump()
            for entry in current.avatar_crops
            if entry.breakthrough != breakthrough
        ] + [_default_avatar_crop(breakthrough, width, height)]
        payload["avatar_crops"].sort(key=lambda entry: entry["breakthrough"])
        try:
            partner = PartnerDefinition.model_validate(payload)
        except ValidationError as exc:
            return _validation_error(exc)
        from src.libraries import cdn_client

        if not cdn_client.upload_bytes_at(asset_key, webp_data, content_type):
            return _error("CDN 上传失败，请检查底层 provider 配置", 503, "cdn_upload_failed")
        save_partner_catalog(replace_partner(catalog, partner), catalog_path)
        return jsonify({"code": 0, "data": serialize_partner(partner)})

    @blueprint.get("/api/red-leaf-town/admin/story")
    async def admin_story_payload():
        if not _is_admin_request():
            return _admin_error()
        try:
            return jsonify({"code": 0, "data": _attach_cdn_urls(story_admin_payload())})
        except (ValueError, ValidationError) as exc:
            return _error(f"剧本内容无法加载：{exc}", 400, "invalid_story_content")

    @blueprint.post("/api/red-leaf-town/admin/story/reload")
    async def admin_story_reload():
        if not _is_admin_request():
            return _admin_error()
        load_story_asset_catalog.cache_clear()
        load_story_catalog.cache_clear()
        try:
            scripts = story_catalog().scripts
        except (ValueError, ValidationError) as exc:
            return _error(f"剧本内容无法加载：{exc}", 400, "invalid_story_content")
        return jsonify({"code": 0, "data": {"script_count": len(scripts)}})

    @blueprint.post("/api/red-leaf-town/admin/story/assets")
    async def admin_story_asset_upload():
        if not _is_admin_request():
            return _admin_error()
        form = await request.form
        kind = str(form.get("kind", "")).strip()
        if kind not in STORY_ASSET_KIND_NAMES:
            return _error("素材类型必须是背景或立绘", 400, "invalid_asset_kind")
        files = await request.files
        uploaded = files.get("file")
        if uploaded is None or not uploaded.filename:
            return _error("未选择图片", 400, "file_required")
        data = uploaded.read()
        if not data:
            return _error("图片内容为空", 400, "empty_file")
        if len(data) > MAX_STORY_ASSET_SIZE:
            return _error("图片不能超过 12 MB", 413, "file_too_large")

        asset_id = str(form.get("id", "")).strip()
        catalog = load_story_asset_catalog(story_asset_catalog_path)
        current = catalog.asset_map.get(asset_id)
        # 覆盖会顶掉剧本正在用的素材，所以要显式勾选，避免手滑写错 ID 就把别的图换掉。
        if current and not _form_flag(form, "overwrite"):
            return _error(f"素材 ID {asset_id} 已存在，勾选覆盖上传才能替换", 409, "asset_exists")
        if current and current.kind != kind:
            return _error(
                f"原素材是{STORY_ASSET_KIND_NAMES[current.kind]}，不能覆盖成{STORY_ASSET_KIND_NAMES[kind]}",
                409,
                "asset_kind_mismatch",
            )

        try:
            webp_data, width, height = _prepare_story_image(data, kind)
        except ValueError as exc:
            return _error(str(exc), 400, "invalid_image")
        except Exception:
            return _error("无法识别图片内容", 400, "invalid_image")

        digest = hashlib.sha256(webp_data).hexdigest()[:16]
        try:
            asset = StoryAsset(
                id=asset_id,
                kind=kind,
                name=str(form.get("name", "")).strip() or (current.name if current else "") or asset_id,
                asset_key=f"red-leaf-town/story/{kind}/{asset_id}-{digest}.webp",
                width=width,
                height=height,
                content_type="image/webp",
                # 覆盖只换图，调好的立绘站位和创建时间留着。
                inline_layout=current.inline_layout if current else StoryAssetLayout(),
                stage_layout=current.stage_layout if current else StoryAssetLayout(),
                created_at=current.created_at if current else 0,
            )
        except ValidationError as exc:
            return _error("; ".join(item["msg"] for item in exc.errors()), 400, "invalid_asset")
        from src.libraries import cdn_client

        if not cdn_client.upload_bytes_at(asset.asset_key, webp_data, asset.content_type):
            return _error("CDN 上传失败，请检查底层 provider 配置", 503, "cdn_upload_failed")
        records = [entry for entry in catalog.assets if entry.id != asset.id]
        records.append(asset)
        save_story_asset_catalog(
            StoryAssetCatalog(schema_version=catalog.schema_version, assets=records),
            story_asset_catalog_path,
        )
        load_story_catalog.cache_clear()
        return jsonify({"code": 0, "data": _attach_cdn_urls(asset.model_dump())}), 201

    @blueprint.patch("/api/red-leaf-town/admin/story/assets/<string:asset_id>")
    async def admin_story_asset_update(asset_id: str):
        if not _is_admin_request():
            return _admin_error()
        catalog = load_story_asset_catalog(story_asset_catalog_path)
        current = catalog.asset_map.get(asset_id)
        if current is None:
            return _error("素材不存在", 404, "asset_not_found")
        body = await request.get_json(silent=True) or {}
        payload = current.model_dump()
        for field in ("name", "inline_layout", "stage_layout"):
            if field in body:
                payload[field] = body[field]
        try:
            asset = StoryAsset.model_validate(payload)
        except ValidationError as exc:
            return _error("; ".join(item["msg"] for item in exc.errors()), 400, "invalid_asset")
        save_story_asset_catalog(
            StoryAssetCatalog(
                schema_version=catalog.schema_version,
                assets=[entry for entry in catalog.assets if entry.id != asset_id] + [asset],
            ),
            story_asset_catalog_path,
        )
        load_story_catalog.cache_clear()
        return jsonify({"code": 0, "data": _attach_cdn_urls(asset.model_dump())})

    @blueprint.delete("/api/red-leaf-town/admin/story/assets/<string:asset_id>")
    async def admin_story_asset_delete(asset_id: str):
        if not _is_admin_request():
            return _admin_error()
        catalog = load_story_asset_catalog(story_asset_catalog_path)
        if asset_id not in catalog.asset_map:
            return _error("素材不存在", 404, "asset_not_found")
        try:
            users = [script.id for script in story_catalog().scripts if asset_id in script.asset_ids]
        except (ValueError, ValidationError) as exc:
            return _error(f"剧本内容无法加载：{exc}", 400, "invalid_story_content")
        if users:
            return _error(f"这张素材仍被剧本引用：{'、'.join(sorted(users))}", 409, "asset_in_use")
        save_story_asset_catalog(
            StoryAssetCatalog(
                schema_version=catalog.schema_version,
                assets=[entry for entry in catalog.assets if entry.id != asset_id],
            ),
            story_asset_catalog_path,
        )
        load_story_catalog.cache_clear()
        return jsonify({"code": 0, "message": "已删除"})

    @blueprint.get("/api/red-leaf-town/admin/players")
    async def admin_player_search():
        if not _is_admin_request():
            return _admin_error()
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        players = get_service().admin_search_players(request.args.get("q", ""), limit)
        return jsonify({"code": 0, "data": players})

    @blueprint.post("/api/red-leaf-town/admin/players/<string:player_id>/partners")
    async def admin_player_grant_partner(player_id: str):
        if not _is_admin_request():
            return _admin_error()
        payload = await request.get_json(silent=True) or {}
        result = get_service().admin_grant_partner(player_id, str(payload.get("partner_id", "")))
        return jsonify({"code": 0, "data": result})

    @blueprint.post("/api/red-leaf-town/admin/players/<string:player_id>/resources")
    async def admin_player_grant_resources(player_id: str):
        if not _is_admin_request():
            return _admin_error()
        payload = await request.get_json(silent=True) or {}
        result = get_service().admin_grant_resources(
            player_id,
            payload.get("coins", 0),
            payload.get("experience", 0),
            payload.get("maple_flame", 0),
        )
        return jsonify({"code": 0, "data": result, "message": "资源已发放"})

    @blueprint.delete("/api/red-leaf-town/admin/players/<string:player_id>")
    async def admin_player_delete(player_id: str):
        if not _is_admin_request():
            return _admin_error()
        result = get_service().admin_delete_player(player_id)
        return jsonify({"code": 0, "data": result, "message": "角色已删除"})

    @blueprint.get("/api/red-leaf-town/admin/mail")
    async def admin_mail_list():
        if not _is_admin_request():
            return _admin_error()
        result = get_service().admin_list_mail(
            request.args.get("scope", "global"),
            request.args.get("player_id", ""),
        )
        return jsonify({"code": 0, "data": result})

    @blueprint.post("/api/red-leaf-town/admin/mail")
    async def admin_mail_send():
        if not _is_admin_request():
            return _admin_error()
        payload = await request.get_json(silent=True) or {}
        result = get_service().admin_send_mail(payload)
        return jsonify({"code": 0, "data": result, "message": "邮件已投递"})

    @blueprint.delete("/api/red-leaf-town/admin/mail/<string:mail_id>")
    async def admin_mail_delete(mail_id: str):
        if not _is_admin_request():
            return _admin_error()
        result = get_service().admin_delete_mail(mail_id, request.args.get("recipient_id", ""))
        return jsonify({"code": 0, "data": result, "message": "邮件已撤回"})

    @blueprint.get("/api/red-leaf-town/admin/redemption-codes")
    async def admin_redemption_code_list():
        if not _is_admin_request():
            return _admin_error()
        try:
            limit = int(request.args.get("limit", 200))
        except (TypeError, ValueError):
            limit = 200
        return jsonify({"code": 0, "data": get_service().list_redemption_codes(limit)})

    @blueprint.post("/api/red-leaf-town/admin/redemption-codes")
    async def admin_redemption_code_create():
        if not _is_admin_request():
            return _admin_error()
        payload = await request.get_json(silent=True) or {}
        try:
            count = int(payload.get("count", 0))
        except (TypeError, ValueError):
            return _error("生成数量必须是整数", 400, "invalid_count")
        codes = get_service().generate_redemption_codes(
            count,
            str(payload.get("batch", "")),
            str(payload.get("note", "")),
        )
        return jsonify({
            "code": 0,
            "data": {"codes": codes, "count": len(codes)},
            "message": f"已生成 {len(codes)} 个激活码",
        })

    @blueprint.get("/api/oauth/red-leaf-town/start")
    async def oauth_start():
        from private.libraries.df_oauth import OAuthError, build_authorize_url

        try:
            url = await build_authorize_url(
                "red_leaf_town",
                next_url=_safe_next(request.args.get("next", "")),
            )
        except OAuthError as exc:
            return _oauth_error(str(exc))
        return redirect(url)

    @blueprint.get("/api/oauth/red-leaf-town/callback")
    async def oauth_callback():
        from private.libraries.df_oauth import OAuthError, complete_login
        from private.libraries.jwt import AUD_RED_LEAF_TOWN, subject_encode

        if request.args.get("error"):
            description = request.args.get("error_description") or request.args.get("error")
            return _oauth_error(f"授权未完成：{description}")
        try:
            result = await complete_login(request.args.get("code", ""), request.args.get("state", ""))
        except OAuthError as exc:
            return _oauth_error(str(exc))
        if result.get("app") != "red_leaf_town":
            return _oauth_error("授权应用不匹配")
        subject = str(result.get("sub", "") or "").strip()
        if not subject:
            return _oauth_error("授权结果缺少账户标识")
        display_name = str(result.get("nickname") or result.get("username") or "红叶镇居民")
        get_service().ensure_player(subject, display_name)
        response = redirect(_safe_next(result.get("next", "")) or HOME_PATH)
        response.set_cookie(
            COOKIE_NAME,
            subject_encode(subject, AUD_RED_LEAF_TOWN),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            path="/",
        )
        return response

    @blueprint.post("/api/red-leaf-town/logout")
    async def logout():
        from private.libraries.df_oauth import LOGOUT_URL

        response = await make_response(jsonify({"code": 0, "message": "logged out", "data": {"logout_url": LOGOUT_URL}}))
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @blueprint.get("/api/red-leaf-town/account")
    @login_required
    async def account(subject: str):
        return jsonify({"code": 0, "data": _attach_cdn_urls(get_service().account(subject))})

    @blueprint.get("/api/red-leaf-town/state")
    @login_required
    async def state(subject: str):
        return jsonify({"code": 0, "data": _attach_cdn_urls(get_service().snapshot_by_sub(subject))})

    def serialize_upload(upload, subject: str) -> dict:
        """上传者的 oauth sub 不外发，别人只看得到昵称。"""
        return {
            "id": upload.id,
            "kind": upload.kind,
            "name": upload.name,
            "asset_key": upload.asset_key,
            "width": upload.width,
            "height": upload.height,
            "uploader_name": upload.uploader_name,
            "created_at": upload.created_at,
            "mine": upload.uploader_sub == subject,
            "layout": upload.layout.model_dump(),
        }

    def story_example() -> dict | None:
        """新用户第一次进编辑器时的起手草稿，用现成的那段全屏剧情当样板。"""
        try:
            scripts = story_catalog().scripts
        except (ValueError, ValidationError):
            return None
        staged = [script for script in scripts if script.mode == "stage"]
        script = next(iter(sorted(staged or scripts, key=lambda entry: -entry.priority)), None)
        if script is None:
            return None
        return {
            "id": f"demo_{script.id}"[:64],
            "title": f"{script.title}（示例）"[:64],
            "priority": script.priority,
            "steps": [step.model_dump() for step in script.steps],
        }

    def uploader_name(subject: str) -> str:
        player = get_service().repository.get_by_sub(subject)
        return player.display_name if player else ""

    @blueprint.get("/api/red-leaf-town/story/resources")
    @login_required
    async def story_resources(subject: str):
        """剧情编辑器要拿到素材库、可用作立绘的伙伴插画，以及大家传上来的图。只读。"""
        assets = load_story_asset_catalog(story_asset_catalog_path)
        partners = load_partner_catalog(catalog_path)
        uploads = load_story_upload_index(story_upload_index_path)
        now = int(time.time())
        return jsonify({
            "code": 0,
            "data": _attach_cdn_urls({
                "assets": [
                    {
                        "id": asset.id,
                        "kind": asset.kind,
                        "name": asset.name,
                        "asset_key": asset.asset_key,
                        "width": asset.width,
                        "height": asset.height,
                        "layouts": {
                            "inline": asset.inline_layout.model_dump(),
                            "stage": asset.stage_layout.model_dump(),
                        },
                    }
                    for asset in sorted(assets.assets, key=lambda entry: entry.id)
                    if not asset.pending
                ],
                "partners": [
                    {
                        "id": partner.id,
                        "name": partner.name,
                        "artworks": [
                            {
                                "breakthrough": artwork.breakthrough,
                                "asset_key": artwork.asset_key,
                                "width": artwork.width,
                                "height": artwork.height,
                            }
                            for artwork in sorted(partner.artworks, key=lambda entry: entry.breakthrough)
                        ],
                    }
                    for partner in sorted(partners.partners, key=lambda entry: entry.id)
                    if partner.artworks
                ],
                # 别人传的图不外发：玩家只看得到公共素材和自己传的。
                "uploads": [
                    serialize_upload(upload, subject)
                    for upload in sorted(uploads.uploads, key=lambda entry: -entry.created_at)
                    if upload.uploader_sub == subject
                ],
                "example": story_example(),
                "upload": {
                    "quota_bytes": upload_quota_bytes,
                    "used_bytes": used_quota(uploads, subject, now),
                    "window_seconds": UPLOAD_WINDOW_SECONDS,
                    "max_file_bytes": MAX_STORY_ASSET_SIZE,
                },
            }),
        })

    @blueprint.post("/api/red-leaf-town/story/shares")
    @login_required
    async def story_share_create(subject: str):
        if len(await request.get_data()) > 512 * 1024:
            return _error("剧本文件过大", 413, "story_too_large")
        raw = await request.get_json(silent=True)
        try:
            script = build_share(
                raw, subject,
                load_story_asset_catalog(story_asset_catalog_path),
                load_partner_catalog(catalog_path),
                load_story_upload_index(story_upload_index_path),
            )
        except (ValueError, ValidationError) as exc:
            return _error(f"无法分享剧本：{exc}", 400, "invalid_story_share")
        share_id = save_share(script, story_upload_index_path.parent / "story_shares")
        return jsonify({"code": 0, "data": {"id": share_id}}), 201

    @blueprint.get("/api/red-leaf-town/story/shares/<string:share_id>")
    async def story_share_read(share_id: str):
        script = load_share(share_id, story_upload_index_path.parent / "story_shares")
        if script is None:
            return _error("分享的剧情不存在", 404, "story_share_not_found")
        return jsonify({"code": 0, "data": _attach_cdn_urls(script)})

    @blueprint.post("/api/red-leaf-town/story/uploads")
    @login_required
    async def story_upload(subject: str):
        """玩家自己给剧情传图。转码成 WebP 推到 CDN，本地只留索引和上传者记录。"""
        form = await request.form
        kind = str(form.get("kind", "")).strip()
        if kind not in STORY_ASSET_KIND_NAMES:
            return _error("素材类型必须是背景或立绘", 400, "invalid_asset_kind")
        files = await request.files
        uploaded = files.get("file")
        if uploaded is None or not uploaded.filename:
            return _error("未选择图片", 400, "file_required")
        data = uploaded.read()
        if not data:
            return _error("图片内容为空", 400, "empty_file")
        if len(data) > MAX_STORY_ASSET_SIZE:
            return _error("单张图片不能超过 12 MB", 413, "file_too_large")

        now = int(time.time())
        index = load_story_upload_index(story_upload_index_path)
        used = used_quota(index, subject, now)
        if used + len(data) > upload_quota_bytes:
            remaining = max(0, upload_quota_bytes - used)
            return _error(
                f"一小时内最多上传 {upload_quota_bytes // (1024 * 1024)} MB，"
                f"现在还剩 {remaining // (1024 * 1024)} MB，过一会再试",
                429,
                "upload_quota_exceeded",
            )

        try:
            webp_data, width, height = _prepare_story_image(data, kind)
        except ValueError as exc:
            return _error(str(exc), 400, "invalid_image")
        except Exception:
            return _error("无法识别图片内容", 400, "invalid_image")

        upload_id = new_upload_id()
        digest = hashlib.sha256(webp_data).hexdigest()[:16]
        try:
            upload = StoryUpload(
                id=upload_id,
                kind=kind,
                name=str(form.get("name", "")).strip()[:64] or uploaded.filename.rsplit(".", 1)[0][:64] or upload_id,
                asset_key=f"red-leaf-town/story/community/{kind}/{upload_id}-{digest}.webp",
                width=width,
                height=height,
                content_type="image/webp",
                uploader_sub=subject,
                uploader_name=uploader_name(subject),
                source_bytes=len(data),
                created_at=now,
            )
        except ValidationError as exc:
            return _validation_error(exc)
        from src.libraries import cdn_client

        if not cdn_client.upload_bytes_at(upload.asset_key, webp_data, upload.content_type):
            return _error("CDN 上传失败，请稍后再试", 503, "cdn_upload_failed")
        # 配额在写入时再核一次，并发同时传也不会超额。
        if not append_story_upload(upload, story_upload_index_path, quota_bytes=upload_quota_bytes):
            return _error("一小时内的上传额度已经用完，过一会再试", 429, "upload_quota_exceeded")
        return jsonify({"code": 0, "data": _attach_cdn_urls(serialize_upload(upload, subject))}), 201

    @blueprint.patch("/api/red-leaf-town/story/uploads/<string:upload_id>")
    @login_required
    async def story_upload_update(subject: str, upload_id: str):
        """改自己传的图的名称和默认站位。别人的图连改带删都碰不到。"""
        body = await request.get_json(silent=True) or {}
        changes = {field: body[field] for field in ("name", "layout") if field in body}
        if not changes:
            return _error("没有要修改的内容", 400, "nothing_to_update")
        try:
            updated = update_story_upload(upload_id, subject, story_upload_index_path, **changes)
        except ValidationError as exc:
            return _validation_error(exc)
        if updated is None:
            return _error("这张图不存在，或者不是你传的", 404, "upload_not_found")
        return jsonify({"code": 0, "data": _attach_cdn_urls(serialize_upload(updated, subject))})

    @blueprint.delete("/api/red-leaf-town/story/uploads/<string:upload_id>")
    @login_required
    async def story_upload_delete(subject: str, upload_id: str):
        index = load_story_upload_index(story_upload_index_path)
        upload = index.upload_map.get(upload_id)
        if upload is None:
            return _error("这张图不存在", 404, "upload_not_found")
        if upload.uploader_sub != subject:
            return _error("只能删自己传的图", 403, "not_your_upload")
        remove_story_upload(upload_id, story_upload_index_path)
        return jsonify({"code": 0, "message": "已删除"})

    @blueprint.post("/api/red-leaf-town/shop/buy")
    @login_required
    async def buy(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().buy(
            subject,
            str(payload.get("shop_id", "")),
            int(payload.get("quantity", 1)),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/plots/<int:slot>/plant")
    @login_required
    async def plant(subject: str, slot: int):
        payload = await request.get_json(silent=True) or {}
        result = get_service().plant(
            subject,
            slot,
            str(payload.get("crop_id", "")),
            str(payload.get("task_item_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.put("/api/red-leaf-town/plots/<int:slot>/partners")
    @login_required
    async def assign_plot_partner(subject: str, slot: int):
        payload = await request.get_json(silent=True) or {}
        result = get_service().assign_partner(subject, slot, str(payload.get("partner_id", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/plots/<int:slot>/harvest")
    @login_required
    async def harvest(subject: str, slot: int):
        result = get_service().harvest(subject, slot)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.put("/api/red-leaf-town/gathering/sites/<string:site_id>/partner")
    @login_required
    async def assign_gathering_partner(subject: str, site_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().assign_gathering_partner(
            subject,
            site_id,
            str(payload.get("partner_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/gathering/sites/<string:site_id>/start")
    @login_required
    async def start_gathering(subject: str, site_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().start_gathering(
            subject,
            site_id,
            str(payload.get("task_id", "")),
            str(payload.get("task_item_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/gathering/sites/<string:site_id>/collect")
    @login_required
    async def collect_gathering(subject: str, site_id: str):
        result = get_service().collect_gathering(subject, site_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/sailing/build")
    @login_required
    async def build_sailing_ship(subject: str):
        result = get_service().build_sailing_ship(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/sailing/start")
    @login_required
    async def start_sailing(subject: str):
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise GameError("invalid_payload", "请求必须为 JSON 对象")
        result = get_service().start_sailing(
            subject, payload.get("route_id"), payload.get("partner_ids"),
            payload.get("supply_id", "none"), payload.get("request_id"),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/sailing/cancel")
    @login_required
    async def cancel_sailing(subject: str):
        payload = await request.get_json(silent=True)
        if (not isinstance(payload, dict) or not isinstance(payload.get("run_id"), str)
                or not payload["run_id"].strip()):
            raise GameError("invalid_payload", "请提供航行编号")
        result = get_service().cancel_sailing(subject, payload["run_id"])
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/sailing/collect")
    @login_required
    async def collect_sailing(subject: str):
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("run_id"), str):
            raise GameError("invalid_payload", "请提供航行编号")
        result = get_service().collect_sailing(subject, payload["run_id"])
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/sailing/upgrade")
    @login_required
    async def upgrade_sailing(subject: str):
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise GameError("invalid_payload", "请求必须为 JSON 对象")
        result = get_service().upgrade_sailing(subject, payload.get("kind"), payload.get("expected_level"))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/exploration/<string:expedition_id>/start")
    @login_required
    async def start_exploration(subject: str, expedition_id: str):
        payload = await request.get_json(silent=True) or {}
        partner_ids = payload.get("partner_ids") or []
        if not isinstance(partner_ids, list):
            partner_ids = []
        loadout = payload.get("loadout")
        carried_items = payload.get("carried_items")
        result = get_service().start_exploration(
            subject,
            expedition_id,
            [str(partner_id) for partner_id in partner_ids],
            str(payload.get("leader_partner_id", "")),
            loadout if isinstance(loadout, dict) else None,
            carried_items if isinstance(carried_items, list) else None,
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/exploration/current/resolve")
    @login_required
    async def resolve_exploration_event(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().resolve_exploration_event(
            subject,
            str(payload.get("choice_id", "")),
            str(payload.get("actor_partner_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/exploration/current/battle/act")
    @login_required
    async def resolve_delve_battle_action(subject: str):
        payload = await request.get_json(silent=True) or {}
        try:
            item_quality = int(payload.get("item_quality", 0) or 0)
        except (TypeError, ValueError):
            item_quality = 0
        result = get_service().resolve_delve_battle_action(
            subject,
            str(payload.get("action", "")),
            str(payload.get("target", "")),
            str(payload.get("item_id", "")),
            item_quality,
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/exploration/current/withdraw")
    @login_required
    async def withdraw_exploration(subject: str):
        result = get_service().withdraw_exploration(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.put("/api/red-leaf-town/crafting/stations/<string:station_id>/partner")
    @login_required
    async def assign_crafting_partner(subject: str, station_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().assign_crafting_partner(
            subject,
            station_id,
            str(payload.get("partner_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/crafting/stations/<string:station_id>/start")
    @login_required
    async def start_crafting(subject: str, station_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().start_crafting(
            subject,
            station_id,
            str(payload.get("recipe_id", "")),
            str(payload.get("task_item_id", "")),
            quantity=payload.get("quantity", 1),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/crafting/stations/<string:station_id>/collect")
    @login_required
    async def collect_crafting(subject: str, station_id: str):
        result = get_service().collect_crafting(subject, station_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.put("/api/red-leaf-town/mining/sites/<string:site_id>/partner")
    @login_required
    async def assign_mining_partner(subject: str, site_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().assign_mining_partner(
            subject,
            site_id,
            str(payload.get("partner_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/mining/sites/<string:site_id>/start")
    @login_required
    async def start_mining(subject: str, site_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().start_mining(
            subject,
            site_id,
            str(payload.get("task_id", "")),
            str(payload.get("task_item_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/mining/sites/<string:site_id>/collect")
    @login_required
    async def collect_mining(subject: str, site_id: str):
        result = get_service().collect_mining(subject, site_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.put("/api/red-leaf-town/fishing/companion")
    @login_required
    async def assign_fishing_companion(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().assign_fishing_companion(subject, str(payload.get("partner_id", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/fishing/spots/<string:spot_id>/cast")
    @login_required
    async def cast_line(subject: str, spot_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().cast_line(subject, spot_id, str(payload.get("request_id", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/fishing/big-catch")
    @login_required
    async def resolve_big_catch(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().resolve_big_catch(subject, str(payload.get("action", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/facilities/<string:upgrade_id>")
    @login_required
    async def upgrade_facility(subject: str, upgrade_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().upgrade_facility(subject, upgrade_id, str(payload.get("partner_id", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.put("/api/red-leaf-town/refining/partner")
    @login_required
    async def assign_refining_partner(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().assign_refining_partner(subject, str(payload.get("partner_id", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/refining/<int:slot>/start")
    @login_required
    async def start_refining(subject: str, slot: int):
        payload = await request.get_json(silent=True) or {}
        result = get_service().start_refining(subject, slot, str(payload.get("recipe_id", "")), payload.get("quality"))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/refining/<int:slot>/finish")
    @login_required
    async def finish_refining(subject: str, slot: int):
        payload = await request.get_json(silent=True) or {}
        result = get_service().finish_refining(subject, slot, str(payload.get("task_id", "")), payload.get("cancel") is True)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/ponds/<string:pond_id>/build")
    @login_required
    async def build_pond(subject: str, pond_id: str):
        result = get_service().build_pond(subject, pond_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.put("/api/red-leaf-town/ponds/<string:pond_id>/partner")
    @login_required
    async def assign_pond_partner(subject: str, pond_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().assign_pond_partner(subject, pond_id, str(payload.get("partner_id", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/ponds/<string:pond_id>/stock")
    @login_required
    async def stock_pond(subject: str, pond_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().stock_pond(
            subject,
            pond_id,
            str(payload.get("species_id", "")),
            int(payload.get("quantity", 1)),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/ponds/<string:pond_id>/harvest")
    @login_required
    async def harvest_pond(subject: str, pond_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().harvest_pond(subject, pond_id, int(payload.get("quantity", 1)))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/livestock/facilities/<string:facility_id>/build")
    @login_required
    async def build_livestock_facility(subject: str, facility_id: str):
        result = get_service().build_livestock_facility(subject, facility_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/livestock/facilities/<string:facility_id>/upgrade")
    @login_required
    async def upgrade_livestock_facility(subject: str, facility_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().upgrade_livestock_facility(subject, facility_id, payload.get("target_tier"))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.put("/api/red-leaf-town/livestock/facilities/<string:facility_id>/partner")
    @login_required
    async def assign_livestock_partner(subject: str, facility_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().assign_livestock_partner(
            subject,
            facility_id,
            str(payload.get("partner_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/livestock/facilities/<string:facility_id>/buy")
    @login_required
    async def buy_animal(subject: str, facility_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().buy_animal(
            subject,
            facility_id,
            str(payload.get("species_id", "")),
            str(payload.get("nickname", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/livestock/facilities/<string:facility_id>/collect")
    @login_required
    async def collect_livestock(subject: str, facility_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().collect_livestock(subject, facility_id, str(payload.get("animal_id", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/livestock/facilities/<string:facility_id>/incubate")
    @login_required
    async def incubate_egg(subject: str, facility_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().incubate_egg(
            subject,
            facility_id,
            int(payload.get("quality", 0)),
            str(payload.get("nickname", "")),
            str(payload.get("species_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/livestock/facilities/<string:facility_id>/breed")
    @login_required
    async def breed_animals(subject: str, facility_id: str):
        payload = await request.get_json(silent=True) or {}
        parents = payload.get("parent_ids") or []
        if not isinstance(parents, list):
            parents = []
        result = get_service().breed_animals(
            subject,
            facility_id,
            [str(entry) for entry in parents],
            str(payload.get("nickname", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/livestock/animals/<string:animal_id>/care")
    @login_required
    async def care_animal(subject: str, animal_id: str):
        result = get_service().care_animal(subject, animal_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/livestock/animals/<string:animal_id>/sell")
    @login_required
    async def sell_animal(subject: str, animal_id: str):
        result = get_service().sell_animal(subject, animal_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/feed-slot/deposit")
    @login_required
    async def deposit_feed(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().deposit_feed(
            subject,
            str(payload.get("item_id", "")),
            int(payload.get("quality", 0)),
            int(payload.get("count", 1)),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/feed-slot/dump")
    @login_required
    async def dump_feed(subject: str):
        result = get_service().dump_feed(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/summer/rewards/<int:points>/claim")
    @login_required
    async def claim_summer_reward(subject: str, points: int):
        result = get_service().claim_summer_reward(subject, points)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/gacha/convert")
    @login_required
    async def convert_gacha_currency(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().convert_maple_flame(subject, int(payload.get("quantity", 1)))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/achievements/<string:achievement_id>/claim")
    @login_required
    async def claim_achievement(subject: str, achievement_id: str):
        result = get_service().claim_achievement(subject, achievement_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/achievements/claim-all")
    @login_required
    async def claim_all_achievements(subject: str):
        result = get_service().claim_all_achievements(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/gacha/pull")
    @login_required
    async def gacha_pull(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().recruit(
            subject,
            int(payload.get("count", 1)),
            str(payload.get("request_id", "")),
            str(payload.get("pool_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/partners/<string:partner_id>/train")
    @login_required
    async def train_partner(subject: str, partner_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().train_partner(
            subject,
            partner_id,
            str(payload.get("item_id", "")),
            int(payload.get("quantity", 1)),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/partners/<string:partner_id>/star-up")
    @login_required
    async def star_up_partner(subject: str, partner_id: str):
        result = get_service().star_up_partner(subject, partner_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/partners/<string:partner_id>/artwork")
    @login_required
    async def select_partner_artwork(subject: str, partner_id: str):
        payload = await request.get_json(silent=True) or {}
        stage = payload.get("artwork_stage") if isinstance(payload, dict) else None
        result = get_service().select_partner_artwork(subject, partner_id, stage)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/partners/<string:partner_id>/breakthrough")
    @login_required
    async def breakthrough_partner(subject: str, partner_id: str):
        result = get_service().breakthrough_partner(subject, partner_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/tasks/use-item")
    @login_required
    async def use_active_task_item(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().use_active_task_item(
            subject,
            str(payload.get("industry", "")),
            str(payload.get("slot_id", "")),
            str(payload.get("task_item_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/tasks/cancel")
    @login_required
    async def cancel_task(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().cancel_task(
            subject,
            str(payload.get("industry", "")),
            str(payload.get("slot_id", "")),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/talents/<string:node_id>/unlock")
    @login_required
    async def unlock_talent(subject: str, node_id: str):
        result = get_service().unlock_talent(subject, node_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/inventory/<string:item_id>/sell")
    @login_required
    async def sell(subject: str, item_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().sell(
            subject,
            item_id,
            int(payload.get("quantity", 1)),
            int(payload.get("quality", 0)),
        )
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.get("/api/red-leaf-town/partner-select/candidates")
    @login_required
    async def partner_select_candidates(subject: str):
        result = get_service().partner_select_candidates(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/inventory/<string:item_id>/use")
    @login_required
    async def use_inventory_item(subject: str, item_id: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().use_partner_select_item(subject, item_id, str(payload.get("partner_id", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/monthly-card/redeem")
    @login_required
    async def redeem_monthly_card(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().redeem_code(subject, str(payload.get("code", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result), "message": "月卡已激活"})

    @blueprint.post("/api/red-leaf-town/monthly-card/claim")
    @login_required
    async def claim_monthly_card(subject: str):
        result = get_service().claim_monthly_card(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/stamina/potion")
    @login_required
    async def use_stamina_potion(subject: str):
        result = get_service().use_stamina_potion(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/stamina/purchase")
    @login_required
    async def buy_stamina(subject: str):
        result = get_service().buy_stamina(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/portals/<string:portal_id>/tributes/<string:tribute_id>/deliver")
    @login_required
    async def deliver_tribute(subject: str, portal_id: str, tribute_id: str):
        payload = await request.get_json(silent=True) or {}
        try:
            quantity = int(payload.get("quantity", 1))
        except (TypeError, ValueError):
            return _error("交付数量必须是整数", 400, "invalid_quantity")
        result = get_service().deliver_tribute(subject, portal_id, tribute_id, quantity)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.get("/api/red-leaf-town/commissions/board")
    @login_required
    async def commission_board(subject: str):
        result = get_service().commission_board_snapshot(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/commissions/submit")
    @login_required
    async def submit_commission(subject: str):
        result = get_service().submit_commission(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/commissions/forward")
    @login_required
    async def forward_commission(subject: str):
        result = get_service().forward_commission(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/commissions/withdraw")
    @login_required
    async def withdraw_commission(subject: str):
        result = get_service().withdraw_commission(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/commissions/<string:commission_id>/take")
    @login_required
    async def take_commission(subject: str, commission_id: str):
        result = get_service().take_commission(subject, commission_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.get("/api/red-leaf-town/mail")
    @login_required
    async def mailbox(subject: str):
        result = get_service().mailbox(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/mail/<string:mail_id>/read")
    @login_required
    async def read_mail(subject: str, mail_id: str):
        result = get_service().read_mail(subject, mail_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/mail/<string:mail_id>/claim")
    @login_required
    async def claim_mail(subject: str, mail_id: str):
        result = get_service().claim_mail(subject, mail_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.get("/api/red-leaf-town/crossover")
    @login_required
    async def crossover_campaigns(subject: str):
        result = get_service().crossover_campaigns(subject)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/crossover/<string:campaign_id>/claim")
    @login_required
    async def claim_crossover(subject: str, campaign_id: str):
        result = get_service().claim_crossover(subject, campaign_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/story/cue")
    @login_required
    async def story_cue(subject: str):
        payload = await request.get_json(silent=True) or {}
        result = get_service().story_cue(subject, str(payload.get("cue", "")))
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/story/<string:story_id>/seen")
    @login_required
    async def story_seen(subject: str, story_id: str):
        result = get_service().mark_story_seen(subject, story_id)
        return jsonify({"code": 0, "data": _attach_cdn_urls(result)})

    @blueprint.post("/api/red-leaf-town/account/binding-code")
    @login_required
    async def binding_code(subject: str):
        code = get_service().create_binding_code(subject)
        return jsonify({
            "code": 0,
            "data": {
                "binding_code": code,
                "command": f"绑定红叶镇 {code}",
                "expires_in": 600,
            },
        })

    async def _serve_frontend(dist: Path, asset_path: str):
        if asset_path:
            candidate = dist / asset_path
            if candidate.is_file():
                return await send_from_directory(dist, asset_path)
        index = dist / "index.html"
        if not index.is_file():
            return _error("前端尚未构建", 503, "frontend_not_built")
        return await send_from_directory(dist, "index.html")

    @blueprint.get("/red-leaf-town/")
    @blueprint.get("/red-leaf-town/<path:asset_path>")
    async def frontend(asset_path: str = ""):
        return await _serve_frontend(FRONTEND_DIST, asset_path)

    @blueprint.get("/red-leaf-town-beta/")
    @blueprint.get("/red-leaf-town-beta/<path:asset_path>")
    async def frontend_beta(asset_path: str = ""):
        return await _serve_frontend(FRONTEND_DIST_BETA, asset_path)

    return blueprint


def install(app) -> None:
    if app.extensions.get("red_leaf_town_installed"):
        return
    app.register_blueprint(create_blueprint())
    app.extensions["red_leaf_town_installed"] = True
