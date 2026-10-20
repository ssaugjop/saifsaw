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
import mimetypes, os, secrets, time
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

# ── خدمة الملفات الثابتة ───────────────────────────────────────────────────────
for _ext, _mt in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".wasm", "application/wasm"),
                  (".webmanifest", "application/manifest+json"), (".json", "application/json"),
                  (".map", "application/json"), (".svg", "image/svg+xml"), (".webp", "image/webp"),
                  (".avif", "image/avif"), (".woff", "font/woff"), (".woff2", "font/woff2"),
                  (".ico", "image/x-icon"), (".md", "text/plain")):
    mimetypes.add_type(_mt, _ext)

_COMPRESSIBLE = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".svg", ".txt", ".xml", ".map",
                 ".csv", ".webmanifest", ".md"}
_MAX_COMPRESS = 2 * 1024 * 1024
# مسار بلا امتداد غير موجود → الصفحة الرئيسية (تطبيقات SPA: React/Vue/Angular). عطّله بـ SPA_FALLBACK=0
_SPA_FALLBACK = os.getenv("SPA_FALLBACK", "1") != "0"


def _blocked_path(rel: str) -> bool:
    """يمنع تقديم الملفات/المجلدات المخفية (.env, .git, .htpasswd…) ويسمح فقط بـ .well-known."""
    for part in rel.replace("\\", "/").split("/"):
        if part.startswith(".") and part not in ("", ".well-known"):
            return True
    return False


async def _static_file(req: web.Request, target: Path, status: int = 200) -> web.StreamResponse:
    """ملف ثابت بترويسات كاش صحيحة، ETag/304، وضغط gzip للنصوص الصغيرة. يدعم Range للملفات الكبيرة."""
    ext = target.suffix.lower()
    ct = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    headers = {
        "X-Frame-Options": "SAMEORIGIN",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-cache" if (ext in (".html", ".htm") or status != 200) else "public, max-age=3600",
    }
    try:
        st = target.stat()
    except OSError:
        return web.Response(status=404, content_type="text/html", text="<h2>404 — الملف غير موجود.</h2>")

    if ext in _COMPRESSIBLE and st.st_size <= _MAX_COMPRESS and "Range" not in req.headers:
        etag = f'W/"{st.st_size:x}-{st.st_mtime_ns:x}"'
        headers["ETag"] = etag
        headers["Vary"] = "Accept-Encoding"
        if status == 200 and req.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers=headers)
        try:
            data = await asyncio.to_thread(target.read_bytes)
        except OSError:
            return web.Response(status=404, content_type="text/html", text="<h2>404 — الملف غير موجود.</h2>")
        resp = web.Response(body=data, status=status, headers=headers, content_type=ct)
        resp.enable_compression()
        return resp

    headers["Content-Type"] = ct
    return web.FileResponse(path=target, status=status, headers=headers)


async def _static_404(req: web.Request, site) -> web.StreamResponse:
    """404.html المخصصة للموقع إن وُجدت، وإلا صفحة عامة بلا كشف لأسماء الملفات."""
    try:
        custom = Storage.resolve(site.id, "404.html")
        if custom.is_file():
            return await _static_file(req, custom, status=404)
    except Exception:
        pass
    return web.Response(status=404, content_type="text/html", text="<h2>404 — الصفحة غير موجودة.</h2>")


async def _serve_static(req: web.Request, site, tail: str) -> web.StreamResponse:
    if req.method not in ("GET", "HEAD"):
        return web.Response(status=405, text="Method Not Allowed", headers={"Allow": "GET, HEAD"})
    rel = (tail or "").lstrip("/")
    if _blocked_path(rel):
        return await _static_404(req, site)

    target: Path | None
    if not rel:
        target = await Storage.resolve_homepage(site.id)
        if not target:
            return web.Response(status=404, content_type="text/html", text=(
                "<h2>404 — لم يُعثَر على أي صفحة رئيسية بجذر الموقع.</h2>"
                "<p>ارفع أي ملف HTML بجذر الموقع (أياً كان اسمه) وسيُعرَض تلقائياً.</p>"))
    else:
        try:
            target = Storage.resolve(site.id, rel)
        except Exception:
            return web.Response(status=400, text="مسار غير صالح.")
        if target.is_dir():
            target = next((target / c for c in _INDEX if (target / c).is_file()), None)

    if target is None or not target.is_file():
        last = rel.rsplit("/", 1)[-1]
        if _SPA_FALLBACK and rel and "." not in last:
            home = await Storage.resolve_homepage(site.id)
            if home and home.is_file():
                return await _static_file(req, home)
        return await _static_404(req, site)
    return await _static_file(req, target)


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

    return await _serve_static(req, site, req.match_info.get("tail", ""))


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
        return await _serve_static(req, site, tail)

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


_proxy_session = None
_PROXY_BODY_MAX = 50 * 1024 * 1024
_WS_SKIP = {"upgrade", "connection", "sec-websocket-key", "sec-websocket-version",
            "sec-websocket-extensions", "sec-websocket-protocol", "sec-websocket-accept"}
_NO_BODY = {204, 304}


def _get_proxy_session():
    """جلسة aiohttp واحدة مشتركة (اتصالات keep-alive) بدل إنشاء جلسة لكل طلب."""
    global _proxy_session
    import aiohttp as _aiohttp
    if _proxy_session is None or _proxy_session.closed:
        _proxy_session = _aiohttp.ClientSession(
            auto_decompress=False,
            connector=_aiohttp.TCPConnector(limit=200, ttl_dns_cache=300),
        )
    return _proxy_session


async def _close_proxy_session(_app=None) -> None:
    global _proxy_session
    if _proxy_session is not None and not _proxy_session.closed:
        await _proxy_session.close()
    _proxy_session = None


def _upstream_headers(req: web.Request, skip: set[str]) -> dict:
    headers = {k: v for k, v in req.headers.items() if k.lower() not in skip}
    if "Cookie" in headers:
        ck = _strip_panel_cookie(headers["Cookie"])
        if ck: headers["Cookie"] = ck
        else: headers.pop("Cookie")
    public_host = effective_host(req)
    headers["Host"] = public_host                     # روابط وإعادة توجيه التطبيق تخرج بالدومين الحقيقي لا 127.0.0.1
    headers["X-Forwarded-Host"] = public_host
    headers["X-Forwarded-For"] = (req.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or req.remote or "")
    headers["X-Forwarded-Proto"] = req.headers.get("X-Forwarded-Proto", req.scheme)
    return headers


async def _proxy_ws(req: web.Request, port: int, safe_tail: str) -> web.StreamResponse:
    """WebSocket: وصلة ثنائية الاتجاه بين الزائر وتطبيق المستخدم."""
    import aiohttp as _aiohttp
    url = f"http://127.0.0.1:{port}/{safe_tail}" + (f"?{req.query_string}" if req.query_string else "")
    protos = [p.strip() for p in req.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
    try:
        up = await _get_proxy_session().ws_connect(
            url, headers=_upstream_headers(req, _HOP | _WS_SKIP | {"accept-encoding"}),
            protocols=protos, max_msg_size=4 * 1024 * 1024, heartbeat=30, timeout=10)
    except Exception:
        return web.Response(status=502, text="التطبيق لا يستجيب.")
    ws = web.WebSocketResponse(protocols=(up.protocol,) if up.protocol else (), max_msg_size=4 * 1024 * 1024, heartbeat=30)
    await ws.prepare(req)

    async def pump(src, dst) -> None:
        async for msg in src:
            if msg.type == _aiohttp.WSMsgType.TEXT:
                await dst.send_str(msg.data)
            elif msg.type == _aiohttp.WSMsgType.BINARY:
                await dst.send_bytes(msg.data)
            elif msg.type in (_aiohttp.WSMsgType.CLOSE, _aiohttp.WSMsgType.CLOSING,
                              _aiohttp.WSMsgType.CLOSED, _aiohttp.WSMsgType.ERROR):
                break

    tasks = {asyncio.create_task(pump(ws, up)), asyncio.create_task(pump(up, ws))}
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await up.close()
        with contextlib.suppress(Exception):
            await ws.close()
    return ws


async def _proxy_request(req: web.Request, port: int, tail: str) -> web.StreamResponse:
    """يمرر الطلب لعملية المستخدم المعزولة على 127.0.0.1:port (بثّ للرد، WebSocket، SSE)."""
    import aiohttp as _aiohttp
    from multidict import CIMultiDict

    # تعقيم tail: يجب أن يبقى مساراً نسبياً بحتاً، لمنع أي محاولة SSRF
    safe_tail = tail.lstrip("/")
    if "://" in safe_tail or safe_tail.startswith("//") or "\r" in safe_tail or "\n" in safe_tail:
        return web.Response(status=400, text="مسار غير صالح.")

    if req.headers.get("Upgrade", "").lower() == "websocket":
        return await _proxy_ws(req, port, safe_tail)

    if (req.content_length or 0) > _PROXY_BODY_MAX:
        return web.Response(status=413, text="حجم الطلب كبير جداً.")

    target_url = f"http://127.0.0.1:{port}/{safe_tail}"
    if req.query_string:
        target_url += f"?{req.query_string}"

    body = await req.read()
    headers = _upstream_headers(req, _HOP)

    try:
        async with _get_proxy_session().request(
            req.method, target_url, headers=headers, data=body or None,
            timeout=_aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=120),
            allow_redirects=False,
        ) as resp:
            out = CIMultiDict()
            for k, v in resp.headers.items():
                kl = k.lower()
                if kl in ("transfer-encoding", "connection", "keep-alive"):
                    continue
                if kl == "set-cookie" and v.strip().startswith(_PANEL_COOKIE + "="):
                    continue    # تطبيق مستضاف لا يحق له كتابة كوكي جلسة اللوحة (cookie tossing)
                out.add(k, v)
            out["X-Content-Type-Options"] = "nosniff"

            if req.method == "HEAD" or resp.status in _NO_BODY or resp.status < 200:
                return web.Response(status=resp.status, headers=out)

            sresp = web.StreamResponse(status=resp.status, headers=out)
            await sresp.prepare(req)
            try:
                async for chunk in resp.content.iter_chunked(65536):
                    await sresp.write(chunk)
                await sresp.write_eof()
            except (ConnectionResetError, asyncio.CancelledError):
                raise
            except Exception:
                pass                       # انقطع التطبيق أثناء البث: أغلق الاتصال بهدوء
            return sresp
    except (ConnectionResetError, asyncio.CancelledError):
        raise
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
    app.on_cleanup.append(_close_proxy_session)
    app.router.add_get("/dl/{token}", download_handler)
    app.router.add_post("/gh/{site_id}", github_webhook_handler)
    # التوجيه الجديد بالمسار (path-based) - الأسلوب الرسمي الحالي
    app.router.add_route("*", "/s/{slug}", path_site_handler)
    app.router.add_route("*", "/s/{slug}/{tail:.*}", path_site_handler)
    # التوجيه القديم بالدومين الفرعي يبقى مدعوماً للتوافق العكسي
    app.router.add_route("*", "/{tail:.*}", site_handler)
