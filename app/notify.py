"""
SAWHost - إشعارات الأدمن الفورية (تيليجرام)
===========================================
طلب اشتراك جديد يصل للأدمن كرسالة منسّقة مع أزرار:
  ✅ قبول | ❌ رفض | 👤 الملف الشخصي | 🖥 فتح اللوحة
عند فشل الإرسال يُحفَظ كإشعار عادي (store_notif) كي لا يضيع الطلب.
"""
from __future__ import annotations

import asyncio
import html
import logging
import uuid

from app.config import get_cfg
from app.keyboards import btn, kb, row, url_btn, webapp_btn

log = logging.getLogger("sawhost.notify")

REJECT_REASONS = [
    "لم يصلنا الدفع",
    "المبلغ غير مطابق",
    "بيانات الطلب غير واضحة",
    "تواصل مع الدعم لإكمال الطلب",
]


def _e(x) -> str:
    return html.escape(str(x if x not in (None, "") else "—"))


def profile_url(telegram_id: int, username: str | None) -> str:
    return f"https://t.me/{username}" if username else f"tg://user?id={telegram_id}"


def request_text(req, user, *, via: str = "لوحة الويب", status: str | None = None) -> str:
    plan = req.plan
    name = _e(user.first_name)
    uname = f"@{_e(user.username)}" if user.username else "بلا معرّف"
    created = req.created_at.strftime("%Y-%m-%d %H:%M") if req.created_at else "—"
    t = (
        "💳 <b>طلب اشتراك جديد</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>{name}</b>  ·  {uname}\n"
        f"🆔 <code>{user.telegram_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>{_e(plan.icon)} {_e(plan.name)}</b>  ·  {req.months} شهر\n"
        f"💰 الإجمالي: <b>{req.total_price:,} د.ع</b>\n"
        f"📝 {_e(req.note) if req.note else 'بلا ملاحظة'}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {created}  ·  عبر {_e(via)}"
    )
    if status:
        t += f"\n\n{status}"
    return t


def request_kb(rid: uuid.UUID, telegram_id: int, username: str | None):
    base = get_cfg().public_url
    rows = [
        row(btn("✅ قبول", f"rq:ok:{rid}"), btn("❌ رفض", f"rq:no:{rid}")),
        row(url_btn("👤 الملف الشخصي", profile_url(telegram_id, username))),
    ]
    if base.startswith("https://"):
        rows.append(row(webapp_btn("🖥 فتح الطلبات في اللوحة", base + "/panel/#/admin/requests")))
    return kb(*rows)


def reject_kb(rid: uuid.UUID):
    rows = [row(btn(f"• {r}", f"rq:nr:{rid}:{i}")) for i, r in enumerate(REJECT_REASONS)]
    rows.append(row(btn("✏️ سبب مخصص", f"admin:req_reject:{rid}"), btn("⬅️ رجوع", f"rq:bk:{rid}")))
    return kb(*rows)


async def push_new_request(bot, req_id: uuid.UUID) -> bool:
    """يرسل بطاقة الطلب للأدمن. يرجع False عند الفشل (ويُخزَّن إشعار بديل)."""
    from app.engines import Requests, store_notif
    req = await Requests.get(req_id)
    if not req:
        return False
    user = req.user
    try:
        await bot.send_message(
            get_cfg().admin_id, request_text(req, user),
            reply_markup=request_kb(req.id, user.telegram_id, user.username),
            disable_web_page_preview=True,
        )
        return True
    except Exception as ex:
        log.warning("admin push failed: %s", ex)
        await store_notif(request_text(req, user), kind="sub_request")
        return False


async def run_notif_pusher(bot, interval: float = 4.0) -> None:
    """يرسل كل إشعار للأدمن كرسالة مستقلة (مستخدم جديد، طلب، تنبيه...) بدل دمجه في رسالة الترحيب.
    يحترم حدود تيليجرام (نحو رسالتين/ثانية لمحادثة واحدة) ويعيد المحاولة عند الفشل دون تكرار الإرسال."""
    from aiogram.exceptions import TelegramRetryAfter
    from app.engines import get_unread_notifs, mark_notifs_read_ids
    last_warn = 0.0
    while True:
        await asyncio.sleep(interval)
        try:
            items = list(reversed(await get_unread_notifs()))       # الأقدم أولاً
            if not items:
                continue
            sent: list = []
            for n in items[:10]:
                try:
                    await bot.send_message(get_cfg().admin_id, f"🔔 <b>إشعار جديد</b>\n\n{n.text}", disable_web_page_preview=True)
                    sent.append(n.id)
                    await asyncio.sleep(0.5)
                except TelegramRetryAfter as e:
                    await asyncio.sleep(min(int(e.retry_after) + 1, 60))
                    break
                except Exception as ex:                              # الأدمن لم يبدأ البوت / حُظر: يبقى الإشعار غير مقروء
                    now = asyncio.get_event_loop().time()
                    if now - last_warn > 300:
                        log.warning("notif push failed: %s", ex); last_warn = now
                    break
            await mark_notifs_read_ids(sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("notif pusher")
