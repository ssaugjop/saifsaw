"""
SAWHost - Main Entry Point
===========================
Webhook Piggybacking: handle_in_background=False (طبقة أمان إضافية)
+ DPI Bypass (app/dpi_bypass.py): يحل مشكلة اتصال تيليجرام من جذورها
بتجاوز حجب SNI الخاص بـ HuggingFace، فيصبح كل اتصال صادر لتيليجرام
(get_file/download_file/send_message/set_webhook...) موثوقاً وسريعاً.

كل شيء (البوت + الداشبورد + استضافة المواقع) يعمل على نفس العملية
ونفس المنفذ الواحد عبر aiohttp فقط. لا Flask، لا Threads، لا منفذ ثانٍ.

أوضاع التشغيل (RUN_MODE):
  webhook  (الافتراضي)  HuggingFace Space: ويب هوك + لوحة ويب + استضافة مواقع.
  polling               تطوير محلي فقط.
  actions               GitHub Actions: Polling للبوت + ويب على localhost عبر Cloudflare Tunnel، يعمل RUN_SECONDS ثم يتوقف بنظافة بعد حفظ لقطة
                        في قناة التخزين، ويُسلّم الشعلة لدورة جديدة (انظر .github/workflows/bot.yml).

التخزين الدائم: app/tgstore.py — لقطات مشفّرة في قناة تلجرام خاصة تُستعاد تلقائياً عند الإقلاع.
"""
from __future__ import annotations
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.config import get_cfg
from app.security import effective_host
from app.dashboard import register_dashboard
from app.database import close_db, init_db
from app.dpi_bypass import apply_dpi_bypass
from app.handlers import BOT_COMMANDS, router
from app.middlewares import AuthMiddleware, LiveNotifMiddleware, MaintenanceMiddleware, RateLimitMiddleware
from app.routing import register_routing
from app.storage_admin import storage_router
from app.webpanel import MAX_UPLOAD, register_webpanel
from app import tgstore

_RUN_MODE = os.getenv("RUN_MODE", "webhook").lower()
_LOG_FMT = "%(asctime)s | %(levelname)s | %(message)s"
_LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
if _RUN_MODE == "actions":
    # لوج GitHub Actions في المستودعات العامة يراه الجميع: السجلات التفصيلية (فيها تحديثات المستخدمين)
    # تذهب لملف محلي فقط، ويُطبع للشاشة سطور آمنة قليلة عبر tgstore.ci()
    os.makedirs("data", exist_ok=True)
    logging.basicConfig(level=_LOG_LEVEL, format=_LOG_FMT,
                        handlers=[logging.FileHandler("data/bot.log", encoding="utf-8")])
else:
    logging.basicConfig(level=_LOG_LEVEL, format=_LOG_FMT)
log = logging.getLogger("sawhost")

_bot_inst: Bot | None = None
_dp_inst:  Dispatcher | None = None


def get_bot() -> Bot:
    global _bot_inst
    if not _bot_inst:
        cfg = get_cfg()
        _bot_inst = Bot(token=cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return _bot_inst


def get_dp() -> Dispatcher:
    global _dp_inst
    if not _dp_inst:
        _dp_inst = Dispatcher(storage=MemoryStorage())
        _dp_inst.update.outer_middleware(AuthMiddleware())
        _dp_inst.update.outer_middleware(MaintenanceMiddleware())
        _dp_inst.update.outer_middleware(RateLimitMiddleware())
        _dp_inst.update.outer_middleware(LiveNotifMiddleware())
        _dp_inst.include_router(router)
        _dp_inst.include_router(storage_router)   # 💾 التخزين السحابي (قناة تلجرام) في لوحة الأدمن
    return _dp_inst


def _harden_fs() -> None:
    """صلاحيات الملفات: عمليات المواقع (UID مختلف) لا يجب أن تقرأ قاعدة البيانات ولا النسخ الاحتياطية ولا السجلات."""
    import pathlib
    cfg = get_cfg()
    for path, mode in ((cfg.sites_root, 0o711), (cfg.backups_root, 0o700),
                       (str(pathlib.Path(cfg.backups_root).parent / "logs"), 0o700), ("/tmp/sawhost", 0o711)):
        try:
            pathlib.Path(path).mkdir(parents=True, exist_ok=True)
            os.chmod(path, mode)
        except OSError:
            pass


async def _on_startup(app: web.Application) -> None:
    """
    بعد تفعيل DPI Bypass، الاتصال الصادر لتيليجرام أصبح موثوقاً، لذا
    نضبط الـ webhook تلقائياً هنا مباشرة (لا حاجة لأي خطوة يدوية من
    المتصفح بعد الآن).
    """
    try:
        await tgstore.boot(get_bot())     # يستعيد القاعدة/المواقع من قناة التخزين إن كانت مفقودة محلياً
    except Exception as e:
        log.warning("⚠️ تعذّرت استعادة التخزين السحابي: %s", e)
    _harden_fs()
    await init_db()
    from app.engines import Plans
    await Plans.seed_defaults()
    tgstore.start(get_bot())
    from app.process_manager import apps_enabled
    if not apps_enabled():
        log.warning("ℹ️ تشغيل تطبيقات Python/Node/PHP معطّل (لا يوجد عزل آمن). المواقع الثابتة فقط. للتفعيل شغّل كـ root أو ALLOW_APPS=1 على مسؤوليتك.")
    cfg = get_cfg()
    if cfg.public_domain and not cfg.edge_secret:
        log.warning("⚠️ PUBLIC_DOMAIN معيّن لكن EDGE_SECRET غير معيّن: لن تُصدَّق ترويسات المضيف/IP القادمة من الـ Worker.")

    if cfg.webhook_base_url and cfg.webhook_secret:
        target_url = cfg.base_url + cfg.webhook_path
        try:
            ok = await get_bot().set_webhook(
                url=target_url, secret_token=cfg.webhook_secret, drop_pending_updates=True,
            )
            if ok:
                log.info("✅ تم ضبط الويب هوك تلقائياً على: %s", target_url)
            else:
                log.warning("⚠️ رفض تيليجرام ضبط الويب هوك، تحقق يدوياً من الرابط.")
        except Exception as e:
            log.warning("⚠️ تعذّر ضبط الويب هوك تلقائياً (%s). اضبطه يدوياً عبر:\n"
                        "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=%s&secret_token=%s",
                        e, target_url, cfg.webhook_secret)
    # أوامر البوت + زر القائمة السفلي يفتح لوحة الويب مباشرة كتطبيق مصغّر
    try:
        await get_bot().set_my_commands(BOT_COMMANDS)
        if cfg.public_url.startswith("https://"):
            await get_bot().set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🖥 اللوحة", web_app=WebAppInfo(url=cfg.public_url + "/panel/")),
            )
    except Exception as e:
        log.warning("⚠️ تعذّر ضبط أوامر البوت/زر القائمة: %s", e)
    log.info("✅ SAWHost جاهز — DPI Bypass نشط، Piggybacking كطبقة أمان إضافية")


async def _on_shutdown(app: web.Application) -> None:
    if _bot_inst:
        await tgstore.stop(_bot_inst, final_save=True)   # لقطة أخيرة قبل إغلاق القاعدة
        await _bot_inst.session.close()
    import app.process_manager as pm
    await pm.stop_all()
    await close_db()


async def _root_redirect(req: web.Request) -> web.Response:
    """
    توجيه الرابط الرئيسي (/) للداشبورد، لكن فقط عندما يكون الطلب
    على دومين الـ Space نفسه — أي دومين مستخدم آخر مربوط بموقع
    مستضاف يمر بشكل طبيعي إلى site_handler في routing.py.
    """
    cfg = get_cfg()
    host = effective_host(req)
    if host in cfg.platform_hosts:
        raise web.HTTPFound("/panel/")
    # دومين خارجي: مرّر الطلب لمعالج المواقع المستضافة كأي مسار عادي
    from app.routing import site_handler
    return await site_handler(req)


async def _health(req: web.Request) -> web.Response:
    """مسار خفيف بلا مصادقة لمراقبة UptimeRobot — لا يلمس قاعدة البيانات."""
    return web.Response(text="OK", content_type="text/plain", headers={"Cache-Control": "no-store"})


@web.middleware
async def _security_headers(req: web.Request, handler):
    """رؤوس أمان عامة لكل الردود (لا تستبدل ما ضبطته المسارات بنفسها)."""
    try:
        resp = await handler(req)
    except web.HTTPException as e:
        resp = e
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if get_cfg().public_url.startswith("https://"):
        h.setdefault("Strict-Transport-Security", "max-age=15552000")
    if isinstance(resp, web.HTTPException):
        raise resp
    return resp


def create_app(web_only: bool = False) -> web.Application:
    """web_only=True (وضع GitHub Actions): الويب فقط — بلا ويب هوك ولا خطافات إقلاع/إغلاق
    (الإقلاع والإغلاق يديرهما _run_actions بنفسه، والبوت يعمل بـ Polling)."""
    cfg = get_cfg()
    app = web.Application(client_max_size=MAX_UPLOAD + 8 * 1024 * 1024, middlewares=[_security_headers])

    if not web_only:
        # 1) Webhook الخاص بالبوت (Piggybacking - handle_in_background=False)
        SimpleRequestHandler(
            dispatcher=get_dp(), bot=get_bot(),
            secret_token=cfg.webhook_secret,
            handle_in_background=False,   # ← يضمن أن الرد يُرسَل ضمن نفس HTTP response
        ).register(app, path=cfg.webhook_path)
        setup_application(app, get_dp(), bot=get_bot())

    # 1.2) مسار الصحة لـ UptimeRobot (GET + HEAD) — قبل الـ catch-all
    app.router.add_get("/health", _health)

    # 1.5) الرابط الرئيسي / - توجيه لدومين الـ Space فقط، وإلا معاملة عادية كموقع مستضاف
    app.router.add_get("/", _root_redirect)

    # 1.8) لوحة الويب الكاملة /panel (يجب أن تُسجَّل قبل الـ catch-all)
    register_webpanel(app, get_bot)

    # 2) الداشبورد - مسارات صريحة (يجب أن تُسجَّل قبل الـ catch-all في routing.py)
    register_dashboard(app)

    # 3) استضافة المواقع + روابط التحميل (catch-all - يجب أن يكون آخر شيء يُسجَّل)
    register_routing(app)

    if not web_only:
        app.on_startup.append(_on_startup)
        app.on_shutdown.append(_on_shutdown)
    return app


async def _run_actions() -> None:
    """وضع GitHub Actions: Polling + استعادة من القناة + لقطات تلقائية + إيقاف نظيف بعد RUN_SECONDS."""
    import asyncio
    bot, dp = get_bot(), get_dp()
    run_seconds = int(os.getenv("RUN_SECONDS", "20100"))          # 5س35د: يترك هامشاً قبل حدّ 6 ساعات

    # أي ويب هوك قديم (من الـ Space) يمنع getUpdates — نحذفه دون إسقاط الرسائل المعلّقة
    for attempt in range(5):
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            break
        except Exception as e:
            log.warning("delete_webhook failed (%s) attempt %d", e, attempt + 1)
            await asyncio.sleep(3)

    await tgstore.boot(bot)
    _harden_fs()
    await init_db()
    from app.engines import Plans
    await Plans.seed_defaults()
    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception as e:
        log.warning("set_my_commands failed: %s", e)
    tgstore.start(bot)

    # الويب (لوحة /panel + استضافة المواقع) على localhost فقط: يصل إليه الزوار عبر Cloudflare Tunnel
    # (خطوة cloudflared في bot.yml). لتعطيله: WEB_ENABLED=0
    runner: web.AppRunner | None = None
    cfg = get_cfg()
    if os.getenv("WEB_ENABLED", "1") != "0":
        try:
            runner = web.AppRunner(create_app(web_only=True), access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", cfg.port).start()
            tgstore.ci(f"الويب يعمل على المنفذ {cfg.port} (عبر النفق).")
        except Exception as e:
            runner = None
            log.warning("web server failed to start: %s", e)
            tgstore.ci(f"تعذّر تشغيل الويب: {type(e).__name__}")
        if cfg.public_url.startswith("https://"):
            try:
                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text="🖥 اللوحة", web_app=WebAppInfo(url=cfg.public_url + "/panel/")),
                )
            except Exception as e:
                log.warning("set_chat_menu_button failed: %s", e)

    tgstore.ci(f"البوت يعمل (polling) لمدة {run_seconds // 60} دقيقة ثم يسلّم الشعلة.")

    async def _timer() -> None:
        await asyncio.sleep(run_seconds)
        tgstore.ci("انتهت مدة الدورة — إيقاف نظيف.")
        res = dp.stop_polling()
        if asyncio.iscoroutine(res):
            await res

    timer = asyncio.create_task(_timer())
    try:
        await dp.start_polling(bot, handle_signals=True, close_bot_session=False,
                               allowed_updates=dp.resolve_used_update_types())
    finally:
        timer.cancel()
        try:                                  # أوقف تطبيقات المستخدمين بنظافة قبل إغلاق الويب والقاعدة
            import app.process_manager as pm
            await pm.stop_all()
        except Exception as e:
            log.warning("stop_all failed: %s", e)
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception as e:
                log.warning("web cleanup failed: %s", e)
        await tgstore.stop(bot, final_save=True)
        await close_db()
        await bot.session.close()
        tgstore.ci("تم الإيقاف.")


def main() -> None:
    cfg = get_cfg()
    # لا نوقف التشغيل (كي لا ينكسر نشر قائم)، لكن نحذّر: المفتاح القصير أضعف أمنياً.
    if len(cfg.secret_key) < 32:
        log.warning("⚠️ SECRET_KEY قصير (%d حرفاً). يُنصح بـ 32+ حرفاً عشوائياً. تغييره يُبطل الجلسات وتوكنات GitHub المحفوظة.", len(cfg.secret_key))
    if os.getenv("RUN_MODE", "webhook").lower() not in ("polling", "actions") and len(cfg.webhook_secret) < 16:
        log.warning("⚠️ WEBHOOK_SECRET قصير أو غير معيّن (يُنصح بـ 16+ حرفاً).")

    if os.getenv("RUN_MODE", "webhook").lower() == "actions":
        import asyncio
        asyncio.run(_run_actions())     # لا DPI Bypass هنا: GitHub لا يحجب تيليجرام
        return

    log.info("🔌 جارِ اختبار الاتصال المباشر بخوادم تيليجرام (DPI Bypass)...")
    chosen_ip = apply_dpi_bypass()
    log.info("🔌 DPI Bypass نشط على العنوان: %s", chosen_ip)

    if os.getenv("RUN_MODE", "webhook").lower() == "polling":
        # وضع Polling: للتطوير المحلي فقط (على جهازك الشخصي، ليس HuggingFace)
        import asyncio

        async def _poll():
            await init_db()
            bot = get_bot()
            dp = get_dp()
            await bot.delete_webhook(drop_pending_updates=True)
            log.info("Polling mode (تطوير فقط)")
            try:
                await dp.start_polling(bot)
            finally:
                await bot.session.close()
                await close_db()

        asyncio.run(_poll())
        return

    port = cfg.port
    log.info("🚀 SAWHost — المنفذ %d", port)
    web.run_app(create_app(), host="0.0.0.0", port=port, access_log=None)


if __name__ == "__main__":
    main()
