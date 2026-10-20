"""
SAWHost - Telegram Background File Fetcher
=============================================
يستقبل ملفات المستخدمين من تيليجرام مباشرة فقط — بدون أي رابط رفع بديل
عبر المتصفح إطلاقاً. الفلسفة: الاتصال الصادر لتيليجرام من هذه البيئة
غير مستقر (ينجح غالباً، يتعثر أحياناً) وليس محظوراً بشكل قاطع؛ لذلك بدل
الاستسلام لرابط بديل بعد مهلة قصيرة، تعمل إعادة المحاولة **بالكامل
بالخلفية** (asyncio.create_task) دون حجب أي رد على المستخدم، لمدة كافية
جداً (حتى دقيقتين) قبل الإبلاغ بفشل حقيقي فعلي.

يعمل بنفس فلسفة process_manager.py تماماً: إطلاق فوري (trigger) + استطلاع
حالة فوري (get_status) — لا يوجد أي انتظار مباشر داخل أي معالج زر.
"""
from __future__ import annotations
import asyncio
import secrets
import time

MAX_ATTEMPTS   = 12    # عدد محاولات التحميل من تيليجرام
ATTEMPT_TIMEOUT= 8.0   # مهلة كل محاولة (ثوانٍ)
RETRY_GAP      = 2.0   # فاصل بسيط بين المحاولات (ثوانٍ)
RESULT_TTL     = 600   # مدة الاحتفاظ بنتيجة مكتملة قبل تنظيفها تلقائياً (ثانية)

# token -> {"state": pending|done|error, "data": bytes|None, "error": str|None,
#           "telegram_id": int, "kind": str, "meta": dict, "ts": float}
_fetches: dict[str, dict] = {}


def _cleanup() -> None:
    now = time.monotonic()
    stale = [k for k, v in _fetches.items() if now - v["ts"] > RESULT_TTL]
    for k in stale:
        _fetches.pop(k, None)


def start_fetch(bot, file_id: str, telegram_id: int, kind: str, meta: dict) -> str:
    """
    يُطلَق فوراً عند استلام أي مستند من المستخدم. غير محجوب بالكامل:
    يُرجع token فوري (أقل من 1ms) بينما تعمل عملية التحميل الفعلية
    كمهمة خلفية منفصلة تماماً.
    """
    _cleanup()
    token = secrets.token_hex(8)
    _fetches[token] = {
        "state": "pending", "data": None, "error": None,
        "telegram_id": telegram_id, "kind": kind, "meta": meta, "ts": time.monotonic(),
    }
    asyncio.create_task(_do_fetch(bot, file_id, token))
    return token


async def _do_fetch(bot, file_id: str, token: str) -> None:
    entry = _fetches.get(token)
    if entry is None:
        return
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            fi = await asyncio.wait_for(bot.get_file(file_id), timeout=ATTEMPT_TIMEOUT)
            buf = await asyncio.wait_for(bot.download_file(fi.file_path), timeout=ATTEMPT_TIMEOUT)
            entry["data"] = buf.read()
            entry["state"] = "done"
            entry["ts"] = time.monotonic()
            return
        except Exception as e:
            last_err = str(e)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_GAP)
    entry["state"] = "error"
    entry["error"] = last_err or "فشل التحميل من تيليجرام بعد عدة محاولات."
    entry["ts"] = time.monotonic()


def get_status(token: str) -> dict | None:
    """استطلاع فوري لحالة التحميل - بلا أي انتظار."""
    _cleanup()
    return _fetches.get(token)


def pop_result(token: str) -> dict | None:
    """يُستدعى بعد التأكد من اكتمال الرفع (state == 'done') لاستهلاك النتيجة وتحريرها من الذاكرة."""
    return _fetches.pop(token, None)
