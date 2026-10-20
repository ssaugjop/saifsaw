"""SAWHost - All Middlewares (Auth + RateLimit + Maintenance)"""
from __future__ import annotations
import time
from collections import defaultdict
from datetime import date
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import TelegramObject, Update

from app.config import get_cfg
from app.engines import AppError, Auth, is_maintenance, store_notif


def _extract_user(event: Update):
    """
    استخراج المستخدم من التحديث يدوياً، بدون الاعتماد على event_from_user
    (بعض توليفات نسخ aiogram/pydantic لا توفر هذه الخاصية على Update مباشرة).
    """
    if event.message:
        return event.message.from_user
    if event.edited_message:
        return event.edited_message.from_user
    if event.callback_query:
        return event.callback_query.from_user
    if event.inline_query:
        return event.inline_query.from_user
    if event.chosen_inline_result:
        return event.chosen_inline_result.from_user
    if event.shipping_query:
        return event.shipping_query.from_user
    if event.pre_checkout_query:
        return event.pre_checkout_query.from_user
    if event.poll_answer:
        return event.poll_answer.user
    if event.my_chat_member:
        return event.my_chat_member.from_user
    if event.chat_member:
        return event.chat_member.from_user
    if event.chat_join_request:
        return event.chat_join_request.from_user
    return None


class AuthMiddleware(BaseMiddleware):
    """يسجل المستخدم، يتحقق من الحظر، ويخزن إشعار الدخول في قاعدة البيانات."""

    _notified: set[int] = set()
    _day: date | None = None

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable], event: TelegramObject, data: dict) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        tg = _extract_user(event)
        if not tg:
            return await handler(event, data)

        cfg = get_cfg()

        # استخراج كود الإحالة من /start ref_<telegram_id> إن وُجد (فقط لمستخدم جديد)
        referrer_id: int | None = None
        if event.message and event.message.text:
            parts = event.message.text.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0] in ("/start", f"/start@{event.message.from_user.username or ''}") and parts[1].startswith("ref_"):
                raw = parts[1][4:]
                if raw.isdigit():
                    referrer_id = int(raw)

        user, is_new = await Auth.get_or_create(tg.id, tg.username, tg.first_name, cfg.admin_id, referrer_id)

        if user.is_banned:
            reason = user.ban_reason or "غير محدد"
            if event.callback_query:
                return AnswerCallbackQuery(callback_query_id=event.callback_query.id,
                                           text=f"🚫 حسابك محظور. السبب: {reason}", show_alert=True)
            elif event.message:
                return SendMessage(chat_id=event.message.chat.id,
                                   text=f"🚫 حسابك محظور.\nالسبب: {reason}", parse_mode=ParseMode.HTML)
            return None

        data["user"] = user

        # تخزين إشعار الدخول في قاعدة البيانات (يعمل 100% مع Piggybacking)
        today = date.today()
        if AuthMiddleware._day != today:
            AuthMiddleware._notified.clear(); AuthMiddleware._day = today
        if tg.id not in AuthMiddleware._notified and tg.id != cfg.admin_id:
            AuthMiddleware._notified.add(tg.id)
            icon = "🆕 مستخدم جديد!" if is_new else "👁 دخول مستخدم"
            await store_notif(
                f"{icon}\n👤 {user.first_name or '—'} (@{user.username or '—'})\n🆔 <code>{tg.id}</code>"
            )

        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self):
        self._hits: dict[int, list[float]] = defaultdict(list)

    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        tg = _extract_user(event)
        if not tg:
            return await handler(event, data)

        limit = get_cfg().rate_limit
        now   = time.monotonic()
        hits  = self._hits[tg.id]
        while hits and hits[0] < now - 60.0: hits.pop(0)

        if len(hits) >= limit:
            if event.callback_query:
                return AnswerCallbackQuery(callback_query_id=event.callback_query.id,
                                           text="⏳ أنت ترسل طلبات بسرعة كبيرة، انتظر قليلاً.", show_alert=True)
            return None
        hits.append(now)
        return await handler(event, data)


class LiveNotifMiddleware(BaseMiddleware):
    """
    حقن مباشر وفوري (Piggyback حقيقي) لأحدث الإشعارات غير المقروءة داخل
    *أي* رد يستلمه الأدمن من البوت — بدون أي اتصال صادر إضافي.

    لماذا هذا هو الحل الوحيد الموثوق في بيئة تمنع الاتصال الصادر: الرد على
    webhook (Piggyback) لا يمكن إرساله إلا لنفس المحادثة التي جاء منها
    التحديث، فلا يمكن تقنياً "دفع" رسالة لمحادثة الأدمن من محادثة مستخدم
    آخر إلا عبر استدعاء API منفصل (وهذا محظور هنا). الحل العملي: بما أن
    الأدمن يستخدم البوت باستمرار مثل أي شخص، كل رد يصله (أي زر أو أمر)
    يُرفَق تلقائياً بأحدث الإشعارات غير المقروءة، فتصل خلال ثوانٍ من أول
    تفاعل تالٍ للأدمن مع البوت، دون أي إجراء يدوي لفتح شاشة الإشعارات.
    """
    _MAX_INLINE = 5  # أقصى عدد إشعارات تُعرض دفعة واحدة ضمن رد واحد

    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        result = await handler(event, data)
        user = data.get("user")
        if not user or not user.is_admin:
            return result
        if not isinstance(result, (SendMessage, EditMessageText)):
            return result

        from app.engines import get_unread_notifs, mark_notifs_read
        notifs = await get_unread_notifs()
        if not notifs:
            return result

        shown, remaining = notifs[:self._MAX_INLINE], max(0, len(notifs) - self._MAX_INLINE)
        block = "\n".join(f"• {n.text}" for n in shown)
        more = f"\n\n<i>+{remaining} إشعار إضافي — افتح 📬 الإشعارات لعرض الكل</i>" if remaining else ""
        result.text = (
            f"🔔 <b>إشعارات جديدة ({len(notifs)}):</b>\n{block}{more}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n" + (result.text or "")
        )
        await mark_notifs_read()
        return result


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        user = data.get("user")
        if user and not user.is_admin and await is_maintenance():
            from app.engines import get_text
            msg = await get_text("maintenance_msg")
            if event.callback_query:
                return AnswerCallbackQuery(callback_query_id=event.callback_query.id,
                                           text=f"🛠 {msg}"[:200], show_alert=True)
            elif event.message:
                return SendMessage(chat_id=event.message.chat.id, text=f"🛠 {msg}", parse_mode=ParseMode.HTML)
            return None
        return await handler(event, data)
