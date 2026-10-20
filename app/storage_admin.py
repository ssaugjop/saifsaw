"""
SAWHost - إعداد التخزين السحابي من لوحة الأدمن (قناة تلجرام)
============================================================
/admin ← 💾 التخزين السحابي:
  ➕ ربط قناة (أضِف البوت مشرفاً ثم حوّل رسالة من القناة أو أرسل معرّفها/رابط رسالة)
  💾 حفظ الآن   🔁 تغيير القناة   🔌 فصل القناة
وعند إضافة البوت مشرفاً في أي قناة، يصل الأدمن زر «استخدمها للتخزين» تلقائياً.
"""
from __future__ import annotations

import asyncio
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from app import tgstore
from app.config import get_cfg
from app.handlers import _adm, _alert, _edit, _send, _toast
from app.keyboards import back_kb, btn, cancel_kb, kb, row
from app.models import User

storage_router = Router(name="storage")
_tasks: set[asyncio.Task] = set()      # مراجع قوية كي لا تُجمَّع المهام الخلفية


class SS(StatesGroup):
    wait_channel = State()


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _storage_kb():
    if tgstore.S.channel_id:
        return kb(
            row(btn("💾 حفظ الآن", "admin:storage_save")),
            row(btn("🔁 تغيير القناة", "admin:storage_link"), btn("🔌 فصل القناة", "admin:storage_unlink")),
            row(btn("⬅️ رجوع", "admin:panel")),
        )
    return kb(row(btn("➕ ربط قناة التخزين", "admin:storage_link")), row(btn("⬅️ رجوع", "admin:panel")))


def _extract_channel_id(msg: Message) -> int | None:
    origin = getattr(msg, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin else None
    if chat is not None and getattr(chat, "type", "") in ("channel", "supergroup"):
        return chat.id
    legacy = getattr(msg, "forward_from_chat", None)
    if legacy is not None:
        return legacy.id
    txt = (msg.text or "").strip()
    m = re.search(r"t\.me/c/(\d+)", txt)
    if m:
        return int("-100" + m.group(1))
    if re.fullmatch(r"-?\d{6,}", txt):
        n = int(txt)
        return n if n < 0 else int("-100" + txt)
    return None


async def _bg_save(bot, chat_id: int, reason: str = "manual") -> None:
    ok, info = await tgstore.save(bot, db=True, sites=None, reason=reason)
    try:
        await bot.send_message(chat_id, ("✅ " if ok else "❌ ") + info)
    except Exception:
        pass


# ── الشاشة الرئيسية ────────────────────────────────────────────────────────────
@storage_router.callback_query(F.data == "admin:storage")
async def cb_storage(cb: CallbackQuery, state: FSMContext, user: User):
    if not await _adm(user):
        return _alert(cb, "⛔ للأدمن فقط.")
    await state.clear()
    return _edit(cb, tgstore.status_text(), _storage_kb())


# ── ربط قناة ───────────────────────────────────────────────────────────────────
@storage_router.callback_query(F.data == "admin:storage_link")
async def cb_storage_link(cb: CallbackQuery, state: FSMContext, user: User):
    if not await _adm(user):
        return _alert(cb, "⛔ للأدمن فقط.")
    await state.set_state(SS.wait_channel)
    return _edit(cb, (
        "➕ <b>ربط قناة التخزين</b>\n\n"
        "1️⃣ أنشئ قناة <b>خاصة</b> جديدة.\n"
        "2️⃣ أضفني مشرفاً فيها بصلاحيات: <b>نشر الرسائل</b> + <b>تعديل رسائل الآخرين</b> (للتثبيت) + <b>حذف الرسائل</b>.\n"
        "3️⃣ أرسل لي هنا <b>واحداً</b> من:\n"
        "   • رسالة محوّلة (Forward) من القناة\n"
        "   • رابط رسالة من القناة (t.me/c/…)\n"
        "   • معرّف القناة (-100…)\n\n"
        "💡 أو اكتفِ بإضافتي مشرفاً وسيصلك زر تأكيد تلقائي."
    ), cancel_kb("admin:storage"))


@storage_router.message(SS.wait_channel)
async def m_storage_channel(msg: Message, state: FSMContext, user: User):
    if not await _adm(user):
        return None
    cid = _extract_channel_id(msg)
    if cid is None:
        return _send(msg, "⚠️ لم أتعرّف على القناة. حوّل لي رسالة منها، أو أرسل المعرّف (-100…) أو رابط رسالة.",
                     cancel_kb("admin:storage"))
    await state.clear()
    ok, info = await tgstore.link_channel(msg.bot, cid)
    if ok:
        _spawn(_bg_save(msg.bot, msg.chat.id, "link"))
        info += "\n⏳ جارٍ حفظ أول لقطة…"
    return _send(msg, ("✅ " if ok else "❌ ") + info, _storage_kb() if ok else back_kb("admin:storage"))


# ── اكتشاف تلقائي عند إضافة البوت مشرفاً ─────────────────────────────────────
@storage_router.my_chat_member(F.chat.type == "channel")
async def on_added_to_channel(ev: ChatMemberUpdated, user: User):
    if user is None or not user.is_admin:
        return None
    if ev.new_chat_member.status != "administrator" or tgstore.S.channel_id == ev.chat.id:
        return None
    try:
        await ev.bot.send_message(
            get_cfg().admin_id,
            f"🔔 أُضفتُ مشرفاً في القناة «{ev.chat.title}».\nهل تريد استخدامها كقناة تخزين لبيانات المنصة؟",
            reply_markup=kb(row(btn("✅ استخدمها للتخزين", f"admin:storage_use:{ev.chat.id}"),
                                btn("❌ تجاهل", "admin:storage"))),
        )
    except Exception:
        pass
    return None


@storage_router.callback_query(F.data.startswith("admin:storage_use:"))
async def cb_storage_use(cb: CallbackQuery, user: User):
    if not await _adm(user):
        return _alert(cb, "⛔ للأدمن فقط.")
    try:
        cid = int(cb.data.rsplit(":", 1)[1])
    except ValueError:
        return _alert(cb, "⚠️ معرّف غير صالح.")
    ok, info = await tgstore.link_channel(cb.bot, cid)
    if ok:
        _spawn(_bg_save(cb.bot, cb.message.chat.id, "link"))
        info += "\n⏳ جارٍ حفظ أول لقطة…"
    return _edit(cb, ("✅ " if ok else "❌ ") + info, _storage_kb() if ok else back_kb("admin:storage"))


# ── حفظ فوري / فصل ────────────────────────────────────────────────────────────
@storage_router.callback_query(F.data == "admin:storage_save")
async def cb_storage_save(cb: CallbackQuery, user: User):
    if not await _adm(user):
        return _alert(cb, "⛔ للأدمن فقط.")
    if not tgstore.S.channel_id:
        return _alert(cb, "اربط قناة أولاً.")
    _spawn(_bg_save(cb.bot, cb.message.chat.id, "manual"))
    return _toast(cb, "⏳ جارٍ الحفظ… سأرسل لك النتيجة.")


@storage_router.callback_query(F.data == "admin:storage_unlink")
async def cb_storage_unlink(cb: CallbackQuery, user: User):
    if not await _adm(user):
        return _alert(cb, "⛔ للأدمن فقط.")
    return _edit(cb, "⚠️ سيتوقف الحفظ التلقائي ولن تُستعاد البيانات بعد إعادة التشغيل.\n"
                     "محتوى القناة لا يُحذف. تأكيد الفصل؟",
                 kb(row(btn("✅ نعم، افصل", "admin:storage_unlink_ok"), btn("❌ لا", "admin:storage"))))


@storage_router.callback_query(F.data == "admin:storage_unlink_ok")
async def cb_storage_unlink_ok(cb: CallbackQuery, user: User):
    if not await _adm(user):
        return _alert(cb, "⛔ للأدمن فقط.")
    await tgstore.unlink(cb.bot)
    return _edit(cb, "🔌 تم فصل القناة.", _storage_kb())
