"""SAWHost - Bot Handlers

البوت صار بوابة خفيفة: القائمة الرئيسية + عن المنصة + لوحة الأدمن + رمز الدخول.
كل ميزات المستخدم (رفع، مواقع، ملفات، دومينات، نسخ احتياطية، GitHub، اشتراك،
دعوات) نُقلت إلى لوحة الويب /panel، وأي زر قديم منها يحوّل إليها."""
from __future__ import annotations
import secrets, uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import BotCommand, CallbackQuery, ErrorEvent, Message

from app.config import get_cfg
from app.engines import (
    Admin, AppError, Auth, Plans, PlatformDomains, Referral, Requests, Subs,
    count_unread_notifs, get_setting, get_text, get_unread_notifs,
    is_custom_domains_enabled, mark_notifs_read, reset_text, set_setting, set_text, store_notif,
    toggle_custom_domains, toggle_maintenance, is_maintenance,
    BOT_TEXTS,
)
from app.keyboards import (
    admin_kb, admin_pdomain_detail_kb, admin_pdomains_kb, admin_plan_del_confirm_kb,
    admin_plan_detail_kb, admin_plans_kb, admin_referral_kb, admin_user_kb, back_kb,
    btn, cancel_kb, custom_domains_kb, kb, main_kb, maintenance_kb, moved_kb,
    plan_menu_kb, req_detail_kb, row, url_btn, webapp_btn,
)
from app.models import DomainType, User

router = Router(name="root")


@router.errors()
async def global_error_handler(event: ErrorEvent, **kwargs):
    """
    شبكة أمان عامة: تلتقط أي استثناء غير متوقع (خصوصاً ValueError من
    UUID مزوَّرة أو callback_data مُعدَّلة يدوياً من مستخدم خبيث) على
    مستوى كل المسارات دفعة واحدة، بدل ترك أي زر بلا حماية فردية.
    لا تُسرَّب أي تفاصيل تقنية للمستخدم (رسالة عامة فقط)، وتُسجَّل
    التفاصيل الكاملة في اللوغ للمراجعة.
    """
    import logging
    logging.getLogger("sawhost").warning("Unhandled error in update %s: %r", getattr(event, "update", None), event.exception)
    update = getattr(event, "update", None)
    if update is None: return None
    if update.callback_query:
        try:
            return AnswerCallbackQuery(callback_query_id=update.callback_query.id,
                                        text="⚠️ حدث خطأ أو طلب غير صالح، حاول مجدداً.", show_alert=True)
        except Exception: return None
    if update.message:
        try:
            return SendMessage(chat_id=update.message.chat.id,
                                text="⚠️ حدث خطأ أثناء المعالجة، حاول مجدداً من القائمة الرئيسية: /start")
        except Exception: return None
    return None

# ── Response helpers ────────────────────────────────────────────────────────────
def _send(msg: Message, text: str, kb=None) -> SendMessage:
    return SendMessage(chat_id=msg.chat.id, text=text, reply_markup=kb, parse_mode=ParseMode.HTML)

def _edit(cb: CallbackQuery, text: str, kb=None) -> EditMessageText:
    return EditMessageText(chat_id=cb.message.chat.id, message_id=cb.message.message_id,
                           text=text, reply_markup=kb, parse_mode=ParseMode.HTML)

def _alert(cb: CallbackQuery, text: str) -> AnswerCallbackQuery:
    return AnswerCallbackQuery(callback_query_id=cb.id, text=text[:200], show_alert=True)

def _toast(cb: CallbackQuery, text: str) -> AnswerCallbackQuery:
    return AnswerCallbackQuery(callback_query_id=cb.id, text=text[:200], show_alert=False)


# ── FSM States ──────────────────────────────────────────────────────────────────
class S(StatesGroup):
    # الأدمن
    adm_ban       = State()
    adm_broadcast = State()
    adm_reject    = State()
    adm_text_edit = State()
    # إدارة الخطط (أدمن)
    adm_plan_new_name  = State()
    adm_plan_new_price = State()
    adm_plan_field_edit= State()
    # دومينات المنصة (أدمن)
    adm_pdom_add  = State()


# ════════════════════════════════════════════════════════════════════
#  START & MENU
# ════════════════════════════════════════════════════════════════════
BOT_COMMANDS = [
    BotCommand(command="start", description="🏠 القائمة الرئيسية"),
    BotCommand(command="login", description="🖥 رمز الدخول للوحة الويب"),
    BotCommand(command="admin", description="🛡 لوحة تحكم الأدمن"),
    BotCommand(command="help",  description="❓ المساعدة"),
]


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(msg: Message, user: User):
    welcome = await get_text("welcome")
    suffix  = "\n\n🛡 تم التعرف عليك كأدمن للمنصة." if user.is_admin else ""
    return _send(msg, welcome + suffix, main_kb(user))


@router.callback_query(F.data == "menu:main")
async def cb_main(cb: CallbackQuery, state: FSMContext, user: User):
    await state.clear()
    return _edit(cb, "🏠 <b>القائمة الرئيسية</b>", main_kb(user))


@router.callback_query(F.data == "menu:about")
async def cb_about(cb: CallbackQuery):
    return _edit(cb, await get_text("about"), back_kb("menu:main"))


# ── ميزات نُقلت إلى لوحة الويب: أي زر أو أمر قديم يحوّل إليها ──────────────────────
_MOVED_PREFIXES = ("menu:sites", "menu:upload", "menu:subscription", "menu:referral",
                   "upload:", "site:", "fm:", "dom:", "dpp:", "bk:", "gh:", "sub:")
_MOVED_TEXT = (
    "🖥 <b>انتقلت هذه الميزة إلى لوحة الويب</b>\n\n"
    "رفع المواقع، مدير الملفات، الدومينات، النسخ الاحتياطية، GitHub، "
    "الاشتراك والدعوات — كلها الآن في لوحة واحدة أسرع وأوضح.\n\n"
    "اضغط الزر أدناه لفتحها."
)


@router.callback_query(F.data.func(lambda d: bool(d) and d.startswith(_MOVED_PREFIXES)))
async def cb_moved(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    return _edit(cb, _MOVED_TEXT, moved_kb())


@router.message(Command("sites", "upload", "subscription", "referral"))
async def cmd_moved(msg: Message):
    return _send(msg, _MOVED_TEXT, moved_kb())



def _safe_uuid(raw: str) -> uuid.UUID | None:
    """تحويل آمن لـ UUID من نص قادم من callback_data - يمنع أي انهيار أو حقن بمدخلات مزوَّرة."""
    try: return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError): return None


# ════════════════════════════════════════════════════════════════════
#  لوحة الأدمن
# ════════════════════════════════════════════════════════════════════
async def _adm(user: User) -> bool:
    return user.is_admin and not user.is_banned


@router.message(Command("admin"))
async def cmd_admin(msg: Message, user: User):
    if not await _adm(user): return _send(msg, "⛔ للأدمن فقط.")
    stats = await Admin.stats()
    suf   = f"\n\n🔔 {stats.pending_requests} طلب اشتراك | {stats.unread_notifs} إشعار غير مقروء" if (stats.pending_requests or stats.unread_notifs) else ""
    return _send(msg, f"🛡 <b>لوحة تحكم الأدمن</b>{suf}", admin_kb(stats.pending_requests, stats.unread_notifs))


@router.callback_query(F.data == "admin:panel")
async def cb_admin_panel(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔ للأدمن فقط.")
    stats = await Admin.stats()
    suf   = f"\n\n🔔 {stats.pending_requests} طلب | {stats.unread_notifs} إشعار" if (stats.pending_requests or stats.unread_notifs) else ""
    return _edit(cb, f"🛡 <b>لوحة تحكم الأدمن</b>{suf}", admin_kb(stats.pending_requests, stats.unread_notifs))


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    s = await Admin.stats()
    txt = (f"📊 <b>إحصائيات المنصة</b>\n\n"
           f"👥 المستخدمون: {s.total_users} (محظورون: {s.banned_users})\n"
           f"🌐 المواقع: {s.total_sites} (تعمل: {s.running_sites})\n"
           f"🔗 الدومينات: {s.total_domains} (موثَّقة: {s.verified_domains})\n"
           f"💾 التخزين: {s.total_mb} MB\n"
           f"📋 طلبات معلّقة: {s.pending_requests}\n"
           f"📬 إشعارات غير مقروءة: {s.unread_notifs}")
    return _edit(cb, txt, back_kb("admin:panel"))


# ── إشعارات الأدمن (مخزّنة في DB - تعمل 100% مع Piggybacking) ──────
@router.callback_query(F.data == "admin:notifs")
async def cb_admin_notifs(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    notifs = await get_unread_notifs()
    await mark_notifs_read()
    if not notifs:
        return _edit(cb, "📬 لا توجد إشعارات غير مقروءة.", back_kb("admin:panel"))
    lines = ["📬 <b>الإشعارات الأخيرة:</b>\n"]
    for n in notifs[:10]:
        lines.append(f"━━━━━━━━━━━━━━━\n{n.text}\n<i>{n.created_at.strftime('%Y-%m-%d %H:%M')}</i>")
    return _edit(cb, "\n".join(lines), back_kb("admin:panel"))


# ── المستخدمون ──────────────────────────────────────────────────────
@router.callback_query(F.data == "admin:users")
async def cb_admin_users(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    users = await Admin.list_users()
    rows = [[btn(f"{'🚫' if u.is_banned else '👤'} {u.username or u.first_name or u.telegram_id}", f"admin:user:{u.id}")] for u in users]
    rows.append([btn("⬅️ رجوع", "admin:panel")])
    return _edit(cb, "👥 <b>آخر 15 مستخدمًا:</b>", kb(*rows))


@router.callback_query(F.data.startswith("admin:user:"))
async def cb_admin_user(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    tid = _safe_uuid(cb.data.split(":")[2])
    if not tid: return _alert(cb, "⚠️ معرّف غير صالح.")
    target = await Admin.get_user(tid)
    if not target: return _alert(cb, "المستخدم غير موجود.")
    try: sub = await Subs.get(target.id)
    except Exception: sub = None
    plan_label = f"{sub.plan.icon} {sub.plan.name}" if sub else "مجاني"
    exp  = sub.expires_at.strftime("%Y-%m-%d") if (sub and sub.expires_at) else "—"
    txt  = (f"👤 <b>تفاصيل المستخدم</b>\n\n"
            f"UUID: <code>{target.id}</code>\nTelegram: <code>{target.telegram_id}</code>\n"
            f"اليوزر: @{target.username or '—'}\nالاسم: {target.first_name or '—'}\n"
            f"الحالة: {'🚫 محظور' if target.is_banned else '✅ نشط'}\n"
            f"الخطة: {plan_label} (تنتهي: {exp})")
    if target.is_banned and target.ban_reason: txt += f"\nسبب الحظر: {target.ban_reason}"
    return _edit(cb, txt, admin_user_kb(tid, target.is_banned))


@router.callback_query(F.data.startswith("admin:ban:"))
async def cb_admin_ban_start(cb: CallbackQuery, state: FSMContext, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    uid = _safe_uuid(cb.data.split(":")[2])
    if not uid: return _alert(cb, "⚠️ معرّف غير صالح.")
    await state.update_data(ban_uid=str(uid)); await state.set_state(S.adm_ban)
    return _edit(cb, "🚫 أرسل سبب الحظر:", cancel_kb("admin:users"))


@router.message(S.adm_ban)
async def m_admin_ban(msg: Message, state: FSMContext, user: User):
    if not await _adm(user): return _send(msg, "⛔")
    d = await state.get_data(); await state.clear()
    uid = _safe_uuid(d.get("ban_uid", ""))
    if not uid: return _send(msg, "⚠️ حدث خطأ، أعد المحاولة.", admin_kb())
    reason = (msg.text or "بدون سبب").strip()[:512]
    await Auth.ban(uid, reason)
    t = await Admin.get_user(uid)
    if t: await store_notif(f"🚫 تم حظر @{t.username or t.telegram_id}. السبب: {reason}", kind="warning")
    return _send(msg, "✅ تم حظر المستخدم.", admin_kb())


@router.callback_query(F.data.startswith("admin:unban:"))
async def cb_admin_unban(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    uid = _safe_uuid(cb.data.split(":")[2])
    if not uid: return _alert(cb, "⚠️ معرّف غير صالح.")
    await Auth.unban(uid)
    return _edit(cb, "✅ تم فك الحظر.", back_kb("admin:users"))


@router.callback_query(F.data.startswith("admin:set_plan_menu:"))
async def cb_set_plan_menu(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    uid = _safe_uuid(cb.data.split(":")[2])
    if not uid: return _alert(cb, "⚠️ معرّف غير صالح.")
    plans = await Plans.list_all()
    return _edit(cb, "💳 اختر الخطة الجديدة:", plan_menu_kb(uid, plans))


@router.callback_query(F.data.startswith("asp:"))
async def cb_set_plan(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    parts = cb.data.split(":")
    if len(parts) < 3: return _alert(cb, "⚠️ طلب غير صالح.")
    uid = _safe_uuid(parts[1])
    code = parts[2]
    if not uid or not code: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: plan = await Plans.get_by_code(code)
    except AppError as e: return _alert(cb, e.user_msg)
    await Subs.change_plan(uid, plan.id)
    t = await Admin.get_user(uid)
    if t: await store_notif(f"💳 تم تغيير خطة @{t.username or t.telegram_id} إلى {plan.icon} {plan.name}")
    return _edit(cb, f"✅ تم تغيير الخطة إلى {plan.icon} {plan.name}.", back_kb("admin:users"))



# ── البث ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast_start(cb: CallbackQuery, state: FSMContext, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    await state.set_state(S.adm_broadcast)
    return _edit(cb, "📢 أرسل نص الرسالة الجماعية:", cancel_kb("admin:panel"))


@router.message(S.adm_broadcast)
async def m_broadcast(msg: Message, state: FSMContext, user: User):
    if not await _adm(user): return _send(msg, "⛔")
    text = (msg.text or "").strip(); await state.clear()
    if not text: return _send(msg, "⚠️ النص فارغ.")
    res  = await Admin.broadcast(msg.bot, text)
    return _send(msg, f"✅ اكتمل البث.\nنجح: {res['sent']} | فشل: {res['failed']} | الإجمالي: {res['total']}", admin_kb())


# ── الصيانة ──────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin:maintenance")
async def cb_maintenance(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    on = await is_maintenance()
    st = "🟢 مفعّل" if on else "⚪️ غير مفعّل"
    return _edit(cb, f"🛠 <b>وضع الصيانة</b>\nالحالة: {st}", maintenance_kb(not on))


@router.callback_query(F.data.startswith("admin:maintenance_ok:"))
async def cb_maintenance_ok(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    enable = bool(int(cb.data.split(":")[2]))
    await toggle_maintenance(enable)
    st = "🟢 تم تفعيل وضع الصيانة" if enable else "⚪️ تم إيقاف وضع الصيانة"
    return _edit(cb, st, admin_kb())


@router.callback_query(F.data == "admin:custom_domains")
async def cb_admin_custom_domains(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    on = await is_custom_domains_enabled()
    st = "🟢 مفعّلة" if on else "⚪️ معطَّلة"
    return _edit(cb,
        f"🔗 <b>ميزة الدومينات المخصصة</b>\n\n"
        f"الحالة: {st}\n\n"
        "<i>عند التفعيل، يستطيع أي مستخدم ربط دومين يملكه بموقعه المستضاف "
        "عبر تحقق DNS. عند التعطيل، يُخفى هذا الخيار تماماً عن المستخدمين "
        "ويُرفَض أي محاولة ربط جديدة.</i>",
        custom_domains_kb(on),
    )


@router.callback_query(F.data.startswith("admin:custom_domains_toggle:"))
async def cb_admin_custom_domains_toggle(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    enable = bool(int(cb.data.split(":")[2]))
    await toggle_custom_domains(enable)
    st = "🟢 تم تفعيل ميزة الدومينات المخصصة" if enable else "⚪️ تم تعطيل ميزة الدومينات المخصصة"
    return _edit(cb, st, admin_kb())


# ── نظام الإحالة (المصدر الوحيد للوصول المجاني بالمنصة) ────────────────
@router.callback_query(F.data == "admin:referral")
async def cb_admin_referral(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    stats = await Referral.stats()
    top_lines = "\n".join(
        f"  {i+1}. @{u.username or u.telegram_id} — {u.referral_count} إحالة"
        for i, u in enumerate(stats["top"])
    ) or "  لا يوجد بعد"
    from app.models import TRIAL_MAX_DAYS, TRIAL_REFERRAL_DAYS
    txt = (
        "🎁 <b>نظام الإحالة (المصدر الوحيد للتجربة المجانية)</b>\n\n"
        "<i>لا توجد خطة مجانية بالمنصة إطلاقاً؛ الوصول المجاني الوحيد هو "
        "عبر دعوة الأصدقاء:</i>\n"
        f"• كل إحالة ناجحة: +{TRIAL_REFERRAL_DAYS} يوم تجربة للمُحيل فقط\n"
        f"• السقف الأقصى: {TRIAL_MAX_DAYS} يوم تجربة تراكمية\n"
        "• المستخدم الجديد نفسه لا يربح شيئاً لمجرد الانضمام\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 مستخدمون بتجربة نشطة الآن: <b>{stats['active_trials']}</b>\n"
        f"📊 إجمالي الإحالات الناجحة: <b>{stats['total_referrals']}</b>\n\n"
        f"🏆 <b>أعلى المُحيلين:</b>\n{top_lines}"
    )
    return _edit(cb, txt, admin_referral_kb())


# ── طلبات الاشتراك ────────────────────────────────────────────────────
@router.callback_query(F.data == "admin:requests")
async def cb_admin_requests(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    pending = await Requests.list_pending()
    if not pending:
        return _edit(cb, "✅ لا توجد طلبات معلّقة.", back_kb("admin:panel"))
    from aiogram.types import InlineKeyboardMarkup
    rows = [[btn(f"⏳ {r.plan.icon} {r.plan.name} ({r.months} شهر) — {r.created_at.strftime('%m/%d %H:%M')}", f"admin:req_open:{r.id}")] for r in pending]
    rows.append([btn("⬅️ رجوع", "admin:panel")])
    return _edit(cb, f"📋 <b>طلبات معلّقة ({len(pending)}):</b>", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("admin:req_open:"))
async def cb_req_open(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    rid = _safe_uuid(cb.data.split(":")[2])
    if not rid: return _alert(cb, "⚠️ معرّف غير صالح.")
    req = await Requests.get(rid)
    if not req: return _alert(cb, "الطلب غير موجود.")
    t = await Admin.get_user(req.user_id)
    if not t: return _alert(cb, "المستخدم غير موجود.")
    try: sub = await Subs.get(t.id)
    except Exception: sub = None
    current_plan = f"{sub.plan.icon} {sub.plan.name}" if sub else "مجاني"
    txt = (f"📋 <b>طلب اشتراك</b>\n━━━━━━━━━━━━━━━━━━━━\n"
           f"👤 {t.first_name or '—'} (@{t.username or '—'})\n🆔 <code>{t.telegram_id}</code>\n"
           f"📅 تسجيل: {t.created_at.strftime('%Y-%m-%d')}\n"
           f"━━━━━━━━━━━━━━━━━━━━\n📦 {req.plan.icon} {req.plan.name} — {req.months} شهر\n"
           f"💰 الإجمالي: {req.total_price:,} د.ع\n"
           f"━━━━━━━━━━━━━━━━━━━━\n"
           f"💳 خطته الحالية: {current_plan}\n"
           f"⏰ تنتهي: {sub.expires_at.strftime('%Y-%m-%d') if (sub and sub.expires_at) else '—'}\n"
           f"📝 ملاحظة: {req.note or 'لا توجد'}\n"
           f"━━━━━━━━━━━━━━━━━━━━\n📩 للدفع: @S_SA_IQ")
    return _edit(cb, txt, req_detail_kb(rid))


@router.callback_query(F.data.startswith("admin:req_approve:"))
async def cb_req_approve(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    rid = _safe_uuid(cb.data.split(":")[2])
    if not rid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: req = await Requests.approve(rid, user.id)
    except AppError as e: return _alert(cb, e.user_msg)
    t = await Admin.get_user(req.user_id)
    if t:
        await store_notif(f"✅ تم تفعيل {req.plan.icon} {req.plan.name} ({req.months} شهر) للمستخدم @{t.username or t.telegram_id}")
        try: await cb.bot.send_message(t.telegram_id, f"🎉 تم تفعيل {req.plan.icon} {req.plan.name} ({req.months} شهر)! استمتع بالمنصة.")
        except Exception: pass
    pending = await Requests.list_pending()
    return _edit(cb, f"✅ تم تفعيل {req.plan.icon} {req.plan.name}.", admin_kb(len(pending)))


@router.callback_query(F.data.startswith("admin:req_reject:"))
async def cb_req_reject_start(cb: CallbackQuery, state: FSMContext, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    rid = _safe_uuid(cb.data.split(":")[2])
    if not rid: return _alert(cb, "⚠️ معرّف غير صالح.")
    await state.update_data(reject_rid=str(rid)); await state.set_state(S.adm_reject)
    return _edit(cb, "✏️ أرسل سبب الرفض:", cancel_kb("admin:requests"))


@router.message(S.adm_reject)
async def m_req_reject(msg: Message, state: FSMContext, user: User):
    if not await _adm(user): return _send(msg, "⛔")
    d = await state.get_data(); await state.clear()
    rid = _safe_uuid(d.get("reject_rid", ""))
    if not rid: return _send(msg, "⚠️ حدث خطأ، أعد المحاولة.", admin_kb())
    reason = (msg.text or "—").strip()[:512]
    try: req = await Requests.reject(rid, reason)
    except AppError as e: return _send(msg, f"⚠️ {e.user_msg}")
    t = await Admin.get_user(req.user_id)
    if t:
        try: await msg.bot.send_message(t.telegram_id, f"❌ تم رفض طلب اشتراكك.\nالسبب: {reason}\nللاستفسار: @S_SA_IQ")
        except Exception: pass
    return _send(msg, "✅ تم رفض الطلب.", admin_kb())


# ── أزرار بطاقة الطلب الفورية (rq:*) ──────────────────────────────────
async def _rq_finish(cb: CallbackQuery, rid: uuid.UUID, status_line: str):
    """يحدّث بطاقة الطلب بالنتيجة ويزيل الأزرار."""
    from app.notify import request_text
    req = await Requests.get(rid)
    if not req: return _alert(cb, "الطلب غير موجود.")
    return _edit(cb, request_text(req, req.user, status=status_line), None)


@router.callback_query(F.data.startswith("rq:ok:"))
async def cb_rq_ok(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    rid = _safe_uuid(cb.data.split(":")[2])
    if not rid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: req = await Requests.approve(rid, user.id)
    except AppError as e: return _alert(cb, e.user_msg)
    t = await Admin.get_user(req.user_id)
    if t:
        await store_notif(f"✅ تم تفعيل {req.plan.icon} {req.plan.name} ({req.months} شهر) للمستخدم @{t.username or t.telegram_id}")
        try: await cb.bot.send_message(t.telegram_id, f"🎉 تم تفعيل {req.plan.icon} {req.plan.name} ({req.months} شهر)! استمتع بالمنصة.")
        except Exception: pass
    return await _rq_finish(cb, rid, "✅ <b>تم القبول وتفعيل الاشتراك</b>")


@router.callback_query(F.data.startswith("rq:no:"))
async def cb_rq_no(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    rid = _safe_uuid(cb.data.split(":")[2])
    if not rid: return _alert(cb, "⚠️ معرّف غير صالح.")
    from app.notify import reject_kb
    return EditMessageText(chat_id=cb.message.chat.id, message_id=cb.message.message_id,
                           text=(cb.message.html_text or "") + "\n\n❌ <b>اختر سبب الرفض:</b>",
                           reply_markup=reject_kb(rid), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("rq:bk:"))
async def cb_rq_back(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    rid = _safe_uuid(cb.data.split(":")[2])
    if not rid: return _alert(cb, "⚠️ معرّف غير صالح.")
    from app.notify import request_kb, request_text
    req = await Requests.get(rid)
    if not req: return _alert(cb, "الطلب غير موجود.")
    return _edit(cb, request_text(req, req.user), request_kb(req.id, req.user.telegram_id, req.user.username))


@router.callback_query(F.data.startswith("rq:nr:"))
async def cb_rq_reason(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    parts = cb.data.split(":")
    rid = _safe_uuid(parts[2]) if len(parts) > 3 else None
    from app.notify import REJECT_REASONS
    try: reason = REJECT_REASONS[int(parts[3])]
    except (ValueError, IndexError, TypeError): return _alert(cb, "⚠️ سبب غير صالح.")
    if not rid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: req = await Requests.reject(rid, reason)
    except AppError as e: return _alert(cb, e.user_msg)
    t = await Admin.get_user(req.user_id)
    if t:
        try: await cb.bot.send_message(t.telegram_id, f"❌ تم رفض طلب اشتراكك.\nالسبب: {reason}\nللاستفسار: @{get_cfg().admin_username}")
        except Exception: pass
    return await _rq_finish(cb, rid, f"❌ <b>تم الرفض</b> — {reason}")


# ── تعديل نصوص البوت ──────────────────────────────────────────────────
@router.callback_query(F.data == "admin:texts")
async def cb_admin_texts(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    from aiogram.types import InlineKeyboardMarkup
    rows = [[btn(f"✏️ {v['desc']}", f"admin:text_edit:{k}")] for k, v in BOT_TEXTS.items()]
    rows.append([btn("⬅️ رجوع", "admin:panel")])
    return _edit(cb, "📝 <b>تعديل نصوص البوت:</b>", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("admin:text_edit:"))
async def cb_text_edit_start(cb: CallbackQuery, state: FSMContext, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    key = cb.data.split(":")[2]
    if key not in BOT_TEXTS: return _alert(cb, "مفتاح غير صالح.")
    current = await get_text(key)
    await state.update_data(edit_key=key); await state.set_state(S.adm_text_edit)
    return _edit(cb,
        f"✏️ <b>تعديل: {BOT_TEXTS[key]['desc']}</b>\n\n"
        f"النص الحالي:\n<blockquote>{current[:300]}</blockquote>\n\n"
        "أرسل النص الجديد (يدعم HTML)\nأو <code>/reset</code> للافتراضي:",
        cancel_kb("admin:texts"),
    )


@router.message(S.adm_text_edit)
async def m_text_edit(msg: Message, state: FSMContext, user: User):
    if not await _adm(user): return _send(msg, "⛔")
    d   = await state.get_data(); await state.clear()
    key = d.get("edit_key", "")
    txt = (msg.text or "").strip()
    if not txt: return _send(msg, "⚠️ النص فارغ.")
    if txt == "/reset":
        await reset_text(key)
        return _send(msg, f"✅ تم إعادة «{BOT_TEXTS.get(key,{}).get('desc',key)}» للافتراضي.", back_kb("admin:texts"))
    await set_text(key, txt)
    return _send(msg, f"✅ تم تحديث «{BOT_TEXTS.get(key,{}).get('desc',key)}».", back_kb("admin:texts"))


# ════════════════════════════════════════════════════════════════════
#  إدارة خطط الاشتراك (أدمن) - كل الحدود/الأسعار تُتحقق قبل الحفظ
# ════════════════════════════════════════════════════════════════════
_PLAN_INT_FIELDS = {
    "price_monthly", "max_sites", "max_mb", "max_custom_domains",
    "max_platform_domains", "max_backups",
}
_PLAN_FIELD_LABELS = {
    "name": "الاسم", "price_monthly": "السعر الشهري (د.ع)", "max_sites": "عدد المواقع",
    "max_mb": "المساحة (MB)", "max_custom_domains": "عدد الدومينات المخصصة",
    "max_platform_domains": "عدد دومينات المنصة", "max_backups": "عدد النسخ الاحتياطية",
    "features": "الميزات المعروضة (سطر لكل ميزة)",
}
# رموز مختصرة لتفادي تجاوز حد تيليجرام لطول callback_data (64 بايت) -
# انظر التعليق التوضيحي في admin_plan_detail_kb بملف keyboards.py
_PLAN_FIELD_SHORT = {
    "nm": "name", "pr": "price_monthly", "ms": "max_sites", "mb": "max_mb",
    "cd": "max_custom_domains", "pd": "max_platform_domains", "bk": "max_backups", "ft": "features",
}


def _plan_detail_txt(p) -> str:
    feat = p.features or "—"
    return (f"{p.icon} <b>{p.name}</b> {'⭐ (افتراضية)' if p.is_default else ''}\n"
            f"الحالة: {'🟢 ظاهرة للمستخدمين' if p.is_active else '⚪️ مخفية'}\n\n"
            f"💰 السعر الشهري: {p.price_monthly:,} د.ع\n"
            f"🌐 مواقع: {p.max_sites}  |  💾 مساحة: {p.max_mb}MB\n"
            f"🔗 دومينات مخصصة: {p.max_custom_domains}  |  🌐 دومينات منصة: {p.max_platform_domains}\n"
            f"🗄 نسخ احتياطية: {p.max_backups}\n\n"
            f"<b>الميزات:</b>\n{feat}")


@router.callback_query(F.data == "admin:plans")
async def cb_admin_plans(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    plans = await Plans.list_all()
    return _edit(cb, "💳 <b>إدارة خطط الاشتراك</b>\n\nالتغييرات هنا تُطبَّق فوراً على كامل النظام.", admin_plans_kb(plans))


@router.callback_query(F.data.startswith("admin:plan_open:"))
async def cb_admin_plan_open(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    pid = _safe_uuid(cb.data.split(":")[2])
    if not pid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: p = await Plans.get(pid)
    except AppError as e: return _alert(cb, e.user_msg)
    return _edit(cb, _plan_detail_txt(p), admin_plan_detail_kb(p.id, p.is_active, p.is_default))


@router.callback_query(F.data.startswith("admin:plan_toggle:"))
async def cb_admin_plan_toggle(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    pid = _safe_uuid(cb.data.split(":")[2])
    if not pid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: p = await Plans.toggle_active(pid)
    except AppError as e: return _alert(cb, e.user_msg)
    return _edit(cb, _plan_detail_txt(p), admin_plan_detail_kb(p.id, p.is_active, p.is_default))


@router.callback_query(F.data.startswith("admin:plan_set_default:"))
async def cb_admin_plan_set_default(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    pid = _safe_uuid(cb.data.split(":")[2])
    if not pid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try:
        await Plans.set_default(pid)
        p = await Plans.get(pid)
    except AppError as e: return _alert(cb, e.user_msg)
    return _edit(cb, f"⭐ تم تعيينها كخطة افتراضية.\n\n{_plan_detail_txt(p)}", admin_plan_detail_kb(p.id, p.is_active, p.is_default))


@router.callback_query(F.data.startswith("admin:plan_del_confirm:"))
async def cb_admin_plan_del_confirm(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    pid = _safe_uuid(cb.data.split(":")[2])
    if not pid: return _alert(cb, "⚠️ معرّف غير صالح.")
    return _edit(cb, "⚠️ <b>هل أنت متأكد من حذف هذه الخطة؟</b>\nلا يمكن التراجع عن هذا الإجراء.", admin_plan_del_confirm_kb(pid))


@router.callback_query(F.data.startswith("admin:plan_del:"))
async def cb_admin_plan_del(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    pid = _safe_uuid(cb.data.split(":")[2])
    if not pid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: await Plans.delete(pid)
    except AppError as e: return _alert(cb, e.user_msg)
    plans = await Plans.list_all()
    return _edit(cb, "🗑 تم حذف الخطة.", admin_plans_kb(plans))


@router.callback_query(F.data.startswith("admin:plan_edit:"))
async def cb_admin_plan_edit_start(cb: CallbackQuery, state: FSMContext, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    parts = cb.data.split(":")
    pid = _safe_uuid(parts[2])
    short = parts[3] if len(parts) > 3 else ""
    field = _PLAN_FIELD_SHORT.get(short, "")
    if not pid or field not in _PLAN_FIELD_LABELS: return _alert(cb, "⚠️ طلب غير صالح.")
    await state.update_data(plan_edit_id=str(pid), plan_edit_field=field)
    await state.set_state(S.adm_plan_field_edit)
    hint = " (أرقام صحيحة فقط، 0 أو أكبر)" if field in _PLAN_INT_FIELDS else ""
    return _edit(cb,
        f"✏️ <b>تعديل: {_PLAN_FIELD_LABELS[field]}</b>{hint}\n\nأرسل القيمة الجديدة:",
        cancel_kb(f"admin:plan_open:{pid}"),
    )


@router.message(S.adm_plan_field_edit)
async def m_admin_plan_edit(msg: Message, state: FSMContext, user: User):
    if not await _adm(user): return _send(msg, "⛔")
    d = await state.get_data(); await state.clear()
    pid = _safe_uuid(d.get("plan_edit_id", ""))
    field = d.get("plan_edit_field", "")
    if not pid or field not in _PLAN_FIELD_LABELS:
        return _send(msg, "⚠️ حدث خطأ، أعد المحاولة من لوحة الأدمن.", admin_kb())
    raw = (msg.text or "").strip()
    if not raw: return _send(msg, "⚠️ القيمة فارغة.")

    if field in _PLAN_INT_FIELDS:
        # تحقق صارم: رقم صحيح فقط، غير سالب، وبحد أقصى معقول لمنع قيم فاسدة/تجاوز تخزين
        if not raw.isdigit():
            return _send(msg, "⚠️ يجب إرسال رقم صحيح موجب فقط (بدون رموز أو فواصل).")
        value = int(raw)
        if value > 10_000_000_000:
            return _send(msg, "⚠️ القيمة كبيرة جداً وغير منطقية.")
    elif field == "name":
        if len(raw) > 64: return _send(msg, "⚠️ الاسم طويل جداً (الحد الأقصى 64 حرفاً).")
        value = raw
    elif field == "features":
        if len(raw) > 2000: return _send(msg, "⚠️ النص طويل جداً (الحد الأقصى 2000 حرف).")
        value = raw
    else:
        value = raw

    try:
        p = await Plans.update(pid, **{field: value})
    except AppError as e: return _send(msg, f"⚠️ {e.user_msg}")
    return _send(msg, f"✅ تم تحديث {_PLAN_FIELD_LABELS[field]}.\n\n{_plan_detail_txt(p)}", admin_plan_detail_kb(p.id, p.is_active, p.is_default))


@router.callback_query(F.data == "admin:plan_new")
async def cb_admin_plan_new_start(cb: CallbackQuery, state: FSMContext, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    await state.set_state(S.adm_plan_new_name)
    return _edit(cb, "➕ <b>إضافة خطة جديدة</b>\n\nأرسل اسم الخطة (مثال: Premium):", cancel_kb("admin:plans"))


@router.message(S.adm_plan_new_name)
async def m_admin_plan_new_name(msg: Message, state: FSMContext, user: User):
    if not await _adm(user): return _send(msg, "⛔")
    name = (msg.text or "").strip()
    if not name or len(name) > 64:
        return _send(msg, "⚠️ اسم غير صالح (1-64 حرفاً).")
    await state.update_data(new_plan_name=name)
    await state.set_state(S.adm_plan_new_price)
    return _send(msg, "💰 أرسل السعر الشهري بالدينار العراقي (رقم صحيح، 0 تعني مجانية):")


@router.message(S.adm_plan_new_price)
async def m_admin_plan_new_price(msg: Message, state: FSMContext, user: User):
    if not await _adm(user): return _send(msg, "⛔")
    raw = (msg.text or "").strip()
    if not raw.isdigit():
        return _send(msg, "⚠️ يجب إرسال رقم صحيح موجب فقط.")
    price = int(raw)
    if price > 10_000_000_000:
        return _send(msg, "⚠️ القيمة كبيرة جداً وغير منطقية.")
    d = await state.get_data(); await state.clear()
    name = d.get("new_plan_name", "خطة جديدة")
    code = f"plan_{secrets.token_hex(4)}"
    try:
        p = await Plans.create(code=code, name=name, icon="📦", price=price,
                                sites=1, mb=100, cdom=0, pdom=0, backups=1, features="")
    except AppError as e: return _send(msg, f"⚠️ {e.user_msg}")
    return _send(msg,
        f"✅ تم إنشاء الخطة <b>{p.name}</b>!\n\nيمكنك الآن ضبط كل الحدود والميزات من تفاصيل الخطة.",
        admin_plan_detail_kb(p.id, p.is_active, p.is_default),
    )


# ════════════════════════════════════════════════════════════════════
#  إدارة دومينات المنصة (أدمن) - الأدمن فقط يضيف/يحذف/يعطّل
# ════════════════════════════════════════════════════════════════════
def _pdom_detail_txt(pd) -> str:
    status = "🟢 متاح للاختيار" if (pd.is_active and not pd.used_by_site_id) else ("🔵 مُستخدَم حالياً" if pd.used_by_site_id else "⚪️ معطَّل")
    lines = [f"🌐 <b>{pd.hostname}</b>", f"الحالة: {status}"]
    if pd.label: lines.append(f"الوصف: {pd.label}")
    return "\n".join(lines)


@router.callback_query(F.data == "admin:pdomains")
async def cb_admin_pdomains(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    domains = await PlatformDomains.list_all()
    return _edit(cb, "🌐 <b>إدارة دومينات المنصة</b>\n\nهذه الدومينات يمكن لأي مستخدم اختيارها لموقعه ضمن حدود خطته.", admin_pdomains_kb(domains))


@router.callback_query(F.data.startswith("admin:pdom_open:"))
async def cb_admin_pdom_open(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    pdid = _safe_uuid(cb.data.split(":")[2])
    if not pdid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: pd = await PlatformDomains.get(pdid)
    except AppError as e: return _alert(cb, e.user_msg)
    return _edit(cb, _pdom_detail_txt(pd), admin_pdomain_detail_kb(pd.id, pd.is_active, bool(pd.used_by_site_id)))


@router.callback_query(F.data.startswith("admin:pdom_toggle:"))
async def cb_admin_pdom_toggle(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    pdid = _safe_uuid(cb.data.split(":")[2])
    if not pdid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: pd = await PlatformDomains.toggle_active(pdid)
    except AppError as e: return _alert(cb, e.user_msg)
    return _edit(cb, _pdom_detail_txt(pd), admin_pdomain_detail_kb(pd.id, pd.is_active, bool(pd.used_by_site_id)))


@router.callback_query(F.data.startswith("admin:pdom_del:"))
async def cb_admin_pdom_del(cb: CallbackQuery, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    pdid = _safe_uuid(cb.data.split(":")[2])
    if not pdid: return _alert(cb, "⚠️ معرّف غير صالح.")
    try: await PlatformDomains.remove(pdid)
    except AppError as e: return _alert(cb, e.user_msg)
    domains = await PlatformDomains.list_all()
    return _edit(cb, "🗑 تم حذف الدومين.", admin_pdomains_kb(domains))


@router.callback_query(F.data == "admin:pdom_add")
async def cb_admin_pdom_add_start(cb: CallbackQuery, state: FSMContext, user: User):
    if not await _adm(user): return _alert(cb, "⛔")
    await state.set_state(S.adm_pdom_add)
    return _edit(cb,
        "➕ <b>إضافة دومين منصة جديد</b>\n\n"
        "أرسل الدومين (مثال: <code>sites.example.com</code>)\n"
        "<i>تأكد أن DNS الخاص بهذا الدومين مُوجَّه بالفعل لسيرفر المنصة قبل إضافته هنا.</i>",
        cancel_kb("admin:pdomains"),
    )


@router.message(S.adm_pdom_add)
async def m_admin_pdom_add(msg: Message, state: FSMContext, user: User):
    if not await _adm(user): return _send(msg, "⛔")
    await state.clear()
    host = (msg.text or "").strip().lower()
    if not host or len(host) > 255 or " " in host:
        return _send(msg, "⚠️ دومين غير صالح.")
    try: pd = await PlatformDomains.add(host)
    except AppError as e: return _send(msg, f"⚠️ {e.user_msg}")
    return _send(msg, f"✅ تمت إضافة <b>{pd.hostname}</b> لقائمة دومينات المنصة.", back_kb("admin:pdomains"))


# ════════════════════════════════════════════════════════════════════
#  لوحة الويب: رمز دخول لمرة واحدة (يُرسَل كرد مباشر - Piggyback)
# ════════════════════════════════════════════════════════════════════
def _web_login_reply(user: User, back: str | None):
    from app.webpanel import issue_login_code, login_link, pretty_code
    base = get_cfg().public_url
    code = issue_login_code(user.telegram_id)
    txt = (
        "🖥 <b>لوحة الويب</b>\n\n"
        "كل شيء هنا: رفع المواقع، الملفات، الدومينات، النسخ الاحتياطية، GitHub، الاشتراك والدعوات.\n\n"
        "داخل تيليجرام يتم الدخول تلقائياً.\n"
        f"للمتصفح، رمز الدخول: <code>{pretty_code(code)}</code>\n"
        "صالح 10 دقائق ويعمل مرة واحدة فقط."
    )
    rows = []
    if base.startswith("https://"):
        rows.append(row(webapp_btn("🖥 فتح اللوحة داخل تيليجرام", base + "/panel/")))
        rows.append(row(url_btn("🌐 فتحها في المتصفح", login_link(code))))
    if back:
        rows.append(row(btn("⬅️ رجوع", back)))
    return txt, (kb(*rows) if rows else None)


@router.message(Command("login"))
async def cmd_login(msg: Message, user: User):
    txt, markup = _web_login_reply(user, None)
    return _send(msg, txt, markup)


@router.callback_query(F.data == "menu:web")
async def cb_web_panel(cb: CallbackQuery, user: User):
    txt, markup = _web_login_reply(user, "menu:main")
    return _edit(cb, txt, markup)


@router.message(Command("help"))
async def cmd_help(msg: Message):
    txt = (
        "❓ <b>مساعدة SAWHost</b>\n\n"
        "كل شيء يتم من <b>لوحة الويب</b> — اضغط «🖥 لوحة الويب» في /start.\n\n"
        "الأوامر:\n"
        "/start — القائمة الرئيسية\n"
        "/login — رمز الدخول للوحة من المتصفح\n"
        "/admin — لوحة تحكم الأدمن (للأدمن فقط)\n\n"
        f"للدعم الفني: @{get_cfg().admin_username}"
    )
    return _send(msg, txt, back_kb("menu:main"))
