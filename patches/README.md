# 夏日限定皮肤兑换 · 补丁包

皮肤的数据在 `data/summer_skins.json`（内容层），本目录是配套的**运行时补丁**，
**不改动 `red_leaf_town` 包内任何文件**——与 PR #1 的 `patches/` 同一范式。

| 模块 | 职责 |
|---|---|
| `local_summer_shop.py` | 包 `SummerServiceMixin._summer_snapshot`：给 `state.summer_event` 追加 `skins` 数组（含逐项已持有量与 `can_claim`）；并挂 `claim_summer_skin` 方法 |
| `local_partner_skins.py` | 包 `GameService._partner_snapshots` / `select_partner_artwork`：已解锁皮肤以「伪阶段」并入 `available_artworks`，可像突破立绘一样滑动观看 |

## 接线（应用装配层）

在构造 `GameService` 之前（补丁打的是类方法，装晚了第一个请求会漏）：

```python
from patches.local_summer_shop import install_summer_shop
from patches.local_partner_skins import install_partner_skins

install_summer_shop()        # 默认读 <repo>/data/summer_skins.json
install_partner_skins()      # 必须在 install_summer_shop 之后
```

`install_summer_shop(path)` 可显式指定皮肤数据文件；两个 `install()` 均幂等。

## 路由（**patches/ 范式装不下**，需要装配层接管）

皮肤兑换需要一个新路由：

```
POST /web/api/red-leaf-town/summer/skins/<skin_id>/claim
```

`patches/` 的定义是「不碰 `src/`、只包方法」，所以这条路只能由应用装配层注册。
照抄上游 `web/routes.py` 的夏活领奖路由（`.../summer/rewards/<int:points>/claim`）的
登录校验与错误形状即可：

```python
@app.post("/web/api/red-leaf-town/summer/skins/<skin_id>/claim")
async def claim_summer_skin_route(skin_id: str):
    from quart import jsonify

    from red_leaf_town.application.service import GameError
    from red_leaf_town.runtime import get_service
    from red_leaf_town.web.routes import _attach_cdn_urls, _error, _session_subject

    subject = _session_subject()
    if not subject:
        return _error("未登录", 401, "unauthorized")
    try:
        result = get_service().claim_summer_skin(subject, skin_id)
    except GameError as exc:
        return _error(exc.message, exc.status, exc.code)
    return jsonify({"code": 0, "data": _attach_cdn_urls(result)})
```

> 备选：若更希望路由落在包内，在 `web/routes.py` 的夏活领奖路由旁加上述 6 行即可
> （那就不是「src 零改动」了，但与 PR #1 里被上游改写成原生代码的结局一致）。

## 存档

解锁记录复用上游字段 `PlayerState.crossover_claims`（`dict[str, int]`，旧存档自动补空）：

- 已解锁：`crossover_claims["summer_skin:<skin_id>"] = 时刻`
- 当前正在看哪张皮肤：`crossover_claims["summer_skin_view:<partner_id>"] = 伪阶段号`

上游 `crossover.py` 只按 `campaign_id` 精确 `get`，两个前缀不会撞键，**存档 schema 零改动**。

> 关键约束：`OwnedPartner.artwork_stage` 的约束是 `0..2`，皮肤**不能**塞进该字段（存档回读即校验失败）。
> 所以皮肤走「伪阶段」`breakthrough = 100 + 序号` 进 `available_artworks`，选中态另记 `crossover_claims`。

## 素材

两款皮肤立绘（936×1664 WebP）需要上传 CDN，object key 与 `data/summer_skins.json` 的
`asset_key` 一致：

```
red-leaf-town/partners/ai_xinyu/swimsuit-e184b47ed40eb9d3.webp   # 盛夏鳞光
red-leaf-town/partners/aishen/swimsuit-a22e53e4a26ba2d3.webp     # 雷鸣假日
```
