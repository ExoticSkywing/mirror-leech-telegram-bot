from time import time
from typing import List, Tuple

from ...core.config_manager import Config
from ... import LOGGER
from pyrogram.errors import FloodWait, RPCError


_cache = {}


def _make_cache_key(user_id: int, channels: Tuple) -> str:
    return f"{user_id}:{','.join(map(str, channels))}"


def _is_exempt(user_id: int) -> bool:
    try:
        owners = {int(Config.OWNER_ID)} if Config.OWNER_ID else set()
        sudos = {int(x) for x in str(Config.SUDO_USERS).split() if str(x).lstrip('-').isdigit()}
        auth_chats = {int(x) for x in str(Config.AUTHORIZED_CHATS).split() if str(x).lstrip('-').isdigit()}
        return user_id in owners or user_id in sudos or user_id in auth_chats
    except Exception:
        return False


async def _check_single(client, channel, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(channel, user_id)
        status = getattr(member, "status", None)
        # Normalize Pyrogram v2 enums or strings
        try:
            if hasattr(status, "value"):
                status_val = str(status.value).lower()
            else:
                status_val = str(status).lower()
        except Exception:
            status_val = str(status).lower() if status is not None else ""
        return status_val in {"member", "administrator", "creator"}
    except FloodWait as f:
        LOGGER.warning(f"Membership check FloodWait: {int(f.value)}s")
        return False
    except RPCError as e:
        LOGGER.warning(f"Membership RPCError: {e}")
        return False
    except Exception as e:
        LOGGER.warning(f"Membership check error: {e}")
        return False


async def check_membership(client, user_id: int, use_cache: bool = True) -> bool:
    """Return True if user passes membership requirement, considering exemptions.

    Rules:
      - If channel check is disabled, always True
      - If exempt and PARSE_VIDEO_EXEMPT_CONFIG_AUTH=True, True
      - Otherwise check REQUIRED_CHANNELS with ANY/ALL logic
    """
    if not Config.PARSE_VIDEO_CHANNEL_CHECK_ENABLED:
        return True

    if Config.PARSE_VIDEO_EXEMPT_CONFIG_AUTH and _is_exempt(user_id):
        return True

    channels: List = Config.PARSE_VIDEO_REQUIRED_CHANNELS or []
    if not channels:
        # No requirement configured
        return True

    cache_key = _make_cache_key(user_id, tuple(channels))
    ttl = int(Config.PARSE_VIDEO_MEMBERSHIP_CACHE_TTL or 0)
    if use_cache and ttl > 0:
        cached = _cache.get(cache_key)
        if cached and time() - cached[0] < ttl:
            return cached[1]

    if bool(Config.PARSE_VIDEO_REQUIRE_ALL):
        ok = True
        for ch in channels:
            if not await _check_single(client, ch, user_id):
                ok = False
                break
    else:
        ok = False
        for ch in channels:
            if await _check_single(client, ch, user_id):
                ok = True
                break

    if ttl > 0:
        _cache[cache_key] = (time(), ok)
    return ok


