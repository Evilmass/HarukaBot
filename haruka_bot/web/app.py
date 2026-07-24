import asyncio
import base64
import csv
import hashlib
import hmac
import io
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlsplit

import httpx
import nonebot
from bilireq.exceptions import ResponseCodeError
from bilireq.user import get_user_info
from bilireq.utils import get
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from nonebot.log import logger
from pydantic import BaseModel, Field

from ..config import plugin_config
from ..database import DB as db
from ..database.models import Guild, User
from ..utils import PROXIES, safe_send

WEB_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = WEB_ROOT / "static"
SESSION_COOKIE = "haruka_web_session"
CSRF_COOKIE = "haruka_web_csrf"
SESSION_VERSION = 1
LOGIN_WINDOW_SECONDS = 600
LOGIN_MAX_FAILURES = 5
LOGIN_MAX_TRACKED_IPS = 2048
BOT_CACHE_SECONDS = 30
ROOM_RESOLVE_TIMEOUT_SECONDS = 10
AVATAR_CACHE_SECONDS = 6 * 60 * 60
AVATAR_ERROR_CACHE_SECONDS = 5 * 60
AVATAR_BROWSER_CACHE_SECONDS = 60 * 60
AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_ALLOWED_HOSTS = ("hdslb.com", "biliimg.com")
AVATAR_ALLOWED_MEDIA_TYPES = (
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
)
WEB_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'"
)

router = APIRouter(prefix="/admin", tags=["HarukaBot Web"])
_login_failures: Dict[str, List[float]] = {}
_bot_cache: Dict[str, Any] = {"expires": 0.0, "value": None}
_bot_cache_lock = asyncio.Lock()
_avatar_cache: Dict[int, Dict[str, Any]] = {}
_avatar_tasks: Dict[int, "asyncio.Task[Optional[Tuple[bytes, str]]]"] = {}


class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=512)


class SubscriptionCreate(BaseModel):
    uid: Optional[int] = Field(None, gt=0)
    room_id: Optional[int] = Field(None, gt=0)
    target_type: Literal["group", "private", "guild"] = "group"
    target_id: Optional[int] = Field(None, gt=0)
    guild_id: Optional[str] = Field(None, max_length=100)
    channel_id: Optional[str] = Field(None, max_length=100)
    bot_id: int = Field(..., gt=0)
    live: bool = True
    dynamic: bool = False
    at: bool = False


class SubscriptionUpdate(BaseModel):
    target_id: Optional[int] = Field(None, gt=0)
    bot_id: Optional[int] = Field(None, gt=0)
    live: Optional[bool] = None
    dynamic: Optional[bool] = None
    at: Optional[bool] = None


class AuthResponse(BaseModel):
    authenticated: bool


class SessionResponse(AuthResponse):
    expires_at: Optional[int] = None


class GroupOption(BaseModel):
    id: int
    name: str


class BotOption(BaseModel):
    id: int
    name: str
    online: bool
    groups: List[GroupOption] = Field(default_factory=list)


class OptionsResponse(BaseModel):
    bots: List[BotOption]


class SubscriptionView(BaseModel):
    id: int
    uid: int
    name: str
    avatar_url: str
    room_id: int
    target_type: str
    target_id: int
    target_name: str
    guild_id: Optional[str] = None
    channel_id: Optional[str] = None
    bot_id: int
    bot_name: str
    bot_online: bool
    live: bool
    dynamic: bool
    at: bool
    live_duration: int
    live_status: str
    checked_at: Optional[int] = None
    live_started_at: Optional[int] = None
    live_title: str = ""
    live_area: str = ""
    current_live_duration: int = 0
    last_push_at: Optional[int] = None
    last_push_success: Optional[bool] = None
    last_push_event: Optional[str] = None
    last_push_error_code: Optional[str] = None
    last_push_error: Optional[str] = None


class SubscriptionListResponse(BaseModel):
    items: List[SubscriptionView]
    total: int
    live_total: int
    enabled_total: int
    summary_total: int
    summary_live_total: int
    summary_enabled_total: int
    page: int
    page_size: int


class StreamerView(BaseModel):
    uid: int
    name: str
    avatar_url: str
    room_id: int
    live_status: str
    checked_at: Optional[int] = None
    live_started_at: Optional[int] = None
    live_title: str = ""
    live_area: str = ""
    current_live_duration: int = 0
    live_duration: int
    subscription_count: int
    subscriptions: List[SubscriptionView]


class StreamerListResponse(BaseModel):
    items: List[StreamerView]
    total: int
    live_total: int
    enabled_total: int
    page: int
    page_size: int


class SubscriptionBulkRequest(BaseModel):
    ids: List[int] = Field(..., min_items=1, max_items=500)
    operation: Literal["update", "delete"]
    bot_id: Optional[int] = Field(None, gt=0)
    live: Optional[bool] = None
    dynamic: Optional[bool] = None
    at: Optional[bool] = None


class BulkResponse(BaseModel):
    processed_ids: List[int]
    missing_ids: List[int]
    failed_ids: List[int]


class PushTestResponse(BaseModel):
    success: bool
    attempted_at: int
    code: Optional[str] = None
    message: str


class AuditItem(BaseModel):
    id: int
    created_at: int
    actor: str
    action: str
    target_ids: List[int]
    success: bool
    summary: str


class AuditListResponse(BaseModel):
    items: List[AuditItem]
    total: int
    page: int
    page_size: int


class DeleteResponse(BaseModel):
    deleted: bool
    id: int


class RoomResolveError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _require_web_enabled():
    if not plugin_config.haruka_web_password:
        raise HTTPException(
            status_code=503,
            detail="未配置 HARUKA_WEB_PASSWORD，管理页面已安全禁用",
        )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signing_key() -> bytes:
    secret = plugin_config.haruka_web_secret or plugin_config.haruka_web_password or ""
    return hashlib.sha256(("haruka-web:" + secret).encode("utf-8")).digest()


def _create_session() -> Dict[str, str]:
    payload = {
        "v": SESSION_VERSION,
        "exp": int(time.time()) + plugin_config.haruka_web_session_ttl,
        "csrf": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(12),
    }
    encoded = _b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature = _b64encode(
        hmac.new(_signing_key(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return {"token": f"{encoded}.{signature}", "csrf": payload["csrf"]}


def _read_session(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    expected = _b64encode(
        hmac.new(_signing_key(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if payload.get("v") != SESSION_VERSION or payload.get("exp", 0) <= int(time.time()):
        return None
    if not isinstance(payload.get("csrf"), str):
        return None
    return payload


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _prune_login_failures(ip: str) -> List[float]:
    cutoff = time.monotonic() - LOGIN_WINDOW_SECONDS
    for address in list(_login_failures):
        failures = [stamp for stamp in _login_failures[address] if stamp > cutoff]
        if failures:
            _login_failures[address] = failures
        else:
            _login_failures.pop(address, None)

    if ip not in _login_failures and len(_login_failures) >= LOGIN_MAX_TRACKED_IPS:
        oldest_ip = min(
            _login_failures,
            key=lambda address: _login_failures[address][-1],
        )
        _login_failures.pop(oldest_ip, None)
    return _login_failures.get(ip, [])


async def require_auth(request: Request) -> Dict[str, Any]:
    _require_web_enabled()
    session = _read_session(request.cookies.get(SESSION_COOKIE))
    if not session:
        raise HTTPException(status_code=401, detail="请先登录")
    return session


async def require_csrf(
    request: Request,
    session: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    header = request.headers.get("X-CSRF-Token")
    cookie = request.cookies.get(CSRF_COOKIE)
    if (
        not header
        or not cookie
        or not hmac.compare_digest(header, cookie)
        or not hmac.compare_digest(header, session["csrf"])
    ):
        raise HTTPException(status_code=403, detail="CSRF 校验失败，请刷新页面后重试")
    return session


def _set_session_cookies(response: Response, session: Dict[str, str]):
    cookie_options = {
        "max_age": plugin_config.haruka_web_session_ttl,
        "secure": plugin_config.haruka_web_cookie_secure,
        "samesite": "strict",
        "path": "/admin",
    }
    response.set_cookie(
        SESSION_COOKIE,
        session["token"],
        httponly=True,
        **cookie_options,
    )
    response.set_cookie(
        CSRF_COOKIE,
        session["csrf"],
        httponly=False,
        **cookie_options,
    )


def _delete_session_cookies(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/admin")
    response.delete_cookie(CSRF_COOKIE, path="/admin")


async def _bot_api(bot, api: str):
    return await asyncio.wait_for(bot.call_api(api), timeout=5)


async def _build_bot_snapshot() -> Dict[str, Any]:
    bots: Dict[int, Dict[str, Any]] = {}
    group_names: Dict[Any, str] = {}

    async def inspect_bot(bot):
        bot_id = int(bot.self_id)
        bot_data: Dict[str, Any] = {
            "id": bot_id,
            "name": "",
            "online": True,
            "groups": [],
        }
        try:
            login_info = await _bot_api(bot, "get_login_info")
            if isinstance(login_info, dict):
                bot_data["name"] = str(login_info.get("nickname") or "")
        except Exception as e:
            logger.warning(f"WebUI 获取机器人 {bot_id} 登录信息失败：{e}")

        try:
            groups = await _bot_api(bot, "get_group_list")
            if isinstance(groups, dict):
                groups = groups.get("data", [])
            if not isinstance(groups, list):
                groups = []
            for group in groups:
                if not isinstance(group, dict) or not group.get("group_id"):
                    continue
                group_id = int(group["group_id"])
                group_name = str(group.get("group_name") or "")
                bot_data["groups"].append({"id": group_id, "name": group_name})
                group_names[(bot_id, group_id)] = group_name
            bot_data["groups"].sort(key=lambda item: (item["name"], item["id"]))
        except Exception as e:
            logger.warning(f"WebUI 获取机器人 {bot_id} 群列表失败：{e}")
        return bot_data

    inspected = await asyncio.gather(
        *(inspect_bot(bot) for bot in nonebot.get_bots().values()),
        return_exceptions=True,
    )
    for item in inspected:
        if isinstance(item, dict):
            bots[item["id"]] = item

    return {"bots": bots, "group_names": group_names}


async def _get_bot_snapshot(force: bool = False) -> Dict[str, Any]:
    now = time.monotonic()
    if not force and _bot_cache["value"] is not None and _bot_cache["expires"] > now:
        return _bot_cache["value"]
    async with _bot_cache_lock:
        now = time.monotonic()
        if not force and _bot_cache["value"] is not None and _bot_cache["expires"] > now:
            return _bot_cache["value"]
        value = await _build_bot_snapshot()
        _bot_cache.update({"expires": now + BOT_CACHE_SECONDS, "value": value})
        return value


def _avatar_proxy_url(uid: int) -> str:
    return f"/admin/api/users/{uid}/avatar"


def _normalize_avatar_url(value: Any) -> str:
    url = str(value or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://") :]

    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    allowed_host = any(
        hostname == suffix or hostname.endswith("." + suffix)
        for suffix in AVATAR_ALLOWED_HOSTS
    )
    if parsed.scheme != "https" or not allowed_host:
        return ""
    return url


async def _download_avatar(url: str) -> Tuple[bytes, str]:
    client_options: Dict[str, Any] = {
        "follow_redirects": False,
        "timeout": 8.0,
    }
    if plugin_config.haruka_proxy:
        client_options["proxies"] = PROXIES

    headers = {
        "Accept": "image/webp,image/png,image/jpeg,image/gif",
        "Referer": "https://www.bilibili.com/",
        "User-Agent": "Mozilla/5.0 HarukaBot-WebUI",
    }
    async with httpx.AsyncClient(**client_options) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            media_type = (
                response.headers.get("content-type", "").split(";", 1)[0].lower()
            )
            if media_type not in AVATAR_ALLOWED_MEDIA_TYPES:
                actual_type = media_type or "missing"
                raise ValueError(f"unexpected avatar content type: {actual_type}")

            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > AVATAR_MAX_BYTES:
                raise ValueError("avatar is too large")

            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > AVATAR_MAX_BYTES:
                    raise ValueError("avatar is too large")
                chunks.append(chunk)
    return b"".join(chunks), media_type


async def _load_avatar(uid: int) -> Optional[Tuple[bytes, str]]:
    try:
        user_info = await get_user_info(uid, reqtype="web", proxies=PROXIES)
        avatar_url = _normalize_avatar_url((user_info.get("card") or {}).get("face"))
        if not avatar_url:
            return None
        return await _download_avatar(avatar_url)
    except Exception as e:
        logger.warning(f"WebUI 获取用户 {uid} 头像失败：{e}")
        return None


async def _get_avatar_data(uid: int) -> Optional[Tuple[bytes, str]]:
    now = time.monotonic()
    cached = _avatar_cache.get(uid)
    if cached and cached["expires"] > now:
        return cached["value"]

    task = _avatar_tasks.get(uid)
    if task is None:
        task = asyncio.create_task(_load_avatar(uid))
        _avatar_tasks[uid] = task
    try:
        value = await task
    finally:
        if _avatar_tasks.get(uid) is task:
            _avatar_tasks.pop(uid, None)

    ttl = AVATAR_CACHE_SECONDS if value is not None else AVATAR_ERROR_CACHE_SECONDS
    _avatar_cache[uid] = {"expires": time.monotonic() + ttl, "value": value}
    if len(_avatar_cache) > 512:
        oldest_uid = min(
            (item for item in _avatar_cache if item != uid),
            key=lambda item: _avatar_cache[item]["expires"],
        )
        _avatar_cache.pop(oldest_uid, None)
    return value


async def _subscription_rows(force_options: bool = False) -> List[Dict[str, Any]]:
    subs = sorted(
        await db.get_subs(),
        key=lambda sub: (sub.uid, sub.type, sub.type_id, sub.id),
    )
    users = {user.uid: user for user in await User.all()}
    guilds = {guild.id: guild for guild in await Guild.all()}
    live_duration_totals = await db.get_live_duration_totals()
    delivery_states = await db.get_push_delivery_states()
    snapshot = await _get_bot_snapshot(force=force_options)

    try:
        from ..plugins.pusher.live_pusher import (
            live_snapshot,
            status as live_status_map,
        )
    except ImportError:
        live_status_map = {}
        live_snapshot = {}

    rows: List[Dict[str, Any]] = []
    for sub in subs:
        user = users.get(sub.uid)
        bot = snapshot["bots"].get(sub.bot_id)
        guild_id = None
        channel_id = None
        target_name = ""
        if sub.type == "group":
            target_name = snapshot["group_names"].get((sub.bot_id, sub.type_id), "")
        elif sub.type == "guild":
            guild = guilds.get(sub.type_id)
            if guild:
                guild_id = guild.guild_id
                channel_id = guild.channel_id
                target_name = f"{guild.guild_id} / {guild.channel_id}"

        live_data = live_snapshot.get(str(sub.uid)) or live_snapshot.get(sub.uid) or {}
        current_status = live_data.get("status")
        if current_status is None:
            current_status = live_status_map.get(str(sub.uid))
        if current_status is None:
            current_status = live_status_map.get(sub.uid)
        live_status = "unknown"
        if current_status is not None:
            live_status = "live" if current_status else "offline"
        checked_at = live_data.get("checked_at")
        live_started_at = live_data.get("live_started_at") or None
        delivery = delivery_states.get(sub.id)

        rows.append(
            {
                "id": sub.id,
                "uid": sub.uid,
                "name": user.name if user else "",
                "avatar_url": _avatar_proxy_url(sub.uid),
                "room_id": user.room_id if user else 0,
                "target_type": sub.type,
                "target_id": sub.type_id,
                "target_name": target_name,
                "guild_id": guild_id,
                "channel_id": channel_id,
                "bot_id": sub.bot_id,
                "bot_name": bot["name"] if bot else "",
                "bot_online": bool(bot),
                "live": bool(sub.live),
                "dynamic": bool(sub.dynamic),
                "at": bool(sub.at),
                "live_duration": live_duration_totals.get(sub.uid, 0),
                "live_status": live_status,
                "checked_at": checked_at,
                "live_started_at": live_started_at,
                "live_title": str(live_data.get("title") or ""),
                "live_area": str(live_data.get("area") or ""),
                "current_live_duration": (
                    max(0, int(time.time()) - int(live_started_at))
                    if live_status == "live" and live_started_at
                    else 0
                ),
                "last_push_at": delivery.attempted_at if delivery else None,
                "last_push_success": bool(delivery.success) if delivery else None,
                "last_push_event": delivery.event_type if delivery else None,
                "last_push_error_code": delivery.error_code if delivery else None,
                "last_push_error": delivery.error_message if delivery else None,
            }
        )
    return rows


async def _subscription_row(sub_id: int) -> Optional[Dict[str, Any]]:
    rows = await _subscription_rows()
    return next((row for row in rows if row["id"] == sub_id), None)


async def _resolve_room_details(input_room_id: int) -> Dict[str, Any]:
    room = await get(
        "https://api.live.bilibili.com/room/v1/Room/room_init",
        params={"id": input_room_id},
    )
    if not room or not room.get("uid") or not room.get("room_id"):
        raise RoomResolveError(422, "直播间不存在或未绑定主播")
    uid = int(room["uid"])
    user_info = await get_user_info(uid, reqtype="web", proxies=PROXIES)
    name = str((user_info.get("card") or {}).get("name") or "")
    if not name:
        raise RoomResolveError(502, "已解析直播间，但无法获取主播名称")
    return {
        "uid": uid,
        "name": name,
        "room_id": int(room["room_id"]),
        "short_id": int(room.get("short_id") or 0),
    }


async def _resolve_room(input_room_id: int) -> Dict[str, Any]:
    try:
        return await asyncio.wait_for(
            _resolve_room_details(input_room_id),
            timeout=ROOM_RESOLVE_TIMEOUT_SECONDS,
        )
    except RoomResolveError:
        raise
    except asyncio.TimeoutError:
        logger.warning(f"WebUI 解析直播间 {input_room_id} 超时")
        raise RoomResolveError(502, "B站接口响应超时，请稍后再试")
    except ResponseCodeError as e:
        code = getattr(e, "code", None)
        if code in (-400, -404):
            raise RoomResolveError(422, "直播间不存在")
        if code == -412:
            raise RoomResolveError(429, "B站接口触发风控，请稍后再试")
        logger.warning(f"WebUI 解析直播间 {input_room_id} 失败：{e}")
        raise RoomResolveError(502, "B站接口返回异常，请稍后再试")
    except Exception as e:
        logger.warning(f"WebUI 解析直播间 {input_room_id} 失败：{e}")
        raise RoomResolveError(502, "无法连接 B站接口，请稍后再试")


async def _resolve_uid_details(uid: int) -> Dict[str, Any]:
    user_info = await get_user_info(uid, reqtype="web", proxies=PROXIES)
    card = user_info.get("card") or {}
    name = str(card.get("name") or "")
    if not name:
        raise RoomResolveError(422, "用户 UID 不存在")

    live_room = card.get("live_room") or {}
    room_id = int(live_room.get("roomid") or 0)
    if not room_id:
        room = await get(
            "https://api.live.bilibili.com/live_user/v1/Master/info",
            params={"uid": uid},
        )
        room_id = int((room or {}).get("room_id") or 0)
    return {
        "uid": uid,
        "name": name,
        "room_id": room_id,
        "short_id": 0,
    }


async def _resolve_uid(uid: int) -> Dict[str, Any]:
    try:
        return await asyncio.wait_for(
            _resolve_uid_details(uid),
            timeout=ROOM_RESOLVE_TIMEOUT_SECONDS,
        )
    except RoomResolveError:
        raise
    except asyncio.TimeoutError:
        logger.warning(f"WebUI 解析用户 UID {uid} 超时")
        raise RoomResolveError(502, "B站接口响应超时，请稍后再试")
    except ResponseCodeError as e:
        code = getattr(e, "code", None)
        if code in (-400, -404):
            raise RoomResolveError(422, "用户 UID 不存在")
        if code == -412:
            raise RoomResolveError(429, "B站接口触发风控，请稍后再试")
        logger.warning(f"WebUI 解析用户 UID {uid} 失败：{e}")
        raise RoomResolveError(502, "B站接口返回异常，请稍后再试")
    except Exception as e:
        logger.warning(f"WebUI 解析用户 UID {uid} 失败：{e}")
        raise RoomResolveError(502, "无法连接 B站接口，请稍后再试")


@router.get("", include_in_schema=False)
async def web_redirect():
    return RedirectResponse("/admin/", status_code=307)


@router.get("/", include_in_schema=False)
async def web_index():
    return FileResponse(STATIC_ROOT / "index.html")


@router.post("/api/auth/login", response_model=AuthResponse)
async def login(payload: LoginRequest, request: Request):
    _require_web_enabled()
    ip = _client_ip(request)
    failures = _prune_login_failures(ip)
    if len(failures) >= LOGIN_MAX_FAILURES:
        retry_after = max(
            1,
            int(LOGIN_WINDOW_SECONDS - (time.monotonic() - failures[0])),
        )
        raise HTTPException(
            status_code=429,
            detail="登录失败次数过多，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )

    if not hmac.compare_digest(
        payload.password.encode("utf-8"),
        plugin_config.haruka_web_password.encode("utf-8"),
    ):
        _login_failures.setdefault(ip, []).append(time.monotonic())
        raise HTTPException(status_code=401, detail="管理密码错误")

    _login_failures.pop(ip, None)
    session = _create_session()
    response = JSONResponse({"authenticated": True})
    _set_session_cookies(response, session)
    return response


@router.get("/api/auth/session", response_model=SessionResponse)
async def session_status(request: Request):
    _require_web_enabled()
    session = _read_session(request.cookies.get(SESSION_COOKIE))
    return {
        "authenticated": bool(session),
        "expires_at": session.get("exp") if session else None,
    }


@router.post("/api/auth/logout", response_model=AuthResponse)
async def logout(_session: Dict[str, Any] = Depends(require_csrf)):
    response = JSONResponse({"authenticated": False})
    _delete_session_cookies(response)
    return response


def _filter_subscription_rows(
    rows: List[Dict[str, Any]],
    *,
    q: str = "",
    target_type: Optional[str] = None,
    live_enabled: Optional[bool] = None,
    live_status: Optional[str] = None,
    uid: Optional[int] = None,
    room_id: Optional[int] = None,
    target_id: Optional[int] = None,
    bot_id: Optional[int] = None,
    bot_online: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    query = q.strip().casefold()
    if query:
        searchable = (
            "name",
            "uid",
            "room_id",
            "target_id",
            "target_name",
            "bot_id",
            "bot_name",
            "guild_id",
            "channel_id",
        )
        rows = [
            row
            for row in rows
            if any(query in str(row.get(field) or "").casefold() for field in searchable)
        ]
    exact_filters = {
        "target_type": target_type,
        "live_status": live_status,
        "uid": uid,
        "room_id": room_id,
        "target_id": target_id,
        "bot_id": bot_id,
    }
    for field, value in exact_filters.items():
        if value is not None:
            rows = [row for row in rows if row.get(field) == value]
    if live_enabled is not None:
        rows = [row for row in rows if row["live"] is live_enabled]
    if bot_online is not None:
        rows = [row for row in rows if row["bot_online"] is bot_online]
    return rows


def _sort_subscription_rows(
    rows: List[Dict[str, Any]],
    sort_by: str,
    sort_order: str,
) -> List[Dict[str, Any]]:
    status_rank = {"live": 0, "offline": 1, "unknown": 2}

    def primary(row: Dict[str, Any]):
        if sort_by == "live_status":
            return status_rank.get(row["live_status"], 3)
        if sort_by == "name":
            return (row["name"] or "").casefold()
        if sort_by in ("target_type",):
            return str(row.get(sort_by) or "")
        return int(row.get(sort_by) or 0)

    return sorted(
        rows,
        key=lambda row: (
            primary(row),
            (row["name"] or "").casefold(),
            row["uid"],
            row["id"],
        ),
        reverse=sort_order == "desc",
    )


@router.get("/api/subscriptions", response_model=SubscriptionListResponse)
async def list_subscriptions(
    q: str = Query("", max_length=100),
    target_type: Optional[str] = Query(None),
    live_enabled: Optional[bool] = Query(None),
    live_status: Optional[str] = Query(
        None,
        regex="^(live|offline|unknown)$",
    ),
    uid: Optional[int] = Query(None, gt=0),
    room_id: Optional[int] = Query(None, gt=0),
    target_id: Optional[int] = Query(None, gt=0),
    bot_id: Optional[int] = Query(None, gt=0),
    bot_online: Optional[bool] = Query(None),
    sort_by: Literal[
        "live_status",
        "name",
        "uid",
        "live_duration",
        "checked_at",
        "target_type",
        "target_id",
        "bot_id",
    ] = Query("live_status"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1),
    _session: Dict[str, Any] = Depends(require_auth),
):
    if page_size not in (10, 20, 50, 100):
        raise HTTPException(status_code=422, detail="每页条数仅支持 10、20、50、100")
    all_rows = await _subscription_rows()
    rows = _filter_subscription_rows(
        all_rows,
        q=q,
        target_type=target_type,
        live_enabled=live_enabled,
        live_status=live_status,
        uid=uid,
        room_id=room_id,
        target_id=target_id,
        bot_id=bot_id,
        bot_online=bot_online,
    )
    rows = _sort_subscription_rows(rows, sort_by, sort_order)
    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    return {
        "items": rows[start : start + page_size],
        "total": total,
        "live_total": sum(row["live_status"] == "live" for row in rows),
        "enabled_total": sum(bool(row["live"]) for row in rows),
        "summary_total": len(all_rows),
        "summary_live_total": sum(
            row["live_status"] == "live" for row in all_rows
        ),
        "summary_enabled_total": sum(bool(row["live"]) for row in all_rows),
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/streamers", response_model=StreamerListResponse)
async def list_streamers(
    q: str = Query("", max_length=100),
    target_type: Optional[str] = Query(None),
    live_enabled: Optional[bool] = Query(None),
    live_status: Optional[str] = Query(
        None,
        regex="^(live|offline|unknown)$",
    ),
    uid: Optional[int] = Query(None, gt=0),
    room_id: Optional[int] = Query(None, gt=0),
    target_id: Optional[int] = Query(None, gt=0),
    bot_id: Optional[int] = Query(None, gt=0),
    sort_by: Literal[
        "live_status",
        "name",
        "uid",
        "live_duration",
        "checked_at",
        "target_type",
        "target_id",
        "bot_id",
    ] = Query("live_status"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1),
    _session: Dict[str, Any] = Depends(require_auth),
):
    if page_size not in (10, 20, 50, 100):
        raise HTTPException(status_code=422, detail="每页条数仅支持 10、20、50、100")
    rows = _filter_subscription_rows(
        await _subscription_rows(),
        q=q,
        target_type=target_type,
        live_enabled=live_enabled,
        live_status=live_status,
        uid=uid,
        room_id=room_id,
        target_id=target_id,
        bot_id=bot_id,
    )
    rows = _sort_subscription_rows(rows, sort_by, sort_order)
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["uid"], []).append(row)

    streamers = []
    for subscriptions in grouped.values():
        first = subscriptions[0]
        streamers.append(
            {
                "uid": first["uid"],
                "name": first["name"],
                "avatar_url": first["avatar_url"],
                "room_id": first["room_id"],
                "live_status": first["live_status"],
                "checked_at": first["checked_at"],
                "live_started_at": first["live_started_at"],
                "live_title": first["live_title"],
                "live_area": first["live_area"],
                "current_live_duration": first["current_live_duration"],
                "live_duration": first["live_duration"],
                "subscription_count": len(subscriptions),
                "subscriptions": subscriptions,
            }
        )
    total = len(streamers)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    return {
        "items": streamers[start : start + page_size],
        "total": total,
        "live_total": sum(item["live_status"] == "live" for item in streamers),
        "enabled_total": sum(
            any(subscription["live"] for subscription in item["subscriptions"])
            for item in streamers
        ),
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/users/{uid}/avatar")
async def user_avatar(
    uid: int,
    _session: Dict[str, Any] = Depends(require_auth),
):
    if uid <= 0:
        raise HTTPException(status_code=404, detail="头像不可用")
    avatar = await _get_avatar_data(uid)
    if avatar is None:
        raise HTTPException(status_code=404, detail="头像暂时不可用")
    content, media_type = avatar
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": f"private, max-age={AVATAR_BROWSER_CACHE_SECONDS}",
        },
    )


@router.get("/api/options", response_model=OptionsResponse)
async def list_options(_session: Dict[str, Any] = Depends(require_auth)):
    snapshot = await _get_bot_snapshot(force=True)
    stored_bot_ids = {sub.bot_id for sub in await db.get_subs()}
    bots = list(snapshot["bots"].values())
    online_ids = {bot["id"] for bot in bots}
    bots.extend(
        {"id": bot_id, "name": "", "online": False, "groups": []}
        for bot_id in sorted(stored_bot_ids - online_ids)
    )
    bots.sort(key=lambda item: (not item["online"], item["id"]))
    return {"bots": bots}


@router.post(
    "/api/subscriptions",
    status_code=201,
    response_model=SubscriptionView,
)
async def create_subscription(
    payload: SubscriptionCreate,
    _session: Dict[str, Any] = Depends(require_csrf),
):
    if (payload.uid is None) == (payload.room_id is None):
        raise HTTPException(status_code=422, detail="请选择用户 UID 或直播间号")

    target_id = payload.target_id
    guild_id = (payload.guild_id or "").strip()
    channel_id = (payload.channel_id or "").strip()
    if payload.target_type in ("group", "private") and target_id is None:
        raise HTTPException(status_code=422, detail="请输入有效的通知目标 ID")
    if payload.target_type == "guild" and (not guild_id or not channel_id):
        raise HTTPException(status_code=422, detail="请输入频道 ID 和子频道 ID")

    try:
        room = (
            await _resolve_uid(payload.uid)
            if payload.uid is not None
            else await _resolve_room(payload.room_id)
        )
    except RoomResolveError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    if payload.target_type == "guild":
        await db.add_guild(
            guild_id=guild_id,
            channel_id=channel_id,
            admin=True,
        )
        guild = await db.get_guild(guild_id=guild_id, channel_id=channel_id)
        if guild is None:
            raise HTTPException(status_code=500, detail="保存频道信息失败")
        target_id = guild.id

    assert target_id is not None
    created = await db.add_sub(
        uid=room["uid"],
        type=payload.target_type,
        type_id=target_id,
        bot_id=payload.bot_id,
        name=room["name"],
        room_id=room["room_id"],
        live=payload.live,
        dynamic=payload.dynamic,
        at=payload.at if payload.target_type != "private" else False,
    )
    if not created:
        raise HTTPException(
            status_code=409,
            detail="该主播已在此通知目标中订阅，请编辑现有记录",
        )
    sub = await db.get_sub(
        uid=room["uid"],
        type=payload.target_type,
        type_id=target_id,
    )
    _bot_cache["expires"] = 0
    row = await _subscription_row(sub.id)
    await db.add_web_audit(
        action="create",
        target_ids=[sub.id],
        success=True,
        summary=f"新增主播 {room['uid']} 的{payload.target_type}订阅",
    )
    return row


@router.patch(
    "/api/subscriptions/{sub_id}",
    response_model=SubscriptionView,
)
async def update_subscription(
    sub_id: int,
    payload: SubscriptionUpdate,
    _session: Dict[str, Any] = Depends(require_csrf),
):
    sub = await db.get_sub_by_id(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    updates = payload.dict(exclude_unset=True)
    if sub.type != "group" and "target_id" in updates:
        raise HTTPException(status_code=400, detail="私聊和频道订阅不能修改通知目标")
    if sub.type == "private" and updates.get("at"):
        updates["at"] = False
    if "target_id" in updates:
        updates["type_id"] = updates.pop("target_id")
    result = await db.update_sub_by_id(sub_id, **updates)
    if result is False:
        raise HTTPException(status_code=409, detail="修改后会产生重复订阅")
    if result is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    _bot_cache["expires"] = 0
    row = await _subscription_row(sub_id)
    await db.add_web_audit(
        action="update",
        target_ids=[sub_id],
        success=True,
        summary=f"更新订阅字段：{', '.join(sorted(updates)) or '无'}",
    )
    return row


@router.delete(
    "/api/subscriptions/{sub_id}",
    response_model=DeleteResponse,
)
async def delete_subscription(
    sub_id: int,
    _session: Dict[str, Any] = Depends(require_csrf),
):
    sub = await db.get_sub_by_id(sub_id)
    if not await db.delete_sub_by_id(sub_id):
        raise HTTPException(status_code=404, detail="订阅不存在")
    _bot_cache["expires"] = 0
    await db.add_web_audit(
        action="delete",
        target_ids=[sub_id],
        success=True,
        summary=f"删除主播 {sub.uid if sub else '未知'} 的订阅",
    )
    return {"deleted": True, "id": sub_id}


@router.post("/api/subscriptions/bulk", response_model=BulkResponse)
async def bulk_subscriptions(
    payload: SubscriptionBulkRequest,
    _session: Dict[str, Any] = Depends(require_csrf),
):
    ids = list(dict.fromkeys(payload.ids))
    processed_ids: List[int] = []
    missing_ids: List[int] = []
    failed_ids: List[int] = []
    updates = payload.dict(
        exclude={"ids", "operation"},
        exclude_unset=True,
    )
    updates = {key: value for key, value in updates.items() if value is not None}
    if payload.operation == "update" and not updates:
        raise HTTPException(status_code=422, detail="请至少选择一个批量修改字段")

    for sub_id in ids:
        sub = await db.get_sub_by_id(sub_id)
        if sub is None:
            missing_ids.append(sub_id)
            continue
        try:
            if payload.operation == "delete":
                succeeded = await db.delete_sub_by_id(sub_id)
            else:
                sub_updates = dict(updates)
                if sub.type == "private" and sub_updates.get("at"):
                    sub_updates["at"] = False
                succeeded = await db.update_sub_by_id(sub_id, **sub_updates)
            if succeeded:
                processed_ids.append(sub_id)
            else:
                failed_ids.append(sub_id)
        except Exception as e:
            logger.warning(f"WebUI 批量操作订阅 {sub_id} 失败：{e}")
            failed_ids.append(sub_id)

    _bot_cache["expires"] = 0
    await db.add_web_audit(
        action=f"bulk_{payload.operation}",
        target_ids=ids,
        success=not failed_ids,
        summary=(
            f"成功 {len(processed_ids)}，缺失 {len(missing_ids)}，"
            f"失败 {len(failed_ids)}"
        ),
    )
    return {
        "processed_ids": processed_ids,
        "missing_ids": missing_ids,
        "failed_ids": failed_ids,
    }


@router.post(
    "/api/subscriptions/{sub_id}/test-push",
    response_model=PushTestResponse,
)
async def test_subscription_push(
    sub_id: int,
    _session: Dict[str, Any] = Depends(require_csrf),
):
    sub = await db.get_sub_by_id(sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    row = await _subscription_row(sub_id)
    if row is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    attempted_at = int(time.time())
    message = (
        "[HarukaBot 管理页面测试]\n"
        f"主播：{row['name'] or row['uid']}（UID {row['uid']}）\n"
        f"通知目标：{row['target_type']} {row['target_name'] or row['target_id']}\n"
        f"测试时间：{datetime.now().astimezone():%Y-%m-%d %H:%M:%S}"
    )
    result = await safe_send(
        bot_id=sub.bot_id,
        send_type=sub.type,
        type_id=sub.type_id,
        message=message,
        at=False,
        allow_fallback=False,
        cleanup_invalid_target=False,
        subscription_id=sub.id,
        event_type="test",
    )
    await db.add_web_audit(
        action="test_push",
        target_ids=[sub_id],
        success=result.success,
        summary=result.message,
    )
    return {
        "success": result.success,
        "attempted_at": attempted_at,
        "code": result.code,
        "message": result.message,
    }


@router.get("/api/audit", response_model=AuditListResponse)
async def list_audit(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    _session: Dict[str, Any] = Depends(require_auth),
):
    if page_size not in (20, 50, 100):
        raise HTTPException(status_code=422, detail="每页条数仅支持 20、50、100")
    items, total = await db.get_web_audits(
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        items, total = await db.get_web_audits(
            offset=(page - 1) * page_size,
            limit=page_size,
        )
    result = []
    for item in items:
        try:
            target_ids = json.loads(item.target_ids)
        except (TypeError, json.JSONDecodeError):
            target_ids = []
        result.append(
            {
                "id": item.id,
                "created_at": item.created_at,
                "actor": item.actor,
                "action": item.action,
                "target_ids": target_ids,
                "success": bool(item.success),
                "summary": item.summary,
            }
        )
    return {
        "items": result,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _export_filename(extension: str) -> str:
    return f"haruka-subscriptions-{datetime.now().astimezone():%Y%m%d-%H%M%S}.{extension}"


@router.get("/api/export.json")
async def export_json(_session: Dict[str, Any] = Depends(require_auth)):
    rows = await _subscription_rows(force_options=True)
    content = {
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "subscriptions": rows,
    }
    return JSONResponse(
        content,
        headers={
            "Content-Disposition": f'attachment; filename="{_export_filename("json")}"'
        },
    )


@router.get("/api/export.csv")
async def export_csv(_session: Dict[str, Any] = Depends(require_auth)):
    rows = await _subscription_rows(force_options=True)
    headers = {
        "id": "订阅ID",
        "uid": "主播UID",
        "name": "主播名称",
        "room_id": "直播间号",
        "room_url": "直播间链接",
        "live_status": "当前直播状态",
        "target_type": "通知类型",
        "target_id": "通知目标ID",
        "target_name": "通知目标名称",
        "guild_id": "频道ID",
        "channel_id": "子频道ID",
        "bot_id": "机器人QQ",
        "bot_name": "机器人昵称",
        "bot_online": "机器人在线",
        "live": "直播通知",
        "dynamic": "动态通知",
        "at": "@全体",
        "live_duration": "今日直播时长（秒）",
    }
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(headers.values()))
    writer.writeheader()
    status_names = {"live": "直播中", "offline": "未开播", "unknown": "未知"}
    target_names = {"group": "QQ群", "private": "私聊", "guild": "频道"}
    for row in rows:
        export_row = dict(row)
        export_row["room_url"] = (
            f"https://live.bilibili.com/{row['room_id']}" if row["room_id"] else ""
        )
        export_row["live_status"] = status_names.get(row["live_status"], row["live_status"])
        export_row["target_type"] = target_names.get(
            row["target_type"], row["target_type"]
        )
        for key in ("bot_online", "live", "dynamic", "at"):
            export_row[key] = "是" if export_row[key] else "否"
        writer.writerow({label: export_row.get(key, "") for key, label in headers.items()})

    return Response(
        content=("\ufeff" + stream.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_export_filename("csv")}"'
        },
    )


def setup_web():
    """将 HarukaBot 管理页面挂载到 NoneBot FastAPI 驱动。"""
    try:
        app: FastAPI = nonebot.get_app()
    except Exception as e:
        logger.warning(f"当前驱动不支持 HarukaBot Web 管理页面：{e}")
        return
    if getattr(app.state, "haruka_web_registered", False):
        return

    @app.middleware("http")
    async def add_haruka_security_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/admin" or path.startswith("/admin/"):
            response.headers["Content-Security-Policy"] = WEB_CONTENT_SECURITY_POLICY
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Permissions-Policy"] = (
                "camera=(), geolocation=(), microphone=()"
            )
            if (
                path.startswith("/admin/api/users/")
                and path.endswith("/avatar")
                and response.status_code == 200
            ):
                response.headers["Cache-Control"] = (
                    f"private, max-age={AVATAR_BROWSER_CACHE_SECONDS}"
                )
            else:
                response.headers["Cache-Control"] = (
                    "no-store" if path.startswith("/admin/api/") else "no-cache"
                )
        return response

    app.include_router(router)
    app.mount(
        "/admin/static",
        StaticFiles(directory=str(STATIC_ROOT)),
        name="haruka-static",
    )
    app.state.haruka_web_registered = True
    if not plugin_config.haruka_web_password:
        logger.warning("未配置 HARUKA_WEB_PASSWORD，HarukaBot Web 管理功能已安全禁用")
    else:
        logger.info("HarukaBot Web 管理页面已启用：/admin/")
