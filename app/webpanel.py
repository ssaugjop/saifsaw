"""
SAWHost - Web Panel (لوحة الويب الكاملة)
=========================================
كل ما يفعله البوت متاح هنا عبر المتصفح، فوق نفس المحرّكات (engines.py) بدون
تكرار أي منطق: رفع المواقع، مدير الملفات والمحرر، النسخ الاحتياطية، الدومينات،
GitHub، الاشتراكات والإحالة، وكل أدوات الأدمن.

الدخول:
  - مستخدم عادي: يرسل /login للبوت فيستلم رمزاً لمرة واحدة (أو زر دخول مباشر).
  - الأدمن: اسم مستخدم + كلمة مرور (بصمة PBKDF2 مدمجة في config.py، أو DASHBOARD_USER / DASHBOARD_PASS كحساب إضافي)، أو رمز البوت.

الأمان (مهم لأن المواقع المستضافة تُخدَم من نفس الأصل تحت /s/...):
  - جلسة موقّعة HMAC داخل كوكي HttpOnly، مع وقت إصدار (iat) لإلغاء الجلسات (logout-all).
  - رمز CSRF مرتبط بالجلسة، يُدرَج في index.html فقط لتنقّلات المتصفح الحقيقية
    (Sec-Fetch-Dest=document + Mode=navigate)، وكل طلبات /panel/api تتطلبه في
    ترويسة X-Saw-CSRF. أي JavaScript داخل موقع مستضاف (نفس الأصل) لا يستطيع قراءته
    عبر fetch، فلا يستطيع تنفيذ أي إجراء باسم المستخدم. غياب ترويسات Sec-Fetch = رفض.
  - COOP: same-origin (يقطع مقبض window.open)، وframe-ancestors بلا 'self'.
  - فحص Origin لكل طلب غير GET، ورفض الكوكيز المكرَّرة (cookie tossing).
  - تحديد معدّل محاولات الدخول، وتحقق صارم من الملكية في كل مسار، وسجل تدقيق.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import html as _html
import json
import logging
import os
import re
import platform
import secrets
import shutil
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlparse

from aiohttp import web
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import security
from app.config import ADMIN_PASS_HASH, ADMIN_USER, get_cfg
from app.database import db
from app.engines import (
    BOT_TEXTS, Admin, AppError, Auth, BannedErr, Backups, DomainErr, Domains, FileErr,
    GithubIntegration, Host, LimitErr, NotFoundErr, PermErr, Plans, PlatformDomains,
    Referral, Requests, Storage, Subs,
    get_text, get_unread_notifs, is_custom_domains_enabled, is_maintenance,
    mark_notifs_read, reset_text, set_text, store_notif, toggle_custom_domains,
    toggle_maintenance,
)
from app.models import (
    TRIAL_MAX_DAYS, TRIAL_REFERRAL_DAYS, AdminNotif, AuditLog, BotText, Domain, DomainType, GithubSync,
    ReqStatus, Site, SiteStatus, SubRequest, Subscription, SubscriptionPlan, User, as_aware,
)

log = logging.getLogger("sawhost.panel")

WEB_DIR = Path(__file__).resolve().parent / "web"
COOKIE = "sw_session"
SESSION_TTL = 14 * 24 * 3600
CODE_TTL = 600
MAX_UPLOAD = int(os.getenv("PANEL_MAX_UPLOAD_MB", "150")) * 1024 * 1024
MAX_EDIT_BYTES = 1024 * 1024

_get_bot: Callable[[], Any] | None = None
_START = time.time()
VERSION = "2.0"


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════
class ApiErr(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status
        self.msg = msg


def _dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)


def _j(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps)


def _err(status: int, msg: str) -> web.Response:
    return _j({"error": msg}, status=status)


def _iso(dt: datetime | None) -> str | None:
    d = as_aware(dt)
    return d.isoformat() if d else None


def _uuid(raw: Any, what: str = "المعرّف") -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        raise ApiErr(400, f"{what} غير صالح.")


def _strip_tags(text: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def _int(raw: Any, name: str, lo: int = 0, hi: int = 10_000_000_000) -> int:
    try:
        v = int(raw)
    except (ValueError, TypeError):
        raise ApiErr(400, f"{name}: أدخل رقماً صحيحاً.")
    if v < lo or v > hi:
        raise ApiErr(400, f"{name}: القيمة خارج النطاق المسموح.")
    return v


async def _body(req: web.Request) -> dict:
    if (req.content_length or 0) > 4 * 1024 * 1024:
        raise ApiErr(413, "الطلب كبير جداً.")
    try:
        data = await req.json()
    except Exception:
        raise ApiErr(400, "بيانات الطلب غير صالحة.")
    if not isinstance(data, dict):
        raise ApiErr(400, "بيانات الطلب غير صالحة.")
    return data


def _edge_trusted(req: web.Request) -> bool:
    """هل جاء الطلب عبر الـ Worker/البروكسي الخاص بنا؟ (سرّ مشترك بزمن ثابت)"""
    return security.edge_trusted(req.headers)


def _client_key(req: web.Request) -> str:
    """عنوان العميل الحقيقي. ترويسات الـ IP لا تُصدَّق إلا من بروكسي موثوق."""
    if _edge_trusted(req):
        ip = req.headers.get("CF-Connecting-IP") or req.headers.get("X-Saw-Client-IP")
        if ip:
            return ip.strip()[:64]
    elif os.getenv("RUN_MODE", "").lower() == "actions":
        # GitHub Actions: السيرفر على 127.0.0.1 ولا يصله أحد إلا عبر Cloudflare Tunnel، وCloudflare تكتب
        # CF-Connecting-IP بنفسها. (X-Saw-* لا تُصدَّق هنا إطلاقاً.)
        ip = req.headers.get("CF-Connecting-IP")
        if ip:
            return ip.strip()[:64]
    xff = req.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[-1].strip()
    return req.remote or "?"


_hits: dict[str, list[float]] = {}


def _rate(req: web.Request, bucket: str, limit: int, window: int = 600) -> None:
    key = f"{bucket}:{_client_key(req)}"
    now = time.monotonic()
    lst = [t for t in _hits.get(key, []) if t > now - window]
    if len(lst) >= limit:
        _hits[key] = lst
        raise ApiErr(429, "محاولات كثيرة. انتظر بضع دقائق ثم أعد المحاولة.")
    lst.append(now)
    _hits[key] = lst
    if len(_hits) > 5000:
        for k in [k for k, v in _hits.items() if not v or v[-1] < now - window]:
            _hits.pop(k, None)


# ═══════════════════════════════════════════════════════════════════
#  Sessions + one-time login codes
# ═══════════════════════════════════════════════════════════════════
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key() -> bytes:
    return hashlib.sha256(b"sawhost-panel-session:" + get_cfg().secret_key.encode()).digest()


def _sign(data: str) -> str:
    return _b64(hmac.new(_key(), data.encode(), hashlib.sha256).digest())


def make_session(user_id: str) -> str:
    now = int(time.time())
    payload = _b64(json.dumps({"u": user_id, "e": now + SESSION_TTL, "i": now}, separators=(",", ":")).encode())
    return f"{payload}.{_sign(payload)}"


def parse_session(token: str) -> tuple[str, int] | None:
    """يرجع (user_id, وقت الإصدار) أو None إن كانت الجلسة غير صالحة/منتهية."""
    try:
        payload, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        data = json.loads(_unb64(payload))
        if int(data["e"]) < time.time():
            return None
        return str(data["u"]), int(data.get("i", 0))
    except Exception:
        return None


def read_session(token: str) -> str | None:
    r = parse_session(token)
    return r[0] if r else None


_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_codes: dict[str, tuple[int, float]] = {}


def issue_login_code(telegram_id: int) -> str:
    """يُستدعى من البوت (/login). رمز واحد فعّال لكل مستخدم، صالح 10 دقائق، لمرة واحدة."""
    now = time.time()
    for k in [k for k, (tid, exp) in _codes.items() if exp < now or tid == telegram_id]:
        _codes.pop(k, None)
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    _codes[code] = (telegram_id, now + CODE_TTL)
    return code


def pretty_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def login_link(code: str) -> str:
    base = get_cfg().public_url
    return f"{base}/panel/#/login?code={code}"


def _set_cookie(resp: web.Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, max_age=SESSION_TTL,
                    httponly=True, secure=_is_https_cfg(), samesite=("None" if _is_https_cfg() else "Lax"), path="/panel")


def _is_https_cfg() -> bool:
    return get_cfg().public_url.startswith("https://")


def _token(req: web.Request) -> str:
    # كوكيان بنفس الاسم = محاولة cookie tossing من موقع مستضاف: نرفض بدل أن نختار أحدهما
    h = req.headers.get("Authorization", "")
    if h.startswith("Bearer ") and h[7:].strip():
        # توكن صريح (من دخول Mini App الموقَّع) يتقدّم على أي كوكي قديم عالق في المتصفح،
        # فلا تظهر أبداً بيانات حساب آخر سبق أن سجّل الدخول في نفس المتصفح.
        return h[7:].strip()
    raw = "; ".join(req.headers.getall("Cookie", []))
    if len(re.findall(rf"(?:^|;\s*){COOKIE}=", raw)) > 1:
        raise ApiErr(401, "جلسة غير صالحة. امسح كوكيز الموقع وأعد تسجيل الدخول.")
    return req.cookies.get(COOKIE, "")


async def _user(req: web.Request, *, allow_maintenance: bool = False) -> User:
    sess = parse_session(_token(req))
    if not sess:
        raise ApiErr(401, "سجّل الدخول للمتابعة.")
    try:
        uid = uuid.UUID(sess[0])
    except ValueError:
        raise ApiErr(401, "سجّل الدخول للمتابعة.")
    user = await Admin.get_user(uid)
    if not user:
        raise ApiErr(401, "سجّل الدخول للمتابعة.")
    valid_after = as_aware(user.sessions_valid_after)
    if valid_after and sess[1] < int(valid_after.timestamp()):
        raise ApiErr(401, "انتهت الجلسة. سجّل الدخول من جديد.")
    req["actor"] = f"@{user.username}" if user.username else str(user.telegram_id)
    if user.is_banned:
        raise ApiErr(403, f"حسابك محظور. السبب: {user.ban_reason or 'غير محدد'}")
    if not allow_maintenance and not user.is_admin and await is_maintenance():
        raise ApiErr(503, _strip_tags(await get_text("maintenance_msg")))
    return user


async def _admin(req: web.Request) -> User:
    user = await _user(req)
    if not user.is_admin:
        raise ApiErr(403, "هذا الإجراء للأدمن فقط.")
    return user


async def _need_sub(user: User, site: Site | None = None) -> None:
    """يمنع أي تشغيل/تعديل/رفع لمن لا يملك اشتراكاً فعّالاً أو تجربة. الأدمن مستثنى."""
    if user.is_admin:
        return
    await Subs.assert_active(site.owner_id if site else user.id)


# ═══════════════════════════════════════════════════════════════════
#  Serializers
# ═══════════════════════════════════════════════════════════════════
def _site_dict(s: Site) -> dict:
    cfg = get_cfg()
    return {
        "id": str(s.id), "name": s.name, "slug": s.slug, "path": f"/s/{s.slug}",
        # الرابط العلني الكامل: بدومين المنصة الخاص إن عُيّن (وإلا يبني الواجهة الرابط من عنوان الصفحة)
        "url": (f"https://{s.slug}.{cfg.sites_domain}" if cfg.sites_domain
                else (f"{cfg.public_url}/s/{s.slug}" if cfg.public_domain else None)),
        "status": s.status.value, "kind": s.kind.value, "used_mb": int(s.used_mb or 0),
        "last_error": _strip_tags(s.last_error) if s.last_error else None, "created_at": _iso(s.created_at),
    }


def _plan_dict(p: SubscriptionPlan) -> dict:
    return {
        "id": str(p.id), "code": p.code, "name": p.name, "icon": p.icon,
        "price_monthly": p.price_monthly, "max_sites": p.max_sites, "max_mb": p.max_mb,
        "max_custom_domains": p.max_custom_domains, "max_platform_domains": p.max_platform_domains,
        "max_backups": p.max_backups, "features": p.feature_list, "features_raw": p.features or "",
        "is_active": p.is_active, "is_default": p.is_default, "sort_order": p.sort_order,
    }


def _user_dict(u: User) -> dict:
    return {
        "id": str(u.id), "telegram_id": u.telegram_id, "username": u.username,
        "first_name": u.first_name, "is_admin": u.is_admin, "is_banned": u.is_banned,
        "ban_reason": u.ban_reason, "created_at": _iso(u.created_at),
        "referral_count": u.referral_count, "trial_active": u.is_trial_active,
        "trial_expires_at": _iso(u.trial_expires_at), "trial_days_total": u.trial_days_total,
    }


def _domain_dict(d: Domain) -> dict:
    return {
        "id": str(d.id), "hostname": d.hostname, "type": d.dtype.value,
        "is_primary": d.is_primary, "verify_status": d.verify_status.value,
        "verify_token": d.verify_token if d.dtype == DomainType.CUSTOM else None,
    }


async def _usage(uid: uuid.UUID) -> dict:
    async with db() as s:
        sites = (await s.execute(select(func.count(Site.id)).where(Site.owner_id == uid, Site.is_deleted.is_(False)))).scalar_one()
        mb = (await s.execute(select(func.coalesce(func.sum(Site.used_mb), 0)).where(Site.owner_id == uid, Site.is_deleted.is_(False)))).scalar_one()
        cd = (await s.execute(select(func.count(Domain.id)).join(Site, Site.id == Domain.site_id).where(Site.owner_id == uid, Domain.dtype == DomainType.CUSTOM))).scalar_one()
        pdm = (await s.execute(select(func.count(Domain.id)).join(Site, Site.id == Domain.site_id).where(Site.owner_id == uid, Domain.dtype == DomainType.PLATFORM))).scalar_one()
    return {"sites": int(sites), "mb": int(mb), "custom_domains": int(cd), "platform_domains": int(pdm)}


async def _sub_dict(user: User) -> dict:
    sub = await Subs.get(user.id)
    plan = sub.plan
    eff = sub._effective_plan()
    limits = {
        "sites": sub.max_sites, "mb": sub.max_mb, "custom_domains": sub.max_domains,
        "platform_domains": sub.max_platform_domains, "backups": sub.max_backups,
    }
    plan_d = _plan_dict(plan)
    eff_name = eff.name
    if user.is_admin:       # مدير المنصة بلا حدود (قيمة -1 = غير محدود في الواجهة)
        limits = dict.fromkeys(limits, -1)
        plan_d = {**plan_d, "name": "مدير المنصة", "icon": "🛡"}
        eff_name = "مدير المنصة"
    return {
        "plan": plan_d,
        "boosted_by_trial": sub.is_trial_boost,
        "effective_plan_name": eff_name,
        "limits": limits,
        "expires_at": _iso(sub.expires_at),
        "expired": await Subs.is_expired(sub),
        "custom": {
            "sites": sub.custom_sites, "mb": sub.custom_mb, "domains": sub.custom_domains,
            "platform_domains": sub.custom_platform_domains, "backups": sub.custom_backups,
        },
    }


# ═══════════════════════════════════════════════════════════════════
#  Ownership
# ═══════════════════════════════════════════════════════════════════
async def _own_site(req: web.Request, user: User | None = None) -> tuple[User, Site]:
    user = user or await _user(req)
    sid = _uuid(req.match_info.get("sid"), "معرّف الموقع")
    site = await Host.get(sid)
    if site.owner_id != user.id and not user.is_admin:
        raise PermErr()
    return user, site


async def _own_backup(req: web.Request):
    user = await _user(req)
    bid = _uuid(req.match_info.get("bid"), "معرّف النسخة")
    bk = await Backups.get(bid)
    site = await Host.get(bk.site_id)
    if site.owner_id != user.id and not user.is_admin:
        raise PermErr()
    return user, site, bk


async def _own_domain(req: web.Request):
    user = await _user(req)
    did = _uuid(req.match_info.get("did"), "معرّف الدومين")
    dom = await Domains.get(did)
    site = await Host.get(dom.site_id)
    if site.owner_id != user.id and not user.is_admin:
        raise PermErr()
    return user, site, dom


async def _redetect(sid: uuid.UUID) -> None:
    try:
        await Host.redetect_kind(sid)
    except Exception:
        log.debug("redetect_kind failed", exc_info=True)


def _rel(raw: Any) -> str:
    s = str(raw or ".").strip()
    return s or "."


# ═══════════════════════════════════════════════════════════════════
#  Multipart
# ═══════════════════════════════════════════════════════════════════
async def _read_multipart(req: web.Request) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    if not req.content_type.startswith("multipart/"):
        raise ApiErr(400, "صيغة الطلب غير صحيحة.")
    reader = await req.multipart()
    fields: dict[str, str] = {}
    files: list[tuple[str, bytes]] = []
    total = 0
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.filename:
            buf = bytearray()
            while True:
                chunk = await part.read_chunk(256 * 1024)
                if not chunk:
                    break
                buf += chunk
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise ApiErr(413, f"حجم الرفع يتجاوز الحد المسموح ({MAX_UPLOAD // (1024 * 1024)}MB).")
            files.append((Path(part.filename.replace("\\", "/")).name, bytes(buf)))
        else:
            fields[part.name or ""] = (await part.text())[:4000]
    return fields, files


# ═══════════════════════════════════════════════════════════════════
#  Middleware
# ═══════════════════════════════════════════════════════════════════
_CSRF_EXEMPT = {"/panel/api/public"}


def _origin_ok(req: web.Request) -> bool:
    """طلبات التعديل: إن وُجدت ترويسة Origin فيجب أن تطابق مضيفنا (المتصفحات ترسلها دائماً مع POST/PUT)."""
    origin = req.headers.get("Origin")
    if origin is None:
        return True
    try:
        netloc = (urlparse(origin).netloc or "").lower()
    except Exception:
        return False
    if netloc == req.host.lower() or netloc.split(":")[0] == security.effective_host(req):
        return True
    # عبر الـ Worker قد يختلف Host عن Origin: نقبل فقط مضيفات المنصة المعرَّفة صراحةً
    return _edge_trusted(req) and netloc.split(":")[0] in get_cfg().platform_hosts


def _csrf_ok(req: web.Request) -> bool:
    try:
        tok = _token(req) or None
    except ApiErr:
        tok = None
    return security.csrf_ok(tok, req.headers.get("X-Saw-CSRF"))


_AUDIT_RULES = [
    (re.compile(r"^/panel/api/admin/requests/[^/]+/approve$"), "request.approve"),
    (re.compile(r"^/panel/api/admin/requests/[^/]+/reject$"), "request.reject"),
    (re.compile(r"^/panel/api/admin/users/[^/]+/ban$"), "user.ban"),
    (re.compile(r"^/panel/api/admin/users/[^/]+/unban$"), "user.unban"),
    (re.compile(r"^/panel/api/admin/users/[^/]+/plan$"), "user.plan"),
    (re.compile(r"^/panel/api/admin/users/[^/]+/message$"), "user.message"),
    (re.compile(r"^/panel/api/admin/sites/[^/]+/suspend$"), "site.suspend"),
    (re.compile(r"^/panel/api/admin/sites/[^/]+/unsuspend$"), "site.unsuspend"),
    (re.compile(r"^/panel/api/admin/plans$"), "plan.create"),
    (re.compile(r"^/panel/api/admin/plans/[^/]+/delete$"), "plan.delete"),
    (re.compile(r"^/panel/api/admin/plans/[^/]+(/toggle|/default)?$"), "plan.update"),
    (re.compile(r"^/panel/api/admin/pdomains"), "pdomain"),
    (re.compile(r"^/panel/api/admin/texts/"), "text.set"),
    (re.compile(r"^/panel/api/admin/settings$"), "settings"),
    (re.compile(r"^/panel/api/admin/broadcast$"), "broadcast"),
]


async def audit(actor: Any, action: str, target: str | None = None, detail: str | None = None,
                req: web.Request | None = None) -> None:
    """سجل تدقيق. لا يُفشل الطلب الأصلي أبداً."""
    if not isinstance(actor, str):
        actor = f"@{actor.username}" if getattr(actor, "username", None) else str(getattr(actor, "telegram_id", "?"))
    try:
        async with db() as s:
            s.add(AuditLog(actor=actor[:64], action=action[:64], target=(target or None) and target[:128],
                           detail=(detail or None) and detail[:500], ip=_client_key(req)[:64] if req is not None else None))
    except Exception:
        log.warning("audit write failed", exc_info=True)


_SITE_FILE_API = re.compile(
    r"^/panel/api/sites/([0-9a-fA-F-]{36})/(?:start|restart|files|file|download|download-link|upload|mkdir|rename-file|"
    r"delete-file|set-index|backups|github/sync|github/webhook|search|logs)$")


@web.middleware
async def _middleware(req: web.Request, handler):
    # اللوحة تُخدَم فقط على مضيفات المنصة؛ لا على دومين موقع مستضاف ولا على <slug>.<SITES_DOMAIN>
    # (وإلا صار لأي موقع مستضاف نسخة «لوحة» على أصله تصلح للتصيّد).
    _hosts = get_cfg().platform_hosts
    if _hosts and security.effective_host(req) not in _hosts:
        raise web.HTTPNotFound()
    is_api = req.path.startswith("/panel/api/")
    if is_api:
        try:
            _rate(req, "api", 1500, 600)      # سقف عام لكل عنوان IP (حماية من الإغراق/التخمين)
        except ApiErr as e:
            return _err(e.status, e.msg)
        if req.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(req):
            return _err(403, "مصدر الطلب غير مسموح.")
        if req.path not in _CSRF_EXEMPT and not _csrf_ok(req):
            return _j({"error": "انتهت صلاحية رمز الحماية. جارِ تحديث الصفحة…", "code": "csrf"}, status=403)
    if is_api:
        m = _SITE_FILE_API.match(req.path)
        if m:                                                    # موقع بارد في قناة التخزين → استعده قبل لمس ملفاته
            from app import tgstore
            if m.group(1) in tgstore.S.cold or m.group(1) in tgstore.S.bad:
                if not await tgstore.ensure_site(m.group(1)):
                    if m.group(1) in tgstore.S.bad:
                        return _err(503, "ملفات هذا الموقع تالفة في التخزين السحابي. تواصل مع الدعم لاستعادته من نسخة سابقة.")
                    return _err(503, "جارِ استعادة الموقع من التخزين السحابي… أعد المحاولة بعد لحظات.")
    try:
        resp = await handler(req)
    except ApiErr as e:
        resp = _err(e.status, e.msg)
    except web.HTTPException:
        raise
    except BannedErr as e:
        resp = _err(403, e.user_msg)
    except PermErr as e:
        resp = _err(403, e.user_msg)
    except NotFoundErr as e:
        resp = _err(404, e.user_msg)
    except LimitErr as e:
        resp = _err(402, e.user_msg)
    except AppError as e:
        resp = _err(400, e.user_msg)
    except Exception:
        log.exception("panel error on %s %s", req.method, req.path)
        resp = _err(500, "حدث خطأ غير متوقع في السيرفر.")
    if is_api:
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if req.method == "POST" and resp.status < 400 and req.path.startswith("/panel/api/admin/"):
            for rx, action in _AUDIT_RULES:
                if rx.match(req.path):
                    await audit(req.get("actor", "?"), action, req.path.rsplit("/", 2)[-2][:36], None, req)
                    break
    return resp


def _page_headers() -> dict:
    sd = get_cfg().sites_domain
    sites = f" https://*.{sd}" if sd else ""      # معاينة المحرر وصور الموقع تأتي من أصل المواقع المستقل
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            f"font-src 'self' https://fonts.gstatic.com; img-src 'self' data:{sites}; connect-src 'self'; frame-src 'self'{sites}; object-src 'none'; "
            "frame-ancestors https://huggingface.co https://*.huggingface.co https://web.telegram.org https://*.telegram.org; "
            "base-uri 'none'; form-action 'self'"
        ),
    }


# ═══════════════════════════════════════════════════════════════════
#  Pages + static
# ═══════════════════════════════════════════════════════════════════
def _gets_csrf(req: web.Request) -> bool:
    """رمز CSRF يُسلَّم فقط لتنقّل متصفح حقيقي أو لإطار من موقع خارجي (HF/تيليجرام). الغياب = رفض."""
    dest = req.headers.get("Sec-Fetch-Dest", "")
    mode = req.headers.get("Sec-Fetch-Mode", "")
    site = req.headers.get("Sec-Fetch-Site", "")
    if dest == "document" and mode == "navigate":
        return True
    return dest in ("iframe", "frame") and mode == "navigate" and site == "cross-site"


async def page_index(req: web.Request) -> web.Response:
    f = WEB_DIR / "index.html"
    if not f.is_file():
        return web.Response(status=500, text="ملفات واجهة اللوحة (app/web) غير موجودة.")
    try:
        tok = _token(req) or None
    except ApiErr:
        tok = None
    csrf = security.csrf_token(tok) if _gets_csrf(req) else ""
    body = f.read_text(encoding="utf-8").replace("__SAW_CSRF__", csrf)
    return web.Response(text=body, content_type="text/html", charset="utf-8", headers=_page_headers())


_STATIC_EXT = {".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp", ".woff", ".woff2", ".json", ".txt", ".webmanifest"}


async def page_static(req: web.Request) -> web.StreamResponse:
    name = req.match_info.get("name", "")
    parts = name.replace("\\", "/").split("/")
    if any((not p) or p.startswith(".") for p in parts) or "\x00" in name:
        raise web.HTTPNotFound()
    f = (WEB_DIR / name)
    if f.suffix.lower() not in _STATIC_EXT or f.is_symlink():
        raise web.HTTPNotFound()
    f = f.resolve()
    try:
        f.relative_to(WEB_DIR.resolve())
    except ValueError:
        raise web.HTTPNotFound()
    if not f.is_file():
        raise web.HTTPNotFound()
    # الصور لا تتغير: كاش أسبوع (يُسرّع فتح اللوحة على شبكات الهاتف). الكود (js/css) يبقى no-cache مع ETag حتى لا تتعارض نسخ الملفات.
    img = f.suffix.lower() in (".png", ".ico", ".webp", ".jpg", ".jpeg", ".svg", ".woff2")
    cc = "public, max-age=604800, stale-while-revalidate=86400" if img else "no-cache"
    return web.FileResponse(f, headers={"Cache-Control": cc, "X-Content-Type-Options": "nosniff"})


# ═══════════════════════════════════════════════════════════════════
#  Auth API
# ═══════════════════════════════════════════════════════════════════
async def api_public(req: web.Request) -> web.Response:
    cfg = get_cfg()
    plans = [_plan_dict(p) for p in await Plans.list_active()]
    return _j({
        "bot_username": cfg.bot_username, "admin_username": cfg.admin_username, "admin_password_login": _password_login_enabled(),
        "plans": plans, "about": _strip_tags(await get_text("about")),
        "trial": {"days_per_referral": TRIAL_REFERRAL_DAYS, "max_days": TRIAL_MAX_DAYS},
        "version": VERSION,
    })


async def api_about(req: web.Request) -> web.Response:
    """صفحة «عن المنصة» (نفس نص البوت القابل للتعديل من لوحة الأدمن)."""
    await _user(req, allow_maintenance=True)
    cfg = get_cfg()
    return _j({
        "text": _strip_tags(await get_text("about")),
        "welcome": _strip_tags(await get_text("welcome")),
        "bot_username": cfg.bot_username, "admin_username": cfg.admin_username,
    })


async def api_login_code(req: web.Request) -> web.Response:
    _rate(req, "login", 20)
    body = await _body(req)
    code = re.sub(r"[^A-Z0-9]", "", str(body.get("code", "")).upper())
    entry = _codes.get(code)
    if not code or not entry or entry[1] < time.time():
        raise ApiErr(400, "الرمز غير صحيح أو انتهت صلاحيته. أرسل /login للبوت للحصول على رمز جديد.")
    _codes.pop(code, None)
    user = await Admin.get_user_by_tgid(entry[0])
    if not user:
        raise ApiErr(400, "الحساب غير موجود. أرسل /start للبوت أولاً.")
    if user.is_banned:
        raise ApiErr(403, f"حسابك محظور. السبب: {user.ban_reason or 'غير محدد'}")
    tok = make_session(str(user.id))
    resp = _j({"ok": True, "csrf": security.csrf_token(tok)})
    _set_cookie(resp, tok)
    await audit(user, "login", None, "رمز البوت", req)
    return resp


def verify_init_data(init_data: str) -> dict:
    """يتحقق من توقيع Telegram Mini App (initData) ويرجع بيانات المستخدم."""
    pairs = dict(parse_qsl(init_data or "", keep_blank_values=True))
    got = pairs.pop("hash", "")
    if not got or "user" not in pairs:
        raise ApiErr(400, "بيانات تيليجرام غير مكتملة.")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", get_cfg().bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        raise ApiErr(403, "فشل التحقق من هوية تيليجرام.")
    try:
        age = time.time() - int(pairs.get("auth_date", "0"))
        user = json.loads(pairs["user"])
        int(user["id"])
    except Exception:
        raise ApiErr(400, "بيانات تيليجرام غير صالحة.")
    if age > 6 * 3600 or age < -300:
        raise ApiErr(403, "انتهت صلاحية الجلسة. أعد فتح اللوحة من البوت.")
    return user


async def api_login_webapp(req: web.Request) -> web.Response:
    _rate(req, "login-webapp", 30)
    body = await _body(req)
    tg = verify_init_data(str(body.get("init_data", "")))
    cfg = get_cfg()
    user, _ = await Auth.get_or_create(int(tg["id"]), tg.get("username"), tg.get("first_name"), cfg.admin_id)
    if user.is_banned:
        raise ApiErr(403, f"حسابك محظور. السبب: {user.ban_reason or 'غير محدد'}")
    tok = make_session(str(user.id))
    resp = _j({"ok": True, "token": tok, "csrf": security.csrf_token(tok)})
    _set_cookie(resp, tok)
    return resp


def _pbkdf2_ok(stored: str, password: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


def _apps_enabled() -> bool:
    from app.process_manager import apps_enabled
    return apps_enabled()


def _password_login_enabled() -> bool:
    cfg = get_cfg()
    return bool((ADMIN_USER and ADMIN_PASS_HASH) or (cfg.dash_user and len(cfg.dash_pass) >= 12))


def _admin_creds_ok(username: str, password: str) -> bool:
    """يقارن بزمن ثابت. لا توجد بيانات مدمجة بالكود: الدخول بكلمة مرور يعمل فقط إن عُيّنت Secrets."""
    cfg = get_cfg()
    ok = False
    if ADMIN_USER and ADMIN_PASS_HASH:
        u_ok = hmac.compare_digest(username.encode(), ADMIN_USER.encode())
        p_ok = _pbkdf2_ok(ADMIN_PASS_HASH, password)
        ok = u_ok and p_ok
    if cfg.dash_user and len(cfg.dash_pass) >= 12:
        u2 = hmac.compare_digest(username.encode(), cfg.dash_user.encode())
        p2 = hmac.compare_digest(password.encode(), cfg.dash_pass.encode())
        ok = ok or (u2 and p2)
    return ok


_ADMIN_FAILS: list[float] = []


async def api_login_admin(req: web.Request) -> web.Response:
    if not _password_login_enabled():
        raise ApiErr(404, "الدخول بكلمة مرور غير مفعّل. ادخل من تيليجرام عبر زر اللوحة أو الأمر /login.")
    _rate(req, "login-admin", 6)
    # قفل عام: إن كثرت المحاولات الفاشلة من أي مصدر نوقف المحاولات 10 دقائق (يوقف هجمات IP متعدّدة)
    now_m = time.monotonic()
    _ADMIN_FAILS[:] = [t for t in _ADMIN_FAILS if t > now_m - 600]
    if len(_ADMIN_FAILS) >= 25:
        raise ApiErr(429, "تم إيقاف محاولات دخول الأدمن مؤقتاً بسبب كثرة المحاولات الفاشلة. حاول بعد قليل.")
    cfg = get_cfg()
    body = await _body(req)
    username = str(body.get("username", "")).strip()[:128]
    password = str(body.get("password", ""))[:256]
    ok = await asyncio.to_thread(_admin_creds_ok, username, password)
    if not ok:
        _ADMIN_FAILS.append(time.monotonic())
        await audit(f"?{username[:24]}", "login.fail", None, "كلمة مرور أدمن خاطئة", req)
        await asyncio.sleep(1.0)
        raise ApiErr(400, "اسم المستخدم أو كلمة المرور غير صحيحة.")
    # يُنشأ حساب الأدمن تلقائياً إن لم يكن قد أرسل /start للبوت بعد
    admin, _ = await Auth.get_or_create(cfg.admin_id, cfg.admin_username, "Admin", cfg.admin_id)
    if not admin.is_admin:
        raise ApiErr(403, "حساب الأدمن غير مهيّأ. تحقق من ADMIN_ID.")
    tok = make_session(str(admin.id))
    resp = _j({"ok": True, "csrf": security.csrf_token(tok)})
    _set_cookie(resp, tok)
    await audit(admin, "login.admin", None, None, req)
    return resp


async def api_logout(req: web.Request) -> web.Response:
    resp = _j({"ok": True, "csrf": security.csrf_token(None)})
    resp.del_cookie(COOKIE, path="/panel")
    return resp


async def api_logout_all(req: web.Request) -> web.Response:
    """ينهي كل جلسات هذا الحساب على كل الأجهزة (أي جلسة صدرت قبل الآن تُرفض)."""
    user = await _user(req, allow_maintenance=True)
    async with db() as s:
        u = (await s.execute(select(User).where(User.id == user.id))).scalar_one()
        u.sessions_valid_after = datetime.now(timezone.utc)
        s.add(u)
    await audit(user, "logout.all", None, None, req)
    resp = _j({"ok": True, "csrf": security.csrf_token(None)})
    resp.del_cookie(COOKIE, path="/panel")
    return resp


async def api_me(req: web.Request) -> web.Response:
    user = await _user(req, allow_maintenance=True)
    cfg = get_cfg()
    maint = await is_maintenance()
    out: dict[str, Any] = {
        "user": _user_dict(user), "maintenance": maint,
        "maintenance_msg": _strip_tags(await get_text("maintenance_msg")) if maint else None,
        "bot_username": cfg.bot_username, "admin_username": cfg.admin_username,
    }
    if maint and not user.is_admin:
        return _j(out)
    sub = await _sub_dict(user)
    out.update({
        "has_access": await Subs.has_access(user.id),
        "subscription": sub, "usage": await _usage(user.id),
        "custom_domains_enabled": await is_custom_domains_enabled(),
        "payment_info": _strip_tags(await get_text("payment_info")),
        "referral": {
            "link": Referral.build_link(cfg.bot_username, user.telegram_id),
            "count": user.referral_count, "days_per_referral": TRIAL_REFERRAL_DAYS, "max_days": TRIAL_MAX_DAYS,
        },
    })
    if user.is_admin:
        async with db() as s:
            out["pending_requests"] = (await s.execute(select(func.count(SubRequest.id)).where(SubRequest.status == ReqStatus.PENDING))).scalar_one()
            out["unread_notifs"] = (await s.execute(select(func.count(AdminNotif.id)).where(AdminNotif.is_read.is_(False)))).scalar_one()
    return _j(out)


# ═══════════════════════════════════════════════════════════════════
#  Sites
# ═══════════════════════════════════════════════════════════════════
async def api_sites_list(req: web.Request) -> web.Response:
    user = await _user(req)
    sites = await Host.list_user(user.id)
    return _j({"sites": [_site_dict(s) for s in sites]})


async def api_site_create(req: web.Request) -> web.Response:
    user = await _user(req)
    await _need_sub(user)
    fields, files = await _read_multipart(req)
    zip_bytes = None
    stem = ""
    if files:
        fname, data = files[0]
        if not fname.lower().endswith(".zip"):
            raise ApiErr(400, "الملف يجب أن يكون بصيغة ZIP.")
        zip_bytes = data
        stem = Path(fname).stem
    name = (fields.get("name") or "").strip() or stem
    if not name or len(name) > 128:
        raise ApiErr(400, "اكتب اسماً للموقع (حتى 128 حرفاً).")
    site = await Host.create(user.id, name, zip_bytes)
    try:
        site = await Host.start(site.id)
    except Exception:
        log.warning("auto-start failed for %s", site.id, exc_info=True)
        site = await Host.get(site.id)
    return _j({"site": _site_dict(site)})


def _deploy_dict(info: dict) -> dict:
    return {"state": info.get("state", "idle"), "message": _strip_tags(info["message"]) if info.get("message") else None}


async def api_site_get(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    return _j({"site": _site_dict(site), "deploy": _deploy_dict(await Host.check_deploy(site.id))})


async def api_site_status(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    info = _deploy_dict(await Host.check_deploy(site.id))
    return _j({"site": _site_dict(await Host.get(site.id)), "deploy": info})


async def _site_action(req: web.Request, fn, *, needs_sub: bool = True) -> web.Response:
    user, site = await _own_site(req)
    if needs_sub:
        await _need_sub(user, site)
    if site.status == SiteStatus.SUSPENDED and not user.is_admin:
        raise ApiErr(403, "هذا الموقع موقوف من إدارة المنصة.")
    out = await fn(site.id)
    return _j({"site": _site_dict(out)})


async def api_site_start(req: web.Request) -> web.Response:
    return await _site_action(req, Host.start)


async def api_site_stop(req: web.Request) -> web.Response:
    return await _site_action(req, Host.stop, needs_sub=False)


async def api_site_restart(req: web.Request) -> web.Response:
    return await _site_action(req, Host.restart)


async def api_site_rename(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    name = str((await _body(req)).get("name", "")).strip()
    if not name or len(name) > 128:
        raise ApiErr(400, "الاسم يجب أن يكون بين 1 و128 حرفاً.")
    return _j({"site": _site_dict(await Host.rename(site.id, name))})


async def api_site_delete(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    try:
        await Host.stop(site.id)
    except Exception:
        pass
    for d in await Domains.list_site(site.id):
        try:
            await Domains.remove(d.id)
        except Exception:
            pass
    try:
        await GithubIntegration.unlink(site.id)
    except Exception:
        pass
    for b in await Backups.list_site(site.id):
        try:
            await Backups.delete(b.id)
        except Exception:
            pass
    await Host.delete(site.id)
    return _j({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  Files
# ═══════════════════════════════════════════════════════════════════
async def api_files_list(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    rel = _rel(req.query.get("path"))
    target = Storage.resolve(site.id, rel)
    if not target.is_dir():
        raise FileErr(user_msg="المجلد غير موجود.")
    root = Storage.site_root(site.id).resolve()
    cur = "." if target == root else str(target.relative_to(root))
    home = await Storage.resolve_homepage(site.id)
    home_rel = str(home.relative_to(root)) if home else None
    return _j({"path": cur, "items": Storage.ls(site.id, rel), "homepage": home_rel, "used_mb": site.used_mb})


async def api_file_read(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    p = Storage.resolve(site.id, _rel(req.query.get("path")))
    if not p.is_file():
        raise FileErr(user_msg="الملف غير موجود.")
    if p.stat().st_size > MAX_EDIT_BYTES:
        raise ApiErr(413, "الملف أكبر من 1MB ولا يمكن تحريره هنا. نزّله بدلاً من ذلك.")
    raw = p.read_bytes()
    try:
        if b"\x00" in raw:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ApiErr(415, "هذا ملف ثنائي ولا يمكن عرضه كنص. نزّله بدلاً من ذلك.")
    return _j({"path": _rel(req.query.get("path")), "name": p.name, "content": text, "size": len(raw)})


async def api_file_write(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    body = await _body(req)
    rel = _rel(body.get("path"))
    if rel == ".":
        raise FileErr(user_msg="حدد اسم الملف.")
    content = body.get("content", "")
    if not isinstance(content, str):
        raise ApiErr(400, "محتوى الملف غير صالح.")
    data = content.encode("utf-8")
    if len(data) > 3 * 1024 * 1024:
        raise ApiErr(413, "المحتوى كبير جداً للحفظ من المحرر (الحد 3MB).")
    await Storage.save(site.owner_id, site.id, rel, data)
    await _redetect(site.id)
    return _j({"ok": True, "size": len(data), "site": _site_dict(await Host.get(site.id))})


async def api_file_upload(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    fields, files = await _read_multipart(req)
    if not files:
        raise ApiErr(400, "لم يتم اختيار أي ملف.")
    cur = _rel(fields.get("path"))
    extract = fields.get("extract") == "1"
    saved, extracted, skipped = [], 0, []
    for fname, data in files:
        try:
            if extract and fname.lower().endswith(".zip"):
                extracted += await Storage.extract_zip(site.owner_id, site.id, data, cur)
            else:
                rel = fname if cur == "." else f"{cur}/{fname}"
                await Storage.save(site.owner_id, site.id, rel, data)
                saved.append(fname)
        except AppError as e:
            skipped.append({"name": fname, "error": e.user_msg})
    await _redetect(site.id)
    if not saved and not extracted and skipped:
        raise ApiErr(400, skipped[0]["error"])
    return _j({"saved": saved, "extracted": extracted, "skipped": skipped, "site": _site_dict(await Host.get(site.id))})


async def api_file_mkdir(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    rel = _rel((await _body(req)).get("path"))
    if rel == ".":
        raise FileErr(user_msg="اكتب اسم المجلد.")
    Storage.mkdir(site.id, rel)
    return _j({"ok": True})


async def api_file_rename(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    body = await _body(req)
    new = str(body.get("new_name", "")).strip()
    Storage.validate_name(new)
    Storage.rename(site.id, _rel(body.get("path")), new)
    await _redetect(site.id)
    return _j({"ok": True})


async def api_file_delete(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    Storage.delete(site.id, _rel((await _body(req)).get("path")))
    await Storage.recalc(site.id)
    await _redetect(site.id)
    return _j({"ok": True, "site": _site_dict(await Host.get(site.id))})


async def api_file_set_index(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    rel = _rel((await _body(req)).get("path"))
    p = Storage.resolve(site.id, rel)
    if not p.is_file():
        raise FileErr(user_msg="الملف غير موجود.")
    await Storage.save(site.owner_id, site.id, "index.html", p.read_bytes())
    return _j({"ok": True})


async def api_file_download(req: web.Request) -> web.StreamResponse:
    user, site = await _own_site(req)
    rel = _rel(req.query.get("path"))
    p = Storage.resolve(site.id, rel)
    if not p.exists():
        raise FileErr(user_msg="المسار غير موجود.")
    if p.is_file():
        name = p.name
        return web.FileResponse(p, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Content-Type": "application/octet-stream", "Cache-Control": "no-store",
        })
    data = await asyncio.get_running_loop().run_in_executor(None, Storage.compress, site.id, rel)
    name = (site.slug if p == Storage.site_root(site.id).resolve() else p.name) + ".zip"
    return web.Response(body=data, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "Content-Type": "application/zip", "Cache-Control": "no-store",
    })


async def api_file_dlink(req: web.Request) -> web.Response:
    """رابط تنزيل مؤقت (ساعة) عبر /dl/<token>: يعمل داخل تيليجرام وخارجه بلا كوكي."""
    from app.routing import mk_token, mk_zip_token
    user, site = await _own_site(req)
    rel = _rel((await _body(req)).get("path"))
    p = Storage.resolve(site.id, rel)
    if not p.exists():
        raise FileErr(user_msg="المسار غير موجود.")
    if p.is_file():
        tok = mk_token(str(p), p.name)
    else:
        data = await asyncio.get_running_loop().run_in_executor(None, Storage.compress, site.id, rel)
        name = (site.slug if p == Storage.site_root(site.id).resolve() else p.name) + ".zip"
        tok = mk_zip_token(data, name)
    return _j({"url": f"/dl/{tok}"})


# ═══════════════════════════════════════════════════════════════════
#  Backups
# ═══════════════════════════════════════════════════════════════════
async def api_site_logs(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    if site.kind.value == "static":
        return _j({"supported": False, "running": False, "lines": []})
    import app.process_manager as pm
    return _j({"supported": True, "running": pm.is_running(str(site.id)), "lines": pm.get_logs(str(site.id))})


_SEARCH_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".home", ".local", ".cache", ".npm"}
_SEARCH_TEXT_EXT = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".md", ".txt", ".py", ".php", ".xml", ".svg",
                    ".yml", ".yaml", ".env", ".ini", ".toml", ".ts", ".tsx", ".jsx", ".vue", ".sh", ".csv", ".sql"}


def _search_site(root: Path, q: str) -> list[dict]:
    ql = q.lower(); name_hits: list[dict] = []; text_hits: list[dict] = []
    for dp, dns, fns in os.walk(root, followlinks=False):
        dns[:] = [d for d in dns if d not in _SEARCH_SKIP_DIRS and not os.path.islink(os.path.join(dp, d))]
        for fn in fns:
            full = Path(dp) / fn
            if full.is_symlink():
                continue
            rel = full.relative_to(root).as_posix()
            if ql in fn.lower() and len(name_hits) < 25:
                name_hits.append({"path": rel, "line": 0, "text": ""})
            if full.suffix.lower() in _SEARCH_TEXT_EXT and len(text_hits) < 60:
                try:
                    if full.stat().st_size > 512 * 1024:
                        continue
                    for i, line in enumerate(full.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if ql in line.lower():
                            text_hits.append({"path": rel, "line": i, "text": line.strip()[:140]})
                            if len(text_hits) >= 60:
                                break
                except OSError:
                    continue
        if len(text_hits) >= 60:
            break
    return (name_hits + text_hits)[:80]


async def api_file_search(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    q = (req.query.get("q") or "").strip()[:80]
    if len(q) < 2:
        return _j({"results": []})
    root = Storage.site_root(site.id).resolve()
    res = await asyncio.to_thread(_search_site, root, q)
    return _j({"results": res, "truncated": len(res) >= 80})


def _bk_dict(b) -> dict:
    return {"id": str(b.id), "status": b.status.value, "trigger": b.trigger.value,
            "size_mb": int(b.size_mb or 0), "created_at": _iso(b.created_at)}


async def api_backups_list(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    sub = await Subs.get(site.owner_id)
    items = await Backups.list_site(site.id)
    unlimited = await Subs._is_admin(site.owner_id)
    return _j({"backups": [_bk_dict(b) for b in items], "limit": -1 if unlimited else sub.max_backups})


async def api_backup_create(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    bk = await Backups.create(site.owner_id, site.id)
    return _j({"backup": _bk_dict(bk)})


async def api_backup_download(req: web.Request) -> web.StreamResponse:
    user, site, bk = await _own_backup(req)
    if not bk.path or not Path(bk.path).is_file():
        raise ApiErr(404, "ملف النسخة غير موجود.")
    name = f"{site.slug}-{bk.created_at.strftime('%Y%m%d-%H%M')}.zip"
    return web.FileResponse(Path(bk.path), headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "Content-Type": "application/zip", "Cache-Control": "no-store",
    })


async def api_backup_dlink(req: web.Request) -> web.Response:
    from app.routing import mk_token
    user, site, bk = await _own_backup(req)
    if not bk.path or not Path(bk.path).is_file():
        raise ApiErr(404, "ملف النسخة غير موجود.")
    return _j({"url": "/dl/" + mk_token(bk.path, f"{site.slug}-{bk.created_at.strftime('%Y%m%d-%H%M')}.zip")})


async def api_backup_restore(req: web.Request) -> web.Response:
    user, site, bk = await _own_backup(req)
    await _need_sub(user, site)
    await Backups.restore(site.owner_id, bk.id)
    await _redetect(site.id)
    return _j({"ok": True, "site": _site_dict(await Host.get(site.id))})


async def api_backup_delete(req: web.Request) -> web.Response:
    user, site, bk = await _own_backup(req)
    await Backups.delete(bk.id)
    return _j({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  Domains
# ═══════════════════════════════════════════════════════════════════
async def api_domains_list(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    sub = await Subs.get(site.owner_id)
    usage = await _usage(site.owner_id)
    avail = await PlatformDomains.list_available()
    return _j({
        "domains": [_domain_dict(d) for d in await Domains.list_site(site.id)],
        "custom_enabled": await is_custom_domains_enabled(),
        "limits": ({"custom": -1, "platform": -1} if await Subs._is_admin(site.owner_id)
                   else {"custom": sub.max_domains, "platform": sub.max_platform_domains}),
        "usage": {"custom": usage["custom_domains"], "platform": usage["platform_domains"]},
        "available_platform": [{"id": str(p.id), "hostname": p.hostname, "label": p.label} for p in avail],
        "platform_host": (await get_text("platform_domain")).strip(),
    })


async def api_domain_add(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    host = str((await _body(req)).get("hostname", "")).strip().lower()
    if not re.match(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$", host):
        raise ApiErr(400, "اكتب دومين صحيحاً مثل: example.com")
    d = await Domains.add_custom(site.owner_id, site.id, host)
    return _j({"domain": _domain_dict(d)})


async def api_domain_platform(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    pdid = _uuid((await _body(req)).get("platform_domain_id"), "معرّف الدومين")
    d = await PlatformDomains.assign_to_site(site.owner_id, site.id, pdid)
    return _j({"domain": _domain_dict(d)})


async def api_domain_verify(req: web.Request) -> web.Response:
    user, site, dom = await _own_domain(req)
    await _need_sub(user, site)
    d = await Domains.verify(dom.id)
    return _j({"domain": _domain_dict(d)})


async def api_domain_primary(req: web.Request) -> web.Response:
    user, site, dom = await _own_domain(req)
    await Domains.set_primary(site.id, dom.id)
    return _j({"ok": True})


async def api_domain_delete(req: web.Request) -> web.Response:
    user, site, dom = await _own_domain(req)
    await Domains.remove(dom.id)
    return _j({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  GitHub
# ═══════════════════════════════════════════════════════════════════
def _gh_dict(link, sid: uuid.UUID) -> dict | None:
    if not link:
        return None
    return {
        "repo": link.repo_full, "repo_url": link.repo_url, "branch": link.branch, "subdir": link.subdir,
        "private": bool(link.is_private), "has_token": bool(link.token_enc), "clean": bool(link.clean),
        "auto_sync": link.auto_sync, "last_sha": link.last_sha, "last_message": link.last_commit_msg,
        "last_synced_at": _iso(link.last_synced_at), "last_sync_error": link.last_sync_error,
        "webhook_id": link.webhook_id, "webhook_path": f"/gh/{sid}", "webhook_secret": link.webhook_secret,
    }


def _hist_dict(h) -> dict:
    return {"id": str(h.id), "ok": h.ok, "sha": h.sha, "message": h.message, "trigger": h.trigger,
            "files": h.files, "detail": h.detail, "error": (h.detail if not h.ok else None),
            "created_at": _iso(h.created_at)}


async def api_github_get(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    link = await GithubIntegration.get(site.id)
    hist = await GithubIntegration.history(site.id) if link else []
    return _j({"link": _gh_dict(link, site.id), "history": [_hist_dict(h) for h in hist]})


async def api_github_inspect(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    _rate(req, "gh-inspect", 40)
    body = await _body(req)
    owner, repo = GithubIntegration.parse_repo(str(body.get("repo", "")))
    token = str(body.get("token") or "").strip()[:255] or None
    if not token:
        token = GithubIntegration._token_of(await GithubIntegration.get(site.id))
    return _j(await GithubIntegration.inspect(owner, repo, token))


async def api_github_link(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    body = await _body(req)
    owner, repo = GithubIntegration.parse_repo(str(body.get("repo", "")))
    link = await GithubIntegration.link(
        site.owner_id, site.id, owner, repo, str(body.get("branch") or ""),
        token=str(body.get("token") or "").strip()[:255] or None,
        subdir=str(body.get("subdir") or "").strip() or None,
        keep_token=bool(body.get("keep_token", True)), clean=bool(body.get("clean", True)))
    await audit(user, "github.link", str(site.id), link.repo_full, req)
    return _j({"link": _gh_dict(link, site.id)})


async def _after_sync(site_id: uuid.UUID) -> Site:
    fresh = await Host.get(site_id)
    if fresh.kind.value != "static":
        try:
            await Host.restart(site_id)
        except Exception:
            log.info("restart after github sync failed", exc_info=True)
    return await Host.get(site_id)


async def api_github_sync(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    _rate(req, "gh-sync", 30)
    msg = await GithubIntegration.sync_now(site.id, site.owner_id, "manual")
    out = await _after_sync(site.id)
    return _j({"message": msg, "link": _gh_dict(await GithubIntegration.get(site.id), site.id), "site": _site_dict(out)})


async def api_github_toggle(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    link = await GithubIntegration.toggle_auto_sync(site.id)
    return _j({"link": _gh_dict(link, site.id)})


async def api_github_webhook(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await _need_sub(user, site)
    action = str((await _body(req)).get("action") or "")
    link = await GithubIntegration.get(site.id)
    if not link:
        raise NotFoundErr("ربط GitHub")
    if action == "create":
        await GithubIntegration.create_webhook(link)
    elif action == "delete":
        await GithubIntegration.delete_webhook(link)
        async with db() as s:
            from app.models import GithubLink
            l = (await s.execute(select(GithubLink).where(GithubLink.id == link.id))).scalar_one_or_none()
            if l:
                l.webhook_id = None
                s.add(l)
    else:
        raise ApiErr(400, "إجراء غير معروف.")
    return _j({"link": _gh_dict(await GithubIntegration.get(site.id), site.id)})


async def api_github_unlink(req: web.Request) -> web.Response:
    user, site = await _own_site(req)
    await GithubIntegration.unlink(site.id)
    return _j({"ok": True})


async def api_site_import_github(req: web.Request) -> web.Response:
    """ينشئ موقعاً جديداً من مستودع GitHub في خطوة واحدة (فحص → إنشاء → ربط → مزامنة)."""
    user = await _user(req)
    await _need_sub(user)
    _rate(req, "gh-import", 12)
    body = await _body(req)
    owner, repo = GithubIntegration.parse_repo(str(body.get("repo", "")))
    token = str(body.get("token") or "").strip()[:255] or None
    await GithubIntegration.inspect(owner, repo, token)                  # نفشل قبل إنشاء أي موقع
    name = str(body.get("name") or "").strip()[:60] or repo
    site = await Host.create(user.id, name)
    try:
        with contextlib.suppress(Exception):
            Storage.delete(site.id, "index.html")                         # نزيل صفحة البداية الافتراضية
        await GithubIntegration.link(user.id, site.id, owner, repo, str(body.get("branch") or ""), token=token,
                                     subdir=str(body.get("subdir") or "").strip() or None)
        msg = await GithubIntegration.sync_now(site.id, user.id, "import")
    except Exception:
        with contextlib.suppress(Exception):
            await Host.delete(site.id)
        raise
    out = await _after_sync(site.id)
    await audit(user, "github.import", str(site.id), f"{owner}/{repo}", req)
    return _j({"site": _site_dict(out), "message": msg})


# ═══════════════════════════════════════════════════════════════════
#  Subscription + referral
# ═══════════════════════════════════════════════════════════════════
async def api_plans(req: web.Request) -> web.Response:
    await _user(req)
    return _j({"plans": [_plan_dict(p) for p in await Plans.list_active()]})


async def api_sub_history(req: web.Request) -> web.Response:
    user = await _user(req)
    async with db() as s:
        r = await s.execute(
            select(SubRequest).options(selectinload(SubRequest.plan))
            .where(SubRequest.user_id == user.id).order_by(SubRequest.created_at.desc()).limit(20)
        )
        reqs = list(r.scalars().all())
    return _j({"requests": [{
        "id": str(x.id), "plan": x.plan.name, "icon": x.plan.icon, "months": x.months,
        "total": x.total_price, "status": x.status.value, "admin_note": x.admin_note,
        "note": x.note, "created_at": _iso(x.created_at),
    } for x in reqs]})


async def api_sub_request(req: web.Request) -> web.Response:
    user = await _user(req)
    body = await _body(req)
    pid = _uuid(body.get("plan_id"), "معرّف الخطة")
    months = _int(body.get("months", 1), "عدد الأشهر", 1, 24)
    note = str(body.get("note") or "").strip()[:500] or None
    plan = await Plans.get(pid)
    if not plan.is_active or plan.price_monthly <= 0:
        raise ApiErr(400, "هذه الخطة غير متاحة للاشتراك.")
    r = await Requests.create(user.id, plan.id, months, note)
    total = plan.price_monthly * months
    from app.notify import push_new_request
    handled = False   # push_new_request يخزّن إشعاراً بديلاً بنفسه عند فشل الإرسال
    if _get_bot:
        try:
            await asyncio.wait_for(push_new_request(_get_bot(), r.id), timeout=10)
            handled = True
        except Exception:
            log.info("admin push timed out/failed", exc_info=True)
    if not handled:
        await store_notif(f"💳 طلب اشتراك جديد من @{user.username or user.telegram_id}: {plan.icon} {plan.name} — {months} شهر", kind="sub_request")
    await audit(user, "sub.request", str(r.id), f"{plan.name} x{months}", req)
    return _j({"ok": True, "total": total})


# ═══════════════════════════════════════════════════════════════════
#  Admin
# ═══════════════════════════════════════════════════════════════════
async def api_admin_stats(req: web.Request) -> web.Response:
    await _admin(req)
    st = await Admin.stats()
    ref = await Referral.stats()
    import app.process_manager as pm
    disk = None
    try:
        base = Path(get_cfg().sites_root)
        probe = base if base.exists() else Path("/")
        du = shutil.disk_usage(probe)
        disk = {"total_mb": du.total // 1048576, "used_mb": du.used // 1048576, "free_mb": du.free // 1048576}
    except Exception:
        pass
    return _j({
        "stats": st.__dict__, "referral": {"active_trials": ref["active_trials"], "total": ref["total_referrals"],
                                           "top": [_user_dict(u) for u in ref["top"]]},
        "processes": pm.stats(), "disk": disk, "series": await _series(14),
    })


async def _series(days: int) -> dict:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    labels = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    since = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    out: dict[str, Any] = {"days": labels}
    async with db() as s:
        for key, col, extra in (("users", User.created_at, None), ("sites", Site.created_at, Site.is_deleted.is_(False)),
                                ("requests", SubRequest.created_at, None)):
            stmt = select(col).where(col >= since)
            if extra is not None:
                stmt = stmt.where(extra)
            counts = dict.fromkeys(labels, 0)
            for dt in (await s.execute(stmt)).scalars().all():
                k = as_aware(dt).date().isoformat()
                if k in counts:
                    counts[k] += 1
            out[key] = [counts[d] for d in labels]
    return out


def _meminfo() -> dict | None:
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0]) // 1024
        total, avail = info["MemTotal"], info.get("MemAvailable", info["MemFree"])
        return {"total": total, "used": total - avail}
    except Exception:
        return None


async def api_admin_health(req: web.Request) -> web.Response:
    await _admin(req)
    import app.process_manager as pm
    cfg = get_cfg()
    disk = None
    with contextlib.suppress(Exception):
        base = Path(cfg.sites_root)
        du = shutil.disk_usage(base if base.exists() else Path("/"))
        disk = {"total_mb": du.total // 1048576, "used_mb": du.used // 1048576, "free_mb": du.free // 1048576}

    def chk(name: str, ok: bool, detail: str, level: str = "bad") -> dict:
        return {"name": name, "ok": bool(ok), "detail": detail, "level": level}

    apps_on = pm.apps_enabled()
    storage_ok = Path(cfg.sites_root).exists() and os.access(cfg.sites_root, os.W_OK)
    checks = [
        chk("اتصال HTTPS", cfg.public_url.startswith("https://"),
            "الروابط العلنية تعمل عبر HTTPS." if cfg.public_url.startswith("https://") else
            "عيّن WEBHOOK_BASE_URL/PUBLIC_DOMAIN بعنوان https:// (الكوكيز الآمنة وتيليجرام Mini App تحتاجه)."),
        chk("مفتاح التوقيع SECRET_KEY", len(cfg.secret_key) >= 32,
            "طويل بما يكفي." if len(cfg.secret_key) >= 32 else "قصير: استخدم 32 حرفاً عشوائياً على الأقل."),
        chk("سرّ الويب هوك", len(cfg.webhook_secret) >= 16,
            "مضبوط." if len(cfg.webhook_secret) >= 16 else "قصير أو غير معيّن: يسمح بحقن تحديثات مزوّرة للبوت."),
        chk("دخول الأدمن بكلمة مرور", True,
            "مفعّل بصمة PBKDF2 من Secrets." if _password_login_enabled() else
            "معطّل (الأفضل أمنياً): الأدمن يدخل فقط من تيليجرام بهويته الموقَّعة."),
        chk("الدومين العام للمنصة", bool(cfg.public_domain),
            cfg.public_domain if cfg.public_domain else "غير معيّن: عيّن PUBLIC_DOMAIN=sawhost.de5.net بعد ضبط الـ Worker.", "warn"),
        chk("سرّ الـ Worker (EDGE_SECRET)", bool(cfg.edge_secret) or not cfg.public_domain,
            "مضبوط." if cfg.edge_secret else ("غير لازم بدون دومين خاص." if not cfg.public_domain else
            "غير معيّن: لن تُصدَّق ترويسات المضيف وعنوان IP القادمة عبر الـ Worker."), "warn"),
        chk("عزل المواقع المستضافة عن اللوحة", bool(cfg.sites_domain),
            f"كل موقع على أصل مستقل: *.{cfg.sites_domain}" if cfg.sites_domain else
            "المواقع تُخدَم تحت /s/ على نفس أصل اللوحة. عيّن SITES_DOMAIN مع wildcard DNS لعزل كامل.", "warn"),
        chk("تشغيل تطبيقات Python/Node/PHP", True,
            "مفعّل مع عزل UID مستقل لكل موقع." if apps_on else
            "معطّل: لا يوجد عزل آمن (يتطلب تشغيل root). المواقع الثابتة تعمل طبيعياً.", "warn"),
        chk("التخزين الدائم", storage_ok,
            cfg.sites_root if storage_ok else "مجلد المواقع غير موجود/غير قابل للكتابة: فعّل Persistent Storage وإلا ضاعت الملفات عند إعادة التشغيل."),
        chk("مساحة القرص", not disk or disk["free_mb"] > max(200, disk["total_mb"] // 20),
            (f"حر {disk['free_mb']} MB" if disk else "غير متاح"), "warn"),
    ]
    return _j({
        "version": VERSION, "uptime_s": int(time.time() - _START), "python": sys.version.split()[0],
        "platform": platform.platform(), "db": "SQLite" if cfg.is_sqlite else "PostgreSQL",
        "bot": cfg.bot_username, "processes": pm.stats()["running_count"],
        "memory_mb": _meminfo(), "disk": disk, "https": _is_https_cfg(), "checks": checks,
    })


async def api_admin_audit(req: web.Request) -> web.Response:
    await _admin(req)
    page = _int(req.query.get("page", 0), "الصفحة", 0, 100000)
    size = 30
    async with db() as s:
        rows = list((await s.execute(select(AuditLog).order_by(AuditLog.created_at.desc())
                                     .offset(page * size).limit(size + 1))).scalars().all())
    return _j({"items": [{"id": str(a.id), "actor": a.actor, "action": a.action, "target": a.target,
                          "detail": a.detail, "ip": a.ip, "created_at": _iso(a.created_at)} for a in rows[:size]],
               "page": page, "has_more": len(rows) > size})


async def api_admin_user_message(req: web.Request) -> web.Response:
    await _admin(req)
    uid = _uuid(req.match_info.get("uid"), "معرّف المستخدم")
    target = await Admin.get_user(uid)
    if not target:
        raise NotFoundErr("المستخدم")
    text = str((await _body(req)).get("text") or "").strip()[:1500]
    if not text:
        raise ApiErr(400, "اكتب نص الرسالة.")
    if not _get_bot:
        raise ApiErr(503, "البوت غير متاح.")
    try:
        await asyncio.wait_for(_get_bot().send_message(target.telegram_id, "📩 <b>رسالة من إدارة المنصة</b>\n\n" + _html.escape(text)), timeout=10)
    except Exception:
        raise ApiErr(502, "تعذّر إرسال الرسالة (ربما أوقف المستخدم البوت).")
    return _j({"ok": True})


async def api_admin_users(req: web.Request) -> web.Response:
    await _admin(req)
    q = (req.query.get("q") or "").strip()[:64]
    page = _int(req.query.get("page", 0), "الصفحة", 0, 100000)
    size = 20
    stmt = select(User).options(selectinload(User.subscription).selectinload(Subscription.plan)).order_by(User.created_at.desc())
    if q:
        like = f"%{q}%"
        conds = [User.username.ilike(like), User.first_name.ilike(like)]
        if q.isdigit():
            conds.append(User.telegram_id == int(q))
        stmt = stmt.where(or_(*conds))
    async with db() as s:
        users = list((await s.execute(stmt.offset(page * size).limit(size + 1))).scalars().all())
        has_more = len(users) > size
        users = users[:size]
        counts: dict[uuid.UUID, int] = {}
        if users:
            rows = await s.execute(
                select(Site.owner_id, func.count(Site.id)).where(Site.owner_id.in_([u.id for u in users]), Site.is_deleted.is_(False)).group_by(Site.owner_id)
            )
            counts = {r[0]: r[1] for r in rows.all()}
    out = []
    for u in users:
        d = _user_dict(u)
        d["plan"] = u.subscription.plan.name if (u.subscription and u.subscription.plan) else None
        d["sites"] = counts.get(u.id, 0)
        out.append(d)
    return _j({"users": out, "page": page, "has_more": has_more})


async def api_admin_user(req: web.Request) -> web.Response:
    await _admin(req)
    uid = _uuid(req.match_info.get("uid"), "معرّف المستخدم")
    target = await Admin.get_user(uid)
    if not target:
        raise NotFoundErr("المستخدم")
    sites = await Host.list_user(uid)
    return _j({"user": _user_dict(target), "subscription": await _sub_dict(target),
               "usage": await _usage(uid), "sites": [_site_dict(s) for s in sites]})


async def api_admin_user_ban(req: web.Request) -> web.Response:
    admin = await _admin(req)
    uid = _uuid(req.match_info.get("uid"), "معرّف المستخدم")
    target = await Admin.get_user(uid)
    if not target:
        raise NotFoundErr("المستخدم")
    if target.is_admin:
        raise ApiErr(400, "لا يمكن حظر أدمن.")
    reason = str((await _body(req)).get("reason") or "بدون سبب").strip()[:512]
    await Auth.ban(uid, reason)
    await store_notif(f"🚫 تم حظر @{target.username or target.telegram_id}. السبب: {reason}", kind="warning")
    return _j({"ok": True})


async def api_admin_user_unban(req: web.Request) -> web.Response:
    await _admin(req)
    await Auth.unban(_uuid(req.match_info.get("uid"), "معرّف المستخدم"))
    return _j({"ok": True})


async def api_admin_user_plan(req: web.Request) -> web.Response:
    await _admin(req)
    uid = _uuid(req.match_info.get("uid"), "معرّف المستخدم")
    body = await _body(req)
    plan = await Plans.get(_uuid(body.get("plan_id"), "معرّف الخطة"))
    target = await Admin.get_user(uid)
    if not target:
        raise NotFoundErr("المستخدم")
    custom_map = {"custom_sites": "custom_sites", "custom_mb": "custom_mb", "custom_domains": "custom_domains",
                  "custom_platform_domains": "custom_platform_domains", "custom_backups": "custom_backups"}
    async with db() as s:
        r = await s.execute(select(Subscription).where(Subscription.user_id == uid))
        sub = r.scalar_one_or_none()
        if not sub:
            raise NotFoundErr("الاشتراك")
        sub.plan_id = plan.id
        if "days" in body and body["days"] not in (None, ""):
            days = _int(body["days"], "المدة بالأيام", 0, 3650)
            sub.expires_at = None if days == 0 else datetime.now(timezone.utc) + timedelta(days=days)
        for k, attr in custom_map.items():
            if k in body:
                v = body[k]
                setattr(sub, attr, None if v in (None, "") else _int(v, k, 1, 10_000_000))
        s.add(sub)
    await store_notif(f"💳 تم تغيير خطة @{target.username or target.telegram_id} إلى {plan.icon} {plan.name} (من لوحة الويب)")
    return _j({"ok": True, "subscription": await _sub_dict(target)})


async def api_admin_sites(req: web.Request) -> web.Response:
    await _admin(req)
    q = (req.query.get("q") or "").strip()[:64]
    page = _int(req.query.get("page", 0), "الصفحة", 0, 100000)
    size = 25
    stmt = select(Site, User.username, User.telegram_id).join(User, User.id == Site.owner_id).where(Site.is_deleted.is_(False)).order_by(Site.created_at.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Site.name.ilike(like), Site.slug.ilike(like), User.username.ilike(like)))
    async with db() as s:
        rows = (await s.execute(stmt.offset(page * size).limit(size + 1))).all()
    has_more = len(rows) > size
    out = []
    for site, uname, tid in rows[:size]:
        d = _site_dict(site)
        d["owner"] = uname or str(tid)
        out.append(d)
    return _j({"sites": out, "page": page, "has_more": has_more})


async def api_admin_site_suspend(req: web.Request) -> web.Response:
    await _admin(req)
    sid = _uuid(req.match_info.get("sid"), "معرّف الموقع")
    site = await Host.get(sid)
    await Host.stop(sid)
    out = await Host._set_status(sid, SiteStatus.SUSPENDED, error="موقوف من إدارة المنصة.")
    await store_notif(f"🚫 تم إيقاف الموقع {site.name} ({site.slug}) من لوحة الويب", kind="warning")
    return _j({"site": _site_dict(out)})


async def api_admin_site_unsuspend(req: web.Request) -> web.Response:
    await _admin(req)
    sid = _uuid(req.match_info.get("sid"), "معرّف الموقع")
    await Host.get(sid)
    out = await Host._set_status(sid, SiteStatus.STOPPED)
    return _j({"site": _site_dict(out)})


async def api_admin_requests(req: web.Request) -> web.Response:
    await _admin(req)
    status = req.query.get("status", "pending")
    stmt = select(SubRequest).options(selectinload(SubRequest.plan), selectinload(SubRequest.user)).order_by(SubRequest.created_at.desc()).limit(60)
    if status in ("pending", "approved", "rejected"):
        stmt = stmt.where(SubRequest.status == ReqStatus(status))
    async with db() as s:
        rows = list((await s.execute(stmt)).scalars().all())
    return _j({"requests": [{
        "id": str(x.id), "status": x.status.value, "months": x.months, "total": x.total_price,
        "plan": x.plan.name, "icon": x.plan.icon, "note": x.note, "admin_note": x.admin_note,
        "created_at": _iso(x.created_at),
        "user": {"id": str(x.user.id), "name": x.user.first_name, "username": x.user.username, "telegram_id": x.user.telegram_id},
    } for x in rows]})


async def _tell_user(tg_id: int, text: str) -> None:
    if not _get_bot:
        return
    try:
        await asyncio.wait_for(_get_bot().send_message(tg_id, text), timeout=8)
    except Exception:
        log.info("could not notify user %s", tg_id)


async def api_admin_request_approve(req: web.Request) -> web.Response:
    admin = await _admin(req)
    rid = _uuid(req.match_info.get("rid"), "معرّف الطلب")
    r = await Requests.approve(rid, admin.id)
    target = await Admin.get_user(r.user_id)
    if target:
        await store_notif(f"✅ تم تفعيل {r.plan.icon} {r.plan.name} ({r.months} شهر) للمستخدم @{target.username or target.telegram_id}")
        await _tell_user(target.telegram_id, f"🎉 تم تفعيل {r.plan.icon} {r.plan.name} ({r.months} شهر)! استمتع بالمنصة.")
    return _j({"ok": True})


async def api_admin_request_reject(req: web.Request) -> web.Response:
    await _admin(req)
    rid = _uuid(req.match_info.get("rid"), "معرّف الطلب")
    reason = str((await _body(req)).get("reason") or "—").strip()[:512]
    r = await Requests.reject(rid, reason)
    target = await Admin.get_user(r.user_id)
    if target:
        await _tell_user(target.telegram_id, f"❌ تم رفض طلب اشتراكك.\nالسبب: {reason}\nللاستفسار: @{get_cfg().admin_username}")
    return _j({"ok": True})


_PLAN_INTS = ("price_monthly", "max_sites", "max_mb", "max_custom_domains", "max_platform_domains", "max_backups")


def _plan_fields(body: dict, *, partial: bool) -> dict:
    out: dict[str, Any] = {}
    for k in _PLAN_INTS:
        if k in body or not partial:
            out[k] = _int(body.get(k, 0), k)
    if "name" in body or not partial:
        name = str(body.get("name", "")).strip()
        if not name or len(name) > 64:
            raise ApiErr(400, "اسم الخطة يجب أن يكون بين 1 و64 حرفاً.")
        out["name"] = name
    if "icon" in body:
        out["icon"] = str(body.get("icon") or "📦").strip()[:16] or "📦"
    if "features" in body or not partial:
        feats = str(body.get("features", "")).strip()
        if len(feats) > 2000:
            raise ApiErr(400, "نص الميزات طويل جداً (الحد 2000 حرف).")
        out["features"] = feats
    return out


async def api_admin_plans(req: web.Request) -> web.Response:
    await _admin(req)
    plans = await Plans.list_all()
    async with db() as s:
        rows = await s.execute(select(Subscription.plan_id, func.count(Subscription.id)).group_by(Subscription.plan_id))
        subs = {r[0]: r[1] for r in rows.all()}
    out = []
    for p in plans:
        d = _plan_dict(p)
        d["subscribers"] = subs.get(p.id, 0)
        out.append(d)
    return _j({"plans": out})


async def api_admin_plan_create(req: web.Request) -> web.Response:
    await _admin(req)
    f = _plan_fields(await _body(req), partial=False)
    p = await Plans.create(code=f"plan_{secrets.token_hex(4)}", name=f["name"], icon=f.get("icon", "📦"),
                           price=f["price_monthly"], sites=f["max_sites"], mb=f["max_mb"],
                           cdom=f["max_custom_domains"], pdom=f["max_platform_domains"],
                           backups=f["max_backups"], features=f["features"])
    return _j({"plan": _plan_dict(p)})


async def api_admin_plan_update(req: web.Request) -> web.Response:
    await _admin(req)
    pid = _uuid(req.match_info.get("pid"), "معرّف الخطة")
    f = _plan_fields(await _body(req), partial=True)
    p = await Plans.update(pid, **f)
    return _j({"plan": _plan_dict(p)})


async def api_admin_plan_toggle(req: web.Request) -> web.Response:
    await _admin(req)
    p = await Plans.toggle_active(_uuid(req.match_info.get("pid"), "معرّف الخطة"))
    return _j({"plan": _plan_dict(p)})


async def api_admin_plan_default(req: web.Request) -> web.Response:
    await _admin(req)
    await Plans.set_default(_uuid(req.match_info.get("pid"), "معرّف الخطة"))
    return _j({"ok": True})


async def api_admin_plan_delete(req: web.Request) -> web.Response:
    await _admin(req)
    await Plans.delete(_uuid(req.match_info.get("pid"), "معرّف الخطة"))
    return _j({"ok": True})


async def api_admin_pdomains(req: web.Request) -> web.Response:
    await _admin(req)
    items = await PlatformDomains.list_all()
    used_ids = [p.used_by_site_id for p in items if p.used_by_site_id]
    sites: dict[uuid.UUID, Site] = {}
    if used_ids:
        async with db() as s:
            rows = await s.execute(select(Site).where(Site.id.in_(used_ids)))
            sites = {x.id: x for x in rows.scalars().all()}
    return _j({"domains": [{
        "id": str(p.id), "hostname": p.hostname, "label": p.label, "is_active": p.is_active,
        "used_by": (sites[p.used_by_site_id].name if p.used_by_site_id in sites else None),
        "used": bool(p.used_by_site_id),
    } for p in items]})


async def api_admin_pdomain_add(req: web.Request) -> web.Response:
    await _admin(req)
    body = await _body(req)
    host = str(body.get("hostname", "")).strip().lower()
    if not re.match(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$", host):
        raise ApiErr(400, "اكتب دومين صحيحاً مثل: example.com")
    pd = await PlatformDomains.add(host, str(body.get("label", "")).strip()[:128])
    return _j({"ok": True, "id": str(pd.id)})


async def api_admin_pdomain_toggle(req: web.Request) -> web.Response:
    await _admin(req)
    await PlatformDomains.toggle_active(_uuid(req.match_info.get("pdid"), "معرّف الدومين"))
    return _j({"ok": True})


async def api_admin_pdomain_delete(req: web.Request) -> web.Response:
    await _admin(req)
    await PlatformDomains.remove(_uuid(req.match_info.get("pdid"), "معرّف الدومين"))
    return _j({"ok": True})


async def api_admin_texts(req: web.Request) -> web.Response:
    await _admin(req)
    async with db() as s:
        custom = {r[0] for r in (await s.execute(select(BotText.key))).all()}
    return _j({"texts": [{"key": k, "desc": v["desc"], "value": await get_text(k), "is_custom": k in custom}
                         for k, v in BOT_TEXTS.items()]})


async def api_admin_text_set(req: web.Request) -> web.Response:
    await _admin(req)
    key = req.match_info.get("key", "")
    if key not in BOT_TEXTS:
        raise ApiErr(404, "مفتاح النص غير موجود.")
    value = str((await _body(req)).get("value", "")).strip()
    if not value or len(value) > 4000:
        raise ApiErr(400, "النص يجب أن يكون بين 1 و4000 حرف.")
    await set_text(key, value)
    return _j({"ok": True})


async def api_admin_text_reset(req: web.Request) -> web.Response:
    await _admin(req)
    key = req.match_info.get("key", "")
    if key not in BOT_TEXTS:
        raise ApiErr(404, "مفتاح النص غير موجود.")
    await reset_text(key)
    return _j({"ok": True, "value": await get_text(key)})


async def api_admin_settings(req: web.Request) -> web.Response:
    await _admin(req)
    return _j({"maintenance": await is_maintenance(), "custom_domains": await is_custom_domains_enabled(),
               "trial": {"days_per_referral": TRIAL_REFERRAL_DAYS, "max_days": TRIAL_MAX_DAYS}})


async def api_admin_settings_set(req: web.Request) -> web.Response:
    await _admin(req)
    body = await _body(req)
    if "maintenance" in body:
        await toggle_maintenance(bool(body["maintenance"]))
    if "custom_domains" in body:
        await toggle_custom_domains(bool(body["custom_domains"]))
    return _j({"maintenance": await is_maintenance(), "custom_domains": await is_custom_domains_enabled()})


async def api_admin_notifs(req: web.Request) -> web.Response:
    await _admin(req)
    async with db() as s:
        rows = list((await s.execute(select(AdminNotif).order_by(AdminNotif.created_at.desc()).limit(60))).scalars().all())
    return _j({"notifs": [{"id": str(n.id), "text": _strip_tags(n.text), "kind": n.kind,
                           "is_read": n.is_read, "created_at": _iso(n.created_at)} for n in rows]})


async def api_admin_notifs_read(req: web.Request) -> web.Response:
    await _admin(req)
    await mark_notifs_read()
    return _j({"ok": True})


_broadcast: dict[str, Any] = {"running": False, "sent": 0, "failed": 0, "total": 0, "done": True}


async def _run_broadcast(text: str) -> None:
    bot = _get_bot() if _get_bot else None
    try:
        async with db() as s:
            r = await s.execute(select(User.telegram_id).where(User.is_banned.is_(False)))
            ids = [row[0] for row in r.all()]
        _broadcast.update(running=True, done=False, sent=0, failed=0, total=len(ids))
        for tid in ids:
            try:
                await bot.send_message(tid, text)
                _broadcast["sent"] += 1
            except Exception:
                _broadcast["failed"] += 1
            await asyncio.sleep(0.05)
    finally:
        _broadcast.update(running=False, done=True)


async def api_admin_broadcast(req: web.Request) -> web.Response:
    await _admin(req)
    if _broadcast["running"]:
        raise ApiErr(409, "يوجد بث قيد التنفيذ حالياً.")
    if not _get_bot:
        raise ApiErr(500, "البوت غير جاهز.")
    text = str((await _body(req)).get("text", "")).strip()
    if not text or len(text) > 3500:
        raise ApiErr(400, "نص الرسالة يجب أن يكون بين 1 و3500 حرف.")
    _broadcast.update(running=True, done=False, sent=0, failed=0, total=0)
    asyncio.create_task(_run_broadcast(text))
    return _j({"ok": True})


async def api_admin_broadcast_status(req: web.Request) -> web.Response:
    await _admin(req)
    return _j(_broadcast)


# ═══════════════════════════════════════════════════════════════════
#  Expiry enforcement: يوقف مواقع كل من لا يملك اشتراكاً/تجربة فعّالة
# ═══════════════════════════════════════════════════════════════════
LAPSED_MSG = "أُوقف الموقع لانتهاء الاشتراك. اشترك أو جدّد اشتراكك ثم اضغط تشغيل."


async def enforce_expiry() -> int:
    async with db() as s:
        r = await s.execute(select(Site.id, Site.owner_id).where(
            Site.is_deleted.is_(False), Site.status.in_([SiteStatus.RUNNING, SiteStatus.DEPLOYING])))
        rows = r.all()
    ok: dict[uuid.UUID, bool] = {}
    stopped = 0
    for sid, owner in rows:
        if owner not in ok:
            try:
                ok[owner] = await Subs.has_access(owner)
            except Exception:
                ok[owner] = True          # عند أي خطأ لا نوقف أحداً
        if ok[owner]:
            continue
        try:
            await Host.stop(sid)
            await Host._set_status(sid, SiteStatus.STOPPED, error=LAPSED_MSG)
            stopped += 1
        except Exception:
            log.warning("could not stop lapsed site %s", sid, exc_info=True)
    if stopped:
        log.info("expiry sweep: stopped %d site(s) of unsubscribed users", stopped)
    return stopped


async def _expiry_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await enforce_expiry()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("expiry sweep failed")
        await asyncio.sleep(1800)


def start_expiry_task(app: web.Application) -> None:
    """يعمل افتراضياً. عطّله بـ ENFORCE_EXPIRY=0 إن أردت."""
    if os.getenv("ENFORCE_EXPIRY", "1") == "0":
        log.info("expiry enforcement disabled (ENFORCE_EXPIRY=0)")
        return

    async def _start(a: web.Application) -> None:
        a["expiry_task"] = asyncio.create_task(_expiry_loop())

    async def _stop(a: web.Application) -> None:
        t = a.get("expiry_task")
        if t:
            t.cancel()

    app.on_startup.append(_start)
    app.on_cleanup.append(_stop)


# ═══════════════════════════════════════════════════════════════════
#  Registration
# ═══════════════════════════════════════════════════════════════════
def register_webpanel(app: web.Application, get_bot_fn: Callable[[], Any]) -> None:
    """
    يجب استدعاؤها قبل register_routing (الـ catch-all) في main.py.
    """
    global _get_bot
    _get_bot = get_bot_fn

    async def _to_panel(req: web.Request) -> web.Response:
        raise web.HTTPFound("/panel/")

    app.router.add_get("/panel", _to_panel)

    p = web.Application(middlewares=[_middleware], client_max_size=MAX_UPLOAD + 8 * 1024 * 1024)
    r = p.router
    r.add_get("/", page_index)
    r.add_get("/static/{name:.+}", page_static)

    A = "/api"
    r.add_get(f"{A}/public", api_public)
    r.add_post(f"{A}/login", api_login_code)
    r.add_post(f"{A}/login/webapp", api_login_webapp)
    r.add_post(f"{A}/login/admin", api_login_admin)
    r.add_post(f"{A}/logout", api_logout)
    r.add_post(f"{A}/logout-all", api_logout_all)
    r.add_get(f"{A}/me", api_me)
    r.add_get(f"{A}/about", api_about)

    r.add_get(f"{A}/sites", api_sites_list)
    r.add_post(f"{A}/sites", api_site_create)
    r.add_post(f"{A}/sites/import-github", api_site_import_github)
    r.add_get(f"{A}/sites/{{sid}}", api_site_get)
    r.add_get(f"{A}/sites/{{sid}}/status", api_site_status)
    r.add_post(f"{A}/sites/{{sid}}/start", api_site_start)
    r.add_post(f"{A}/sites/{{sid}}/stop", api_site_stop)
    r.add_post(f"{A}/sites/{{sid}}/restart", api_site_restart)
    r.add_post(f"{A}/sites/{{sid}}/rename", api_site_rename)
    r.add_post(f"{A}/sites/{{sid}}/delete", api_site_delete)

    r.add_get(f"{A}/sites/{{sid}}/files", api_files_list)
    r.add_get(f"{A}/sites/{{sid}}/file", api_file_read)
    r.add_put(f"{A}/sites/{{sid}}/file", api_file_write)
    r.add_get(f"{A}/sites/{{sid}}/download", api_file_download)
    r.add_post(f"{A}/sites/{{sid}}/download-link", api_file_dlink)
    r.add_post(f"{A}/sites/{{sid}}/upload", api_file_upload)
    r.add_post(f"{A}/sites/{{sid}}/mkdir", api_file_mkdir)
    r.add_post(f"{A}/sites/{{sid}}/rename-file", api_file_rename)
    r.add_post(f"{A}/sites/{{sid}}/delete-file", api_file_delete)
    r.add_post(f"{A}/sites/{{sid}}/set-index", api_file_set_index)

    r.add_get(f"{A}/sites/{{sid}}/backups", api_backups_list)
    r.add_post(f"{A}/sites/{{sid}}/backups", api_backup_create)
    r.add_get(f"{A}/backups/{{bid}}/download", api_backup_download)
    r.add_post(f"{A}/backups/{{bid}}/download-link", api_backup_dlink)
    r.add_post(f"{A}/backups/{{bid}}/restore", api_backup_restore)
    r.add_post(f"{A}/backups/{{bid}}/delete", api_backup_delete)

    r.add_get(f"{A}/sites/{{sid}}/domains", api_domains_list)
    r.add_post(f"{A}/sites/{{sid}}/domains/custom", api_domain_add)
    r.add_post(f"{A}/sites/{{sid}}/domains/platform", api_domain_platform)
    r.add_post(f"{A}/domains/{{did}}/verify", api_domain_verify)
    r.add_post(f"{A}/domains/{{did}}/primary", api_domain_primary)
    r.add_post(f"{A}/domains/{{did}}/delete", api_domain_delete)

    r.add_get(f"{A}/sites/{{sid}}/github", api_github_get)
    r.add_post(f"{A}/sites/{{sid}}/github", api_github_link)
    r.add_post(f"{A}/sites/{{sid}}/github/inspect", api_github_inspect)
    r.add_post(f"{A}/sites/{{sid}}/github/webhook", api_github_webhook)
    r.add_get(f"{A}/sites/{{sid}}/logs", api_site_logs)
    r.add_get(f"{A}/sites/{{sid}}/search", api_file_search)
    r.add_post(f"{A}/sites/{{sid}}/github/sync", api_github_sync)
    r.add_post(f"{A}/sites/{{sid}}/github/toggle", api_github_toggle)
    r.add_post(f"{A}/sites/{{sid}}/github/unlink", api_github_unlink)

    r.add_get(f"{A}/plans", api_plans)
    r.add_get(f"{A}/subscription/history", api_sub_history)
    r.add_post(f"{A}/subscription/request", api_sub_request)

    r.add_get(f"{A}/admin/stats", api_admin_stats)
    r.add_get(f"{A}/admin/health", api_admin_health)
    r.add_get(f"{A}/admin/audit", api_admin_audit)
    r.add_post(f"{A}/admin/users/{{uid}}/message", api_admin_user_message)
    r.add_get(f"{A}/admin/users", api_admin_users)
    r.add_get(f"{A}/admin/users/{{uid}}", api_admin_user)
    r.add_post(f"{A}/admin/users/{{uid}}/ban", api_admin_user_ban)
    r.add_post(f"{A}/admin/users/{{uid}}/unban", api_admin_user_unban)
    r.add_post(f"{A}/admin/users/{{uid}}/plan", api_admin_user_plan)
    r.add_get(f"{A}/admin/sites", api_admin_sites)
    r.add_post(f"{A}/admin/sites/{{sid}}/suspend", api_admin_site_suspend)
    r.add_post(f"{A}/admin/sites/{{sid}}/unsuspend", api_admin_site_unsuspend)
    r.add_get(f"{A}/admin/requests", api_admin_requests)
    r.add_post(f"{A}/admin/requests/{{rid}}/approve", api_admin_request_approve)
    r.add_post(f"{A}/admin/requests/{{rid}}/reject", api_admin_request_reject)
    r.add_get(f"{A}/admin/plans", api_admin_plans)
    r.add_post(f"{A}/admin/plans", api_admin_plan_create)
    r.add_post(f"{A}/admin/plans/{{pid}}", api_admin_plan_update)
    r.add_post(f"{A}/admin/plans/{{pid}}/toggle", api_admin_plan_toggle)
    r.add_post(f"{A}/admin/plans/{{pid}}/default", api_admin_plan_default)
    r.add_post(f"{A}/admin/plans/{{pid}}/delete", api_admin_plan_delete)
    r.add_get(f"{A}/admin/pdomains", api_admin_pdomains)
    r.add_post(f"{A}/admin/pdomains", api_admin_pdomain_add)
    r.add_post(f"{A}/admin/pdomains/{{pdid}}/toggle", api_admin_pdomain_toggle)
    r.add_post(f"{A}/admin/pdomains/{{pdid}}/delete", api_admin_pdomain_delete)
    r.add_get(f"{A}/admin/texts", api_admin_texts)
    r.add_post(f"{A}/admin/texts/{{key}}", api_admin_text_set)
    r.add_post(f"{A}/admin/texts/{{key}}/reset", api_admin_text_reset)
    r.add_get(f"{A}/admin/settings", api_admin_settings)
    r.add_post(f"{A}/admin/settings", api_admin_settings_set)
    r.add_get(f"{A}/admin/notifs", api_admin_notifs)
    r.add_post(f"{A}/admin/notifs/read", api_admin_notifs_read)
    r.add_post(f"{A}/admin/broadcast", api_admin_broadcast)
    r.add_get(f"{A}/admin/broadcast", api_admin_broadcast_status)

    app.add_subapp("/panel", p)
    start_expiry_task(app)
