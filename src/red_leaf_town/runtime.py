from __future__ import annotations

from red_leaf_town.application import GameService
from red_leaf_town.content import load_content

_service: GameService | None = None


def get_service() -> GameService:
    global _service
    if _service is None:
        from game_host.redis import redis_global

        from red_leaf_town.infrastructure import (
            RedisCommissionBoard,
            RedisMailbox,
            RedisPlayerRepository,
            RedisRedemptionCodes,
        )

        content = load_content()
        _service = GameService(
            content,
            RedisPlayerRepository(redis_global, content),
            commission_board=RedisCommissionBoard(redis_global),
            mailbox=RedisMailbox(redis_global),
            redemption_codes=RedisRedemptionCodes(redis_global),
        )
    return _service


def set_service(service: GameService | None) -> None:
    global _service
    _service = service
