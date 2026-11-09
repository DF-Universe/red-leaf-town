from __future__ import annotations

from .models import PlayerState


class EconomyError(ValueError):
    pass


def add_item(player: PlayerState, item_id: str, amount: int, quality: int = 0) -> None:
    if amount <= 0:
        raise EconomyError("物品数量必须为正数")
    if quality < 0 or quality > 5:
        raise EconomyError("物品品质无效")
    qualities = player.inventory.setdefault(item_id, {})
    qualities[quality] = qualities.get(quality, 0) + amount


def remove_item(player: PlayerState, item_id: str, amount: int, quality: int = 0) -> None:
    if amount <= 0:
        raise EconomyError("物品数量必须为正数")
    qualities = player.inventory.get(item_id, {})
    owned = qualities.get(quality, 0)
    if owned < amount:
        raise EconomyError("物品数量不足")
    remaining = owned - amount
    if remaining:
        qualities[quality] = remaining
    else:
        qualities.pop(quality, None)
    if not qualities:
        player.inventory.pop(item_id, None)


def remove_item_any_quality(player: PlayerState, item_id: str, amount: int) -> None:
    """不挑品质地扣料：品质 0 先扣，不够再按品质升序往上补。

    给「种子」这类不关心品质的耗材用。采集点产出的物品必须带品质
    （`content.py` 的采集校验），所以像蜜薯藤苗这种既是采集产出、又要当种子
    扣的东西，会散落在品质 1~5 的格子里；`remove_item(..., quality=0)` 只认
    品质 0，就会误报「物品数量不足」。
    """
    if amount <= 0:
        raise EconomyError("物品数量必须为正数")
    qualities = player.inventory.get(item_id) or {}
    if sum(qualities.values()) < amount:
        raise EconomyError("物品数量不足")
    remaining = amount
    for quality in sorted(qualities):
        if remaining <= 0:
            break
        owned = int(qualities.get(quality) or 0)
        if owned <= 0:
            continue
        take = min(owned, remaining)
        left = owned - take
        if left:
            qualities[quality] = left
        else:
            qualities.pop(quality, None)
        remaining -= take
    if not qualities:
        player.inventory.pop(item_id, None)


def spend_coins(player: PlayerState, amount: int) -> None:
    if amount <= 0:
        raise EconomyError("金额必须为正数")
    if player.coins < amount:
        raise EconomyError("金币不足")
    player.coins -= amount


def grant_coins(player: PlayerState, amount: int) -> None:
    if amount <= 0:
        raise EconomyError("金额必须为正数")
    player.coins += amount
