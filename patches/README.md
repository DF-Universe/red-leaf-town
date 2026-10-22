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


# 盛夏应援场 · 补丁包

内容在 `data/game.json`（3 个敌人 + 1 条探索路线 + 1 把武器），本目录是配套的**运行时补丁**，
**不改动 `red_leaf_town` 包内任何文件**——与 PR #1 的 `patches/` 同一范式。

| 模块 | 职责 |
|---|---|
| `local_one_time_dungeon.py` | 一次性副本：免费入场（含 0 币）、首通后隐藏、首通补发 2 枚引路枫叶（幂等） |
| `local_boss_moves.py` | 首领大招：`enemy_turn` 上挂「周期型 / 二阶段型」全体重击，带预警与入场播报 |
| `local_guqi_weapon.py` | 「盛夏回响」专属效果：认主校验 + 点名顾祇SP 检定时改用敏捷 |

## 接线（应用装配层）

**一次性副本那个必须最早装**（它 patch `GameService.__init__`，装晚了实例不会被归零）：

```python
from patches.local_one_time_dungeon import install_one_time_dungeon
from patches.local_boss_moves import install_boss_moves
from patches.local_guqi_weapon import install_guqi_weapon

install_one_time_dungeon()   # ← 必须在 GameService(...) 之前
install_boss_moves()
install_guqi_weapon()
```

三个 `install()` 都幂等。

补丁里没有任何新路由，**不需要装配层注册接口**。

## 配置

- `ONE_TIME_DUNGEONS`：副本 id → 名称 / 首通引路枫叶数。加一条就多一个一次性副本。
- `BOSS_MOVES`：敌人 id → 节奏（`every`，二阶段型再加 `hp_ratio` / `enter_text`）、
  招式名、命中、伤害骰、文案（`flavor` / `lead` / `lead_again` / `telegraph`）。
- 武器的认主名单与专属效果在 `local_guqi_weapon.py` 顶部两个常量里。

## 内容（`data/game.json`）

| 位置 | 条目 |
|---|---|
| `items[]` | `spotlight_mic_blade` 盛夏回响（weapon / agility / 1d10 / 攻击+2 / 先攻+2） |
| `delve_enemies[]` | `midsummer_forum_king` 盛夏之吧主甲（HP110 / AC16 / 双动 / boss） |
| `delve_enemies[]` | `forum_king_fan_a` / `forum_king_fan_b` 吧主甲的暖粉A / 暖粉B（HP32 / AC13） |
| `exploration_expeditions[]` | `midsummer_appeal_ground` 盛夏应援场（Lv20 / 2 层 / 入场费 1000 但运行时归零） |

> 通关掉落的 `ribbed_berry_seed`（棱瓜果种子）是 main 里已有的物品（夏夜潮祭实装），
> 本 PR 只引用、不重复定义——`items[]` / `crops[]` / `commissions` 里都没有新增棱瓜果。
> 生成脚本会先检查 base 里有没有棱瓜果，没有就直接报错（防止拿旧 base 生成出悬空引用）。

副本节点 1「暖粉认证」两个选项：`i_am_a_fan` 智力 DC14 检定（过/不过都进第二层，只差体力），
`i_am_a_hater` 直接打两个暖粉（可逃，DC16）。节点 2 上台打首领。

> 为什么入场费不直接写 0：`ExplorationExpeditionDefinition.entry_fee` 的约束是 `gt=0`，
> 内容文件里写 0 会被内容校验拒掉。所以数据里保留一个合法正数，由补丁在加载后改成 0。

## 存档

- 首通登记：上游已有的 `achievement_stats.completed_expedition_ids` / `completed_delve_ids`（复用，不新增字段）。
- 一次性领取幂等：`crossover_claims["one_time_clear:<expedition_id>"]`。

皮肤解锁用的是同一张 claims 表，前缀不撞键，**存档 schema 零改动**。

## 盛夏回响·全服发放

武器通过后台邮件系统发放（`POST /api/red-leaf-town/admin/mail`），全服一封：

```json
{
  "scope": "global",
  "title": "盛夏回响",
  "sender": "红叶镇",
  "body": "应援台的灯还亮着。这把立麦留给你——下次上台，别怯场。",
  "attachments": {
    "items": [{ "item_id": "spotlight_mic_blade", "quantity": 1 }]
  }
}
```

`scope: "global"` 的信只投给 `registered_before`（缺省 = 发送时刻）之前注册的账号，
新注册玩家可在后台补发或改成首次登录绑定。附件结构走上游 `RewardDefinition`，
`items[]` 的 `item_id` 必须已在 `data/game.json` 里存在（本 PR 已加入）。
