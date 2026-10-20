"""
SAWHost - DPI Bypass لاتصالات تيليجرام (aiohttp/aiogram)
=============================================================
الاكتشاف الحاسم: المشكلة لم تكن "منع اتصال صادر" بشكل عام، بل حجب على
مستوى DPI (فحص حزم عميق) خاص بـ SNI (اسم النطاق المُرسَل أثناء مصافحة
TLS) لـ "api.telegram.org" تحديداً داخل حاويات HuggingFace. هذا يفسّر
بالضبط لماذا كان الاتصال "ينجح أحياناً ويتعثر أحياناً" — لأن الحجب على
مستوى DPI غير حتمي 100% دائماً.

الحل: بدل الاتصال باسم النطاق (الذي يُرسِل SNI محظوراً)، نتصل مباشرة
بعنوان IP حقيقي تابع لخوادم تيليجرام (لا يوجد SNI محظور عند الاتصال
بعنوان IP مباشرة)، مع الحفاظ على ترويسة Host الصحيحة حتى تدرك خوادم
تيليجرام الطلب ويُوجَّه بشكل صحيح، وتعطيل التحقق من شهادة TLS (لأن
الشهادة تخص اسم النطاق وليس عنوان الـ IP الخام).

آلية العمل:
  1. اختبار عدة عناوين IP معروفة لتيليجرام عبر اتصال TCP خام سريع
     (بدون DNS) واختيار أول عنوان يستجيب فعلياً.
  2. تصحيح (monkey-patch) الدالة الداخلية aiohttp.ClientSession._request
     — وهي النقطة المشتركة التي تمر منها كل طلبات aiohttp (ومنها كل
     طلبات aiogram) — لإعادة كتابة أي رابط يحتوي "api.telegram.org"
     واستبداله بعنوان الـ IP المُختار مباشرة قبل إرسال الطلب.
  3. لا يؤثر هذا على أي اتصال آخر (لا يمسّ الاتصال بـ GitHub، ولا حتى
     الـ Reverse Proxy الداخلي على 127.0.0.1) لأن الاستبدال مشروط
     صراحة بوجود "api.telegram.org" بالرابط.
"""
from __future__ import annotations
import logging
import socket
import time

log = logging.getLogger("sawhost.dpi_bypass")

TG_HOST = "api.telegram.org"

# عناوين IP حقيقية معروفة لخوادم تيليجرام (مصدر: توثيق Telegram Bot API الرسمي)
TELEGRAM_IPS: list[str] = [
    "149.154.167.220",
    "149.154.167.198",
    "149.154.166.110",
    "149.154.167.91",
    "149.154.165.120",
    "91.108.56.130",
    "91.108.56.190",
    "149.154.167.99",
    "91.108.56.180",
]

_patched = False
_chosen_ip: str | None = None


def pick_fastest_ip(candidates: list[str] | None = None, timeout: float = 3.0) -> str:
    """
    اختبار TCP خام (بدون DNS) على المنفذ 443 لكل عنوان بالترتيب، ويُعاد
    أول عنوان يستجيب فعلياً. هذا اختبار اتصال حقيقي وسريع (لا يعتمد على
    استبانة أسماء النطاقات إطلاقاً، فيتجاوز أي حجب DNS محتمل أيضاً).
    """
    ips = candidates or TELEGRAM_IPS
    for ip in ips:
        try:
            t0 = time.monotonic()
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, 443))
            s.close()
            elapsed_ms = (time.monotonic() - t0) * 1000
            log.info("✅ عنوان تيليجرام يستجيب: %s (%.0fms)", ip, elapsed_ms)
            return ip
        except Exception as e:
            log.debug("❌ %s فشل: %s", ip, e)
    log.warning("⚠️ لم يستجب أي عنوان بالاختبار، استخدام أول عنوان بالقائمة كخيار أخير.")
    return ips[0]


def apply_dpi_bypass(ip: str | None = None) -> str:
    """
    يُطبَّق مرة واحدة عند إقلاع السيرفر، قبل أي استخدام لأي Bot instance.
    يُصحِّح aiohttp.ClientSession._request عالمياً بحيث يُعاد كتابة أي
    رابط لتيليجرام تلقائياً ليتصل بعنوان IP مباشر بدل اسم النطاق المحجوب.
    """
    global _patched, _chosen_ip
    chosen = ip or pick_fastest_ip()
    _chosen_ip = chosen

    if _patched:
        log.info("🔌 DPI Bypass مُطبَّق مسبقاً — تحديث العنوان إلى %s فقط.", chosen)
        return chosen

    try:
        import aiohttp
    except ImportError:
        log.error("❌ aiohttp غير متوفرة — تعذّر تطبيق DPI Bypass.")
        return chosen

    original_request = aiohttp.ClientSession._request

    async def patched_request(self, method, url, *args, **kwargs):
        url_str = str(url)
        if TG_HOST in url_str:
            ip_now = _chosen_ip or chosen
            url_str = url_str.replace(TG_HOST, ip_now)
            kwargs["ssl"] = False
            headers = kwargs.get("headers")
            if headers is None:
                headers = {}
            elif hasattr(headers, "copy"):
                headers = dict(headers)
            else:
                headers = dict(headers)
            headers["Host"] = TG_HOST
            kwargs["headers"] = headers
        return await original_request(self, method, url_str, *args, **kwargs)

    aiohttp.ClientSession._request = patched_request
    _patched = True
    log.info("✅ DPI Bypass نشط — كل اتصالات تيليجرام ستمر عبر %s (Host header محفوظ).", chosen)
    return chosen


def current_ip() -> str | None:
    return _chosen_ip
