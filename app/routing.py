"""
SAWHost - Routing Server + Download Tokens
=============================================
يخدم على نفس المنفذ:
  GET  /dl/<token>     → تحميل مباشر للملفات (بدل إرسالها عبر تيليجرام)
  GET  /*               → المواقع المستضافة حسب Host header أو المسار
"""
from __future__ import annotations
import asyncio
import contextlib
import html as _h
import json
import logging
import mimetypes, secrets, time
from pathlib import Path

from aiohttp import web

from app.config import get_cfg
from app.engines import Domains, Storage
from app.models import SiteStatus
from app.security import effective_host, safe_filename

log = logging.getLogger("sawhost.routing")
_bg_tasks: set = set()

# ── Download Token Store ──────────────────────────────────────────────────────
_store: dict[str, dict] = {}
_TTL = 3600  # ساعة واحدة


def mk_token(file_path: str, filename: str) -> str:
    """يولّد token مؤقت لتحميل ملف."""
    tok = secrets.token_urlsafe(24)
    _store[tok] = {"path": file_path, "name": filename, "exp": time.time() + _TTL}
    return tok


def mk_zip_token(zip_bytes: bytes, filename: str) -> str:
    """يحفظ ZIP مؤقتاً ويولّد token."""
    tmp = Path("/tmp/sawhost")
    try: tmp.mkdir(parents=True, exist_ok=True)
    except OSError: tmp = Path("/data/tmp"); tmp.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError): tmp.chmod(0o711)      # لا يمكن سرد الملفات من عمليات المستخدمين
    p = tmp / f"{secrets.token_hex(16)}.zip"
    p.write_bytes(zip_bytes)
    with contextlib.suppress(OSError): p.chmod(0o600)
    return mk_token(str(p), filename)


def _resolve_token(tok: str) -> tuple[str, str] | None:
    now = time.time()
    expired = [k for k, v in _store.items() if v["exp"] < now]
    for k in expired:
        try: Path(_store[k]["path"]).unlink(missing_ok=True)
        except Exception: pass
        _store.pop(k, None)
    entry = _store.get(tok)
    if not entry: return None
    if time.time() > entry["exp"]:
        _store.pop(tok, None); return None
    return entry["path"], entry["name"]


# ── Handlers ──────────────────────────────────────────────────────────────────
_INDEX = ("index.html", "index.htm")


async def download_handler(req: web.Request) -> web.Response:
    tok    = req.match_info.get("token", "")
    result = _resolve_token(tok)
    if not result:
        return web.Response(status=404, text="❌ الرابط منتهي الصلاحية أو غير صالح.")
    path, name = result
    p = Path(path)
    if not p.exists() or not p.is_file():
        return web.Response(status=404, text="الملف غير موجود.")
    from urllib.parse import quote
    safe = safe_filename(name, "download")
    ct, _ = mimetypes.guess_type(safe)
    return web.FileResponse(path=p, headers={
        "Content-Disposition": f"attachment; filename=\"{safe.encode('ascii', 'replace').decode()}\"; filename*=UTF-8''{quote(safe)}",
        "Content-Type": ct or "application/octet-stream",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
    })


def _wildcard_slug(host: str) -> str | None:
    """يستخرج slug من <slug>.<SITES_DOMAIN>، أو None إن لم يكن المضيف تحت دومين المواقع."""
    sd = get_cfg().sites_domain
    if sd and host.endswith("." + sd):
        slug = host[: -(len(sd) + 1)]
        if slug and "." not in slug:
            return slug
    return None


async def site_handler(req: web.Request) -> web.Response:
    host = effective_host(req)
    slug = _wildcard_slug(host)
    if slug:
        return await _serve_slug(req, slug, req.match_info.get("tail", ""))
    if req.method not in ("GET", "HEAD"):
        return web.Response(status=405, text="Method Not Allowed", headers={"Allow": "GET, HEAD"})
    site = await Domains.resolve_host(host)
    if site is None:
        return web.Response(status=404, content_type="text/html",
            text="<h2>404 — هذا الدومين غير مرتبط بأي موقع على SAWHost.</h2>")
    if site.status == SiteStatus.SUSPENDED:
        return web.Response(status=403, content_type="text/html",
            text="<h2>🚫 هذا الموقع موقوف من إدارة المنصة.</h2>")
    if site.status != SiteStatus.RUNNING:
        return web.Response(status=503, content_type="text/html",
            text="<h2>⏸ هذا الموقع متوقف حاليًا.</h2>")

    rel = req.match_info.get("tail", "").lstrip("/")
    if not rel:
        target = await Storage.resolve_homepage(site.id)
        if not target:
            listing = ""
            try:
                names = [p.name for p in Storage.site_root(site.id).iterdir()][:20]
                if names:
                    listing = "<p>الملفات الموجودة بجذر الموقع:</p><ul>" + "".join(f"<li>{_h.escape(n)}</li>" for n in names) + "</ul>"
            except Exception:
                pass
            return web.Response(status=404, content_type="text/html", text=(
                "<h2>404 — لم يُعثَر على أي صفحة رئيسية بجذر الموقع.</h2>"
                "<p>ارفع أي ملف HTML بجذر الموقع (أياً كان اسمه) وسيُعرَض تلقائياً.</p>" + listing
            ))
    else:
        try: target = Storage.resolve(site.id, rel)
        except Exception:
            return web.Response(status=400, text="مسار غير صالح.")
        if target.is_dir():
            target = next((target / c for c in _INDEX if (target / c).exists()), None)
            if not target:
                return web.Response(status=404, text="لا يوجد ملف index في هذا المجلد.")

    if not target.exists() or not target.is_file():
        return web.Response(status=404, content_type="text/html",
            text=f"<h2>404 — الملف غير موجود.</h2>")

    ct, _ = mimetypes.guess_type(str(target))
    return web.FileResponse(path=target, headers={
        "Content-Type": ct or "application/octet-stream",
        "X-Frame-Options": "SAMEORIGIN",
        "X-Content-Type-Options": "nosniff",
    })


async def path_site_handler(req: web.Request) -> web.Response:
    """
    التوجيه بالمسار: /s/<site-slug>/*
    إن عُيّن SITES_DOMAIN نحوّل الزائر إلى <slug>.<SITES_DOMAIN> (أصل مستقل عن اللوحة)،
    فلا يعمل كود أي موقع مستضاف على نفس أصل لوحة التحكم.
    """
    slug = req.match_info.get("slug", "")
    tail = req.match_info.get("tail", "")
    sd = get_cfg().sites_domain
    if sd and req.method in ("GET", "HEAD"):
        qs = ("?" + req.query_string) if req.query_string else ""
        raise web.HTTPMovedPermanently(f"https://{slug}.{sd}/{tail.lstrip('/')}{qs}")
    return await _serve_slug(req, slug, tail)


async def _serve_slug(req: web.Request, slug: str, tail: str) -> web.Response:
    """
    - مواقع Static: تُخدَم كملفات مباشرة.
    - مواقع Python/Node/PHP: عملية معزولة + Reverse Proxy على 127.0.0.1 فقط.
    """
    from app.models import SiteKind, Site
    from app.database import db
    from sqlalchemy import select

    async with db() as s:
        r = await s.execute(select(Site).where(Site.slug == slug, Site.is_deleted.is_(False)))
        site = r.scalar_one_or_none()

    if site is None:
        return web.Response(status=404, content_type="text/html",
            text="<h2>404 — لا يوجد موقع بهذا الاسم على SAWHost.</h2>")
    if site.status == SiteStatus.SUSPENDED:
        return web.Response(status=403, content_type="text/html",
            text="<h2>🚫 هذا الموقع موقوف من إدارة المنصة.</h2>")

    if site.kind == SiteKind.STATIC:
        if site.status != SiteStatus.RUNNING:
            return web.Response(status=503, content_type="text/html",
                text="<h2>⏸ هذا الموقع متوقف حالياً.</h2>")
        rel = tail.lstrip("/")
        if not rel:
            target = await Storage.resolve_homepage(site.id)
            if not target:
                listing = ""
                with contextlib.suppress(Exception):
                    names = [p.name for p in Storage.site_root(site.id).iterdir()][:20]
                    if names:
                        listing = "<p>الملفات الموجودة بجذر الموقع:</p><ul>" + "".join(f"<li>{_h.escape(n)}</li>" for n in names) + "</ul>"
                return web.Response(status=404, content_type="text/html", text=(
                    "<h2>404 — لم يُعثَر على أي صفحة رئيسية بجذر الموقع.</h2>"
                    "<p>ارفع أي ملف HTML بجذر الموقع (أياً كان اسمه) وسيُعرَض تلقائياً.</p>" + listing
                ))
        else:
            try: target = Storage.resolve(site.id, rel)
            except Exception:
                return web.Response(status=400, text="مسار غير صالح.")
            if target.is_dir():
                target = next((target / c for c in _INDEX if (target / c).exists()), None)
                if not target:
                    return web.Response(status=404, text="لا يوجد ملف index في هذا المجلد.")
        if not target.exists() or not target.is_file():
            listing = ""
            with contextlib.suppress(Exception):
                names = [p.name for p in Storage.site_root(site.id).iterdir()][:20]
                if names:
                    listing = "<p>الملفات الموجودة بجذر الموقع:</p><ul>" + "".join(f"<li>{_h.escape(n)}</li>" for n in names) + "</ul>"
            return web.Response(status=404, content_type="text/html", text=(
                "<h2>404 — الملف غير موجود.</h2>" + listing
            ))
        ct, _ = mimetypes.guess_type(str(target))
        return web.FileResponse(path=target, headers={
            "Content-Type": ct or "application/octet-stream",
            "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff",
        })

    # تطبيقات Python/Node/PHP: تشغيل معزول + Reverse Proxy (غير محجوب بالكامل)
    import app.process_manager as pm
    port = pm.get_port(str(site.id))
    if port is not None:
        pm.touch(str(site.id))
        return await _proxy_request(req, port, tail)

    if site.status not in (SiteStatus.RUNNING, SiteStatus.DEPLOYING):
        return web.Response(status=503, content_type="text/html",
            text="<h2>⏸ هذا الموقع متوقف حالياً. شغّله من البوت أولاً.</h2>")

    # لم تُشغَّل بعد أو ما زالت قيد النشر: نُطلِق المحاولة بالخلفية (إن لم
    # تكن جارية أصلاً) ونعرض صفحة انتظار تُحدِّث نفسها تلقائياً كل 3 ثوانٍ،
    # بدل حجب متصفح الزائر لحين اكتمال النشر (قد يستغرق دقيقة أو أكثر).
    site_root = Storage.site_root(site.id)
    status = pm.get_status(str(site.id))
    if status["state"] == "idle":
        pm.trigger_start(str(site.id), site_root, site.kind.value)
    elif status["state"] == "error":
        return web.Response(status=502, content_type="text/html", text=(
            f"<h2>⚠️ تعذّر تشغيل التطبيق</h2><pre style='white-space:pre-wrap'>{_h.escape(status.get('message') or 'خطأ غير معروف')}</pre>"
        ))

    return web.Response(status=200, content_type="text/html", text=(
        "<!DOCTYPE html><html lang='ar' dir='rtl'><head><meta charset='UTF-8'>"
        "<meta http-equiv='refresh' content='3'>"
        "<title>جارِ التشغيل...</title>"
        "<style>body{font-family:system-ui;background:#0f172a;color:#f1f5f9;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
        ".c{text-align:center}</style></head><body><div class='c'>"
        "<h2>🟡 جارِ تشغيل التطبيق...</h2><p>سيُعاد تحميل الصفحة تلقائياً خلال ثوانٍ</p>"
        "</div></body></html>"
    ))


_HOP = {"host", "content-length", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "x-saw-csrf"}
_PANEL_COOKIE = "sw_session"


def _strip_panel_cookie(raw: str) -> str:
    """يحذف كوكي جلسة اللوحة من الكوكيز الممرَّرة للتطبيق المستضاف."""
    return "; ".join(c.strip() for c in raw.split(";") if c.strip() and not c.strip().startswith(_PANEL_COOKIE + "="))


async def _proxy_request(req: web.Request, port: int, tail: str) -> web.Response:
    """يمرر الطلب فعلياً لعملية المستخدم المعزولة على 127.0.0.1:port."""
    import aiohttp as _aiohttp
    from multidict import CIMultiDict

    # تعقيم tail: يجب أن يبقى مساراً نسبياً بحتاً، لمنع أي محاولة SSRF
    safe_tail = tail.lstrip("/")
    if "://" in safe_tail or safe_tail.startswith("//") or "\r" in safe_tail or "\n" in safe_tail:
        return web.Response(status=400, text="مسار غير صالح.")

    target_url = f"http://127.0.0.1:{port}/{safe_tail}"
    if req.query_string:
        target_url += f"?{req.query_string}"

    body = await req.read()
    headers = {k: v for k, v in req.headers.items() if k.lower() not in _HOP}
    if "Cookie" in headers:
        ck = _strip_panel_cookie(headers["Cookie"])
        if ck: headers["Cookie"] = ck
        else: headers.pop("Cookie")
    headers["X-Forwarded-For"] = (req.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or req.remote or "")
    headers["X-Forwarded-Proto"] = req.headers.get("X-Forwarded-Proto", req.scheme)

    try:
        async with _aiohttp.ClientSession(auto_decompress=False) as session:
            async with session.request(
                req.method, target_url, headers=headers, data=body or None,
                timeout=_aiohttp.ClientTimeout(total=25),
                allow_redirects=False,
            ) as resp:
                resp_body = await resp.read()
                out = CIMultiDict()
                for k, v in resp.headers.items():
                    kl = k.lower()
                    if kl in ("content-length", "transfer-encoding", "connection", "keep-alive"):
                        continue
                    if kl == "set-cookie" and v.strip().startswith(_PANEL_COOKIE + "="):
                        continue    # تطبيق مستضاف لا يحق له كتابة كوكي جلسة اللوحة (cookie tossing)
                    out.add(k, v)
                out["X-Content-Type-Options"] = "nosniff"
                return web.Response(status=resp.status, body=resp_body, headers=out)
    except Exception:
        return web.Response(status=502, content_type="text/html",
            text="<h2>⚠️ التطبيق لا يستجيب حالياً. حاول تحديث الصفحة خلال لحظات.</h2>")


_WH_MAX = 1024 * 1024


async def _webhook_sync(site_id, owner_id) -> None:
    from app.engines import GithubIntegration, Host
    try:
        await GithubIntegration.sync_now(site_id, owner_id, "webhook")
        fresh = await Host.get(site_id)
        if fresh.kind.value != "static":
            with contextlib.suppress(Exception):
                await Host.restart(site_id)
    except Exception as e:
        log.info("webhook sync failed for %s: %s", site_id, e)


async def github_webhook_handler(req: web.Request) -> web.Response:
    """
    يستقبل GitHub Webhook (push) ويزامن الموقع تلقائياً.
    الحماية: توقيع HMAC-SHA256 (X-Hub-Signature-256) بمقارنة ثابتة الزمن، سقف 1MB للجسم،
    وتحقق التوقيع قبل أي عمل ثقيل. المزامنة تُنفَّذ بالخلفية (GitHub يمهل 10 ثوانٍ فقط)
    مع قفل لكل موقع داخل sync_now.
    """
    from app.engines import GithubIntegration, Host
    if (req.content_length or 0) > _WH_MAX:
        return web.Response(status=413, text="الجسم كبير.")
    link = await GithubIntegration.find_by_secret_hint(req.match_info.get("site_id", ""))
    if not link:
        return web.Response(status=404, text="غير موجود.")
    body = await req.read()
    if len(body) > _WH_MAX:
        return web.Response(status=413, text="الجسم كبير.")
    if not GithubIntegration.verify_webhook_signature(link.webhook_secret, body, req.headers.get("X-Hub-Signature-256")):
        return web.Response(status=401, text="توقيع غير صالح.")

    event = req.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return web.Response(status=200, text="pong")
    if event != "push":
        return web.Response(status=200, text="تم التجاهل (ليس push).")
    if not link.auto_sync:
        return web.Response(status=200, text="التحديث التلقائي معطَّل.")
    try:
        ref = json.loads(body).get("ref", "")
        if ref and ref != f"refs/heads/{link.branch}":
            return web.Response(status=200, text="فرع مختلف، تم التجاهل.")
    except Exception:
        return web.Response(status=400, text="حمولة غير صالحة.")

    try:
        site = await Host.get(link.site_id)
    except Exception:
        return web.Response(status=404, text="الموقع غير موجود.")
    t = asyncio.create_task(_webhook_sync(link.site_id, site.owner_id))
    _bg_tasks.add(t); t.add_done_callback(_bg_tasks.discard)
    return web.Response(status=202, text="تمت جدولة المزامنة.")


def register_routing(app: web.Application) -> None:
    app.router.add_get("/dl/{token}", download_handler)
    app.router.add_post("/gh/{site_id}", github_webhook_handler)
    # التوجيه الجديد بالمسار (path-based) - الأسلوب الرسمي الحالي
    app.router.add_route("*", "/s/{slug}", path_site_handler)
    app.router.add_route("*", "/s/{slug}/{tail:.*}", path_site_handler)
    # التوجيه القديم بالدومين الفرعي يبقى مدعوماً للتوافق العكسي
    app.router.add_route("*", "/{tail:.*}", site_handler)
