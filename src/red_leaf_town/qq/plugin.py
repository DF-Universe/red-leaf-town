from __future__ import annotations

from game_host.config import PUBLIC_ORIGIN

import secrets

from nonebot import get_driver, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot_plugin_alconna import UniMessage

from red_leaf_town.application import GameError
from red_leaf_town.domain import QQIdentity
from red_leaf_town.runtime import get_service
from game_host.plugin_manager import plugin_manager


PLUGIN_META = {
    "name": "红叶镇物语",
    "enable": False,
    "help_text": """【红叶镇物语】
  红叶镇                  — 查看农场摘要
  红叶镇招募 [1/10]       — 使用引路枫叶招募伙伴
  红叶镇成就              — 查看成就进度
  红叶镇委托              — 查看今日委托和公共转发池
  红叶镇交委托            — 交付今日委托
  绑定红叶镇 [绑定码]     — 绑定水鱼账号中的红叶镇角色

请先在 Web 端使用水鱼账号登录。""",
}

plugin_manager.register_plugin(PLUGIN_META)


def resolve_identity(bot: Bot, event: Event) -> QQIdentity | None:
    if str(bot.type) != "QQ" or str(bot.self_id) != str(get_driver().config.aether_bot):
        return None
    subject = (
        getattr(getattr(event, "author", None), "id", None)
        or getattr(event, "user_id", None)
        or getattr(getattr(event, "data", None), "sender_id", None)
    )
    if not subject:
        return None
    return QQIdentity(platform=str(bot.type), bot_id=str(bot.self_id), subject=str(subject))


def _reply(event: Event, text: str) -> UniMessage:
    # Milky 的 message_id 是 int，官方 QQ 的 id 是 str。alconna 导出引用段时会对它做
    # split("@")，拿到 int 会直接抛 AttributeError，把整条回复吞掉——这里统一转成字符串。
    message_id = getattr(event, "message_id", None)
    if message_id is None:
        message_id = getattr(event, "id", None)
    prefix = UniMessage.reply(str(message_id)) if message_id is not None else UniMessage()
    return prefix + UniMessage.text(text)


bind_command = on_command("绑定红叶镇", aliases={"红叶镇绑定"}, force_whitespace=True)


@bind_command.handle()
async def bind_player(bot: Bot, event: Event, message: Message = CommandArg()):
    code = message.extract_plain_text().strip().upper()
    if not code:
        await _reply(event, "请先在红叶镇 Web 页面生成绑定码，再发送『绑定红叶镇 绑定码』。").send()
        return
    identity = resolve_identity(bot, event)
    if not identity:
        await _reply(event, "无法识别当前 QQ 身份，请稍后再试。").send()
        return
    try:
        player = get_service().bind_identity(code, identity)
    except GameError as exc:
        await _reply(event, exc.message).send()
        return
    await _reply(event, f"绑定成功，欢迎回来，{player.display_name}！").send()


summary_command = on_command("红叶镇", force_whitespace=True)
recruit_command = on_command("红叶镇招募", aliases={"红叶镇抽卡"}, force_whitespace=True)
commission_command = on_command("红叶镇委托", aliases={"红叶镇今日委托"}, force_whitespace=True)
commission_submit_command = on_command("红叶镇交委托", aliases={"红叶镇提交委托"}, force_whitespace=True)
achievement_command = on_command("红叶镇成就", force_whitespace=True)


@recruit_command.handle()
async def recruit_partner(bot: Bot, event: Event, message: Message = CommandArg()):
    identity = resolve_identity(bot, event)
    if not identity:
        await _reply(event, "无法识别当前 QQ 身份。").send()
        return
    raw = message.extract_plain_text().strip() or "1"
    if raw not in {"1", "10"}:
        await _reply(event, "招募次数只能是 1 或 10。").send()
        return
    service = get_service()
    try:
        result = service.recruit_by_identity(
            identity,
            int(raw),
            f"qq-{secrets.token_hex(8)}",
            "standard-1",
        )
    except GameError as exc:
        await _reply(event, exc.message).send()
        return
    pool_state = next(
        (pool for pool in result["state"]["gacha_pools"] if pool["pool_id"] == "standard-1"),
        {"catalog": [], "task_items": []},
    )
    catalog = {entry["partner_id"]: entry for entry in pool_state["catalog"]}
    task_items = {entry["id"]: entry for entry in pool_state["task_items"]}
    lines = []
    for drop in result["result"]["results"]:
        if drop["kind"] == "partner":
            name = catalog.get(drop["content_id"], {}).get("name", drop["content_id"])
            suffix = f"（重复，同行印记 +{drop['companion_marks']}）" if drop["duplicate"] else "（新伙伴）"
            lines.append(f"{'★' * drop['rarity']} {name}{suffix}")
        else:
            name = task_items.get(drop["content_id"], {}).get("name", drop["content_id"])
            lines.append(f"◆ {name} ×{drop['quantity']}")
    await _reply(event, "招募结果：\n" + "\n".join(lines)).send()


@summary_command.handle()
async def farm_summary(bot: Bot, event: Event):
    identity = resolve_identity(bot, event)
    if not identity:
        await _reply(event, "无法识别当前 QQ 身份。").send()
        return
    try:
        state = get_service().snapshot_by_identity(identity)
    except GameError:
        await _reply(event, "尚未绑定角色。请先登录 Web 端，在账户页生成绑定码。").send()
        return
    player = state["player"]
    ready = sum(1 for plot in state["plots"] if plot["ready"])
    growing = sum(1 for plot in state["plots"] if not plot["empty"] and not plot["ready"])
    working_partners = sum(1 for partner in state["partners"] if partner["locked"])
    gathering_ready = sum(1 for site in state.get("gathering_sites", []) if site["ready"])
    gathering_running = sum(
        1 for site in state.get("gathering_sites", []) if not site["empty"] and not site["ready"]
    )
    crafting_ready = sum(1 for station in state.get("crafting_stations", []) if station["ready"])
    crafting_running = sum(
        1 for station in state.get("crafting_stations", []) if not station["empty"] and not station["ready"]
    )
    mining_ready = sum(1 for site in state.get("mining_sites", []) if site["ready"])
    mining_running = sum(
        1 for site in state.get("mining_sites", []) if not site["empty"] and not site["ready"]
    )
    aquatic = state.get("aquatic") or {}
    ponds = aquatic.get("ponds") or []
    portals = state.get("portals", [])
    portals_opened = sum(1 for portal in portals if portal["completed"])
    portals_active = sum(1 for portal in portals if portal["unlocked"] and not portal["completed"])
    await _reply(
        event,
        f"🍁 {player['display_name']} · Lv.{player['level']}\n"
        f"金币：{player['coins']}\n"
        f"枫火：{player['maple_flame']} · 引路枫叶：{player['guide_leaves']} · 同行印记：{player['companion_marks']}\n"
        f"体力：{player['stamina']}/{player['stamina_cap']}\n"
        f"伙伴：{state['partner_count']} 位，{working_partners} 位任务中\n"
        f"农田：{ready} 块可收获，{growing} 块生长中\n"
        f"采集：{gathering_ready} 处可领取，{gathering_running} 处进行中\n"
        f"加工：{crafting_ready} 件可领取，{crafting_running} 件制作中\n"
        f"矿产：{mining_ready} 处可收取，{mining_running} 处开采中\n"
        + _pond_line(aquatic, ponds)
        + f"传送门：已开启 {portals_opened} 座，{portals_active} 座待交贡品\n"
        + "\n".join(_commission_lines(state))
        + f"\n前往 Web 页面管理农场：{PUBLIC_ORIGIN}/red-leaf-town/",
    ).send()


def _pond_line(aquatic: dict, ponds: list) -> str:
    """鱼塘要到 10 级才有，没开塘的玩家这一行整条不显示。"""
    if not ponds:
        return ""
    stock = sum(pond.get("stock", 0) for pond in ponds)
    fry = sum(pond.get("fry_total", 0) for pond in ponds)
    capacity = sum(pond.get("capacity", 0) for pond in ponds)
    feed_slot = aquatic.get("feed_slot") or {}
    stalled = any(pond.get("stalled") for pond in ponds)
    feed = "饲料已空，鱼塘停摆" if stalled else f"饲料 {int(feed_slot.get('units', 0))} 份"
    fry_text = f"，鱼苗 {fry} 尾" if fry else ""
    return f"鱼塘：成鱼 {stock}/{capacity} 尾{fry_text}，{feed}\n"


def _commission_lines(state: dict) -> list[str]:
    commissions = state.get("commissions") or {}
    if not commissions.get("unlocked"):
        return [f"委托：居民等级达到 {commissions.get('min_level', 1)} 级后开放"]
    today = commissions.get("commission")
    if not today:
        return ["委托：今天没有人来找你"]
    mark = "🌟 幸运日 " if today["lucky"] else ""
    item = (today.get("item") or {}).get("name") or today["item_id"]
    status = {
        "open": f"进度 {today['owned']}/{today['quantity']}",
        "forwarded": "已转发，等人接手",
        "completed": "已交付",
        "forward_completed": f"{today['completed_by_name']} 替你完成了",
    }[today["status"]]
    return [
        f"{mark}{today['npc_title']}{today['npc_name']}：{item} ×{today['quantity']}",
        f"报酬 {today['reward_maple_flame']} 枫火 · {status}",
    ]


@achievement_command.handle()
async def achievement_summary(bot: Bot, event: Event):
    identity = resolve_identity(bot, event)
    if not identity:
        await _reply(event, "无法识别当前 QQ 身份。").send()
        return
    try:
        state = get_service().snapshot_by_identity(identity)
    except GameError as exc:
        await _reply(event, exc.message).send()
        return
    achievements = state["achievements"]
    tier_names = {"blue": "蓝色", "purple": "紫色", "gold": "金色"}
    lines = [
        f"🏆 红叶镇成就 {achievements['completed']}/{achievements['total']}",
        f"已领取：{achievements['maple_flame_earned']} 枫火 · 待领取：{achievements['claimable_maple_flame']} 枫火",
    ]
    for tier, name in tier_names.items():
        entries = [entry for entry in achievements["entries"] if entry["tier"] == tier]
        completed = sum(1 for entry in entries if entry["completed"])
        lines.append(f"{name}：{completed}/{len(entries)}")
    recent = sorted(
        (entry for entry in achievements["entries"] if entry["completed"]),
        key=lambda entry: entry["completed_at"] or 0,
        reverse=True,
    )[:3]
    if recent:
        lines.append("最近达成：" + "、".join(entry["name"] for entry in recent))
    lines.append(f"请前往 Web 成就册领取奖励：{PUBLIC_ORIGIN}/red-leaf-town/")
    await _reply(event, "\n".join(lines)).send()


@commission_command.handle()
async def commission_summary(bot: Bot, event: Event):
    identity = resolve_identity(bot, event)
    if not identity:
        await _reply(event, "无法识别当前 QQ 身份。").send()
        return
    service = get_service()
    try:
        state = service.snapshot_by_identity(identity)
        board = service.commission_board_by_identity(identity)
    except GameError as exc:
        await _reply(event, exc.message).send()
        return
    lines = _commission_lines(state)
    lines.append(
        f"转发池：{len(board['entries'])} 条待接 · 今天还能接 "
        f"{board['remaining_takes']}/{board['daily_take_limit']} 单"
    )
    lines.append(f"接单和转发请到 Web 页面：{PUBLIC_ORIGIN}/red-leaf-town/commissions")
    await _reply(event, "🍁 今日委托\n" + "\n".join(lines)).send()


@commission_submit_command.handle()
async def commission_submit(bot: Bot, event: Event):
    identity = resolve_identity(bot, event)
    if not identity:
        await _reply(event, "无法识别当前 QQ 身份。").send()
        return
    try:
        result = get_service().submit_commission_by_identity(identity)["result"]
    except GameError as exc:
        await _reply(event, exc.message).send()
        return
    await _reply(
        event,
        f"委托交付完成，{result['npc_name']}收下了东西，你获得 {result['maple_flame']} 枫火。",
    ).send()


sailing_command = on_command("红叶镇出海", force_whitespace=True)


@sailing_command.handle()
async def sailing_summary(bot: Bot, event: Event):
    identity = resolve_identity(bot, event)
    if not identity:
        await _reply(event, "无法识别当前 QQ 身份。").send()
        return
    try:
        state = get_service().snapshot_by_identity(identity)
    except GameError as exc:
        await _reply(event, exc.message).send()
        return
    sailing = state.get("sailing")
    if not sailing:
        await _reply(event, "暂时无法获取出海状态，请稍后重试。").send()
        return
    if not sailing["unlocked"]:
        text = f"居民达到 {sailing['min_level']} 级后可以出海。"
    elif not sailing["ship_built"]:
        construction = sailing['construction']
        materials = '＋'.join(
            f"{material['quantity']} 个{material['item_name']}（持有 {material['owned']}）"
            for material in construction['materials']
        )
        text = f"初帆号尚未建造，需要 {construction['coins']} 红叶币＋{materials}。"
    elif sailing["active_run"]:
        run = sailing["active_run"]
        remaining = max(0, run["ready_at"] - state["server_time"])
        status = "已回港，等待领取" if not remaining else f"预计 {(remaining + 59) // 60} 分钟后回港"
        text = f"初帆号 · {run['route_name']}\n{status}"
    else:
        text = f"初帆号已就绪，累计完成 {sailing['completed_voyages']} 次航行。"
    await _reply(event, text + f"\n安排出航与领取收获：\n{PUBLIC_ORIGIN}/red-leaf-town/aquatic?tab=sailing").send()
