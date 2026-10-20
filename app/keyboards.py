"""
SAWHost - Keyboards (ألوان موزَّعة بالتساوي الحقيقي - Bot API 9.4)
===================================================================
كل لوحة مفاتيح (تُبنى عبر kb()) توزَّع ألوانها بتناوب صارم مضمون
رياضياً (Round-Robin: أحمر ← أخضر ← أزرق ← أحمر...) بترتيب إنشاء
الأزرار، بدل الاعتماد على hash عشوائي قد ينحرف عن التساوي الفعلي على
عدد أزرار صغير نسبياً (كما يحدث فعلياً بلوحات من 5-15 زراً).

آلية العمل: btn()/url_btn() تُنشئ "مواصفة" خفيفة (_BtnSpec) بدل كائن
InlineKeyboardButton مباشرة (لأن كائنات aiogram/pydantic غير قابلة
للتعديل بعد الإنشاء في بعض الإصدارات). التحويل الفعلي لأزرار تيليجرام
الحقيقية يحدث فقط داخل kb()، حيث تُوزَّع الألوان بالتناوب أولاً ثم
تُبنى الكائنات النهائية دفعة واحدة.
"""
from __future__ import annotations
import itertools
import uuid
from dataclasses import dataclass

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.config import get_cfg

from app.models import User

_STYLES = (ButtonStyle.DANGER, ButtonStyle.SUCCESS, ButtonStyle.PRIMARY)


@dataclass
class _BtnSpec:
    text: str
    data: str | None = None   # callback_data
    url: str | None = None
    web_app: str | None = None   # رابط Mini App يُفتح داخل تيليجرام
    style: ButtonStyle | None = None  # None = يُلوَّن تلقائياً بالتناوب داخل kb()

    def build(self, style: ButtonStyle) -> InlineKeyboardButton:
        final_style = self.style or style
        if self.web_app is not None:
            return InlineKeyboardButton(text=self.text, web_app=WebAppInfo(url=self.web_app), style=final_style)
        if self.url is not None:
            return InlineKeyboardButton(text=self.text, url=self.url, style=final_style)
        return InlineKeyboardButton(text=self.text, callback_data=self.data, style=final_style)


def btn(text: str, data: str, style: ButtonStyle | None = None) -> _BtnSpec:
    """زر عادي (callback). يُلوَّن بالتناوب الصارم تلقائياً ما لم يُحدَّد style صراحة."""
    return _BtnSpec(text=text, data=data, style=style)


def url_btn(text: str, url: str, style: ButtonStyle | None = None) -> _BtnSpec:
    """زر رابط خارجي. يُلوَّن بالتناوب الصارم تلقائياً ما لم يُحدَّد style صراحة."""
    return _BtnSpec(text=text, url=url, style=style)


def webapp_btn(text: str, url: str, style: ButtonStyle | None = None) -> _BtnSpec:
    """زر يفتح لوحة الويب داخل تيليجرام كتطبيق مصغّر (Mini App)."""
    return _BtnSpec(text=text, web_app=url, style=style)


def row(*btns: _BtnSpec) -> list[_BtnSpec]:
    return list(btns)


def kb(*rows: list[_BtnSpec]) -> InlineKeyboardMarkup:
    """
    يبني لوحة المفاتيح النهائية، ويوزّع الألوان بتناوب صارم (Round-Robin)
    على كل الأزرار التي لم يُحدَّد لها لون يدوياً، بترتيب ظهورها صفاً تلو
    الآخر. هذا يضمن تساوياً حقيقياً بين الثلاثة ألوان على كل لوحة، بدل
    الاعتماد على عشوائية إحصائية قد تنحرف على عدد أزرار صغير.
    """
    cycle = itertools.cycle(_STYLES)
    built_rows: list[list[InlineKeyboardButton]] = []
    for r in rows:
        built_row = []
        for spec in r:
            style = next(cycle)
            built_row.append(spec.build(style))
        built_rows.append(built_row)
    return InlineKeyboardMarkup(inline_keyboard=built_rows)


# ── القائمة الرئيسية ───────────────────────────────────────────────────────────
def _web_btn() -> _BtnSpec:
    base = get_cfg().public_url
    if base.startswith("https://"):
        return webapp_btn("🖥 افتح لوحة التحكم", base + "/panel/")
    return btn("🖥 افتح لوحة التحكم", "menu:web")


def main_kb(user: User) -> InlineKeyboardMarkup:
    """قائمة البوت مختصرة عمداً: كل الميزات داخل لوحة الويب."""
    cfg = get_cfg()
    rows = [
        row(_web_btn()),
        row(btn("ℹ️ عن المنصة", "menu:about"), url_btn("💬 الدعم الفني", f"https://t.me/{cfg.admin_username}")),
    ]
    if user.is_admin:
        rows.append(row(btn("🛡 لوحة تحكم الأدمن", "admin:panel")))
    return kb(*rows)


def moved_kb() -> InlineKeyboardMarkup:
    """يظهر عند الضغط على زر/أمر قديم نُقلت ميزته للويب."""
    return kb(row(_web_btn()), row(btn("⬅️ القائمة الرئيسية", "menu:main")))


def back_kb(data: str = "menu:main") -> InlineKeyboardMarkup:
    return kb(row(btn("⬅️ رجوع", data)))


# ── لوحة الأدمن ────────────────────────────────────────────────────────────────
def admin_kb(pending_reqs: int = 0, unread_notifs: int = 0) -> InlineKeyboardMarkup:
    req_label = f"📋 طلبات الاشتراك {'🔴 ' + str(pending_reqs) if pending_reqs else ''}"
    notif_label = f"📬 الإشعارات {'🔴 ' + str(unread_notifs) if unread_notifs else ''}"
    return kb(
        row(btn("📊 الإحصائيات",        "admin:stats"),    btn("👥 المستخدمون",       "admin:users")),
        row(btn("💳 خطط الاشتراك",       "admin:plans"),    btn("🌐 دومينات المنصة",   "admin:pdomains")),
        row(btn(req_label,               "admin:requests"), btn(notif_label,           "admin:notifs")),
        row(btn("📢 رسالة جماعية",       "admin:broadcast"),btn("📝 تعديل النصوص",     "admin:texts")),
        row(btn("🎁 نظام الإحالة",       "admin:referral"),  btn("🛠 وضع الصيانة",       "admin:maintenance")),
        row(btn("🔗 الدومينات المخصصة",  "admin:custom_domains"), btn("💾 التخزين السحابي", "admin:storage")),
        row(btn("⬅️ القائمة الرئيسية",  "menu:main")),
    )


def admin_user_kb(uid: uuid.UUID, is_banned: bool) -> InlineKeyboardMarkup:
    ban_btn = btn("✅ فك الحظر", f"admin:unban:{uid}") if is_banned else btn("🚫 حظر", f"admin:ban:{uid}")
    return kb(
        row(ban_btn),
        row(btn("💳 تغيير الخطة",    f"admin:set_plan_menu:{uid}")),
        row(btn("⬅️ رجوع للمستخدمين", "admin:users")),
    )


def plan_menu_kb(uid: uuid.UUID, plans: list) -> InlineKeyboardMarkup:
    # ملاحظة أمان: استخدام plan.id (UUID) هنا مع uid (UUID) كان يتجاوز حد
    # تيليجرام لطول callback_data (64 بايت: 88 بايت فعلياً) فيفشل الزر
    # بالكامل. نستخدم بدلاً منه كود الخطة القصير (مثل "starter"/"pro").
    rows = [row(btn(f"{p.icon} {p.name}", f"asp:{uid}:{p.code}")) for p in plans]
    rows.append(row(btn("⬅️ إلغاء", f"admin:user:{uid}")))
    return kb(*rows)


# ── إدارة خطط الاشتراك (أدمن) ──────────────────────────────────────────────────
def admin_plans_kb(plans: list) -> InlineKeyboardMarkup:
    rows = [row(btn(f"{p.icon} {p.name}" + ("" if p.is_active else " (مخفية)") + (" ⭐" if p.is_default else ""),
                     f"admin:plan_open:{p.id}")) for p in plans]
    rows.append(row(btn("➕ إضافة خطة جديدة", "admin:plan_new")))
    rows.append(row(btn("⬅️ رجوع للوحة الأدمن", "admin:panel")))
    return kb(*rows)


def admin_plan_detail_kb(pid: uuid.UUID, is_active: bool, is_default: bool) -> InlineKeyboardMarkup:
    """
    ملاحظة أمان مهمة: أسماء الحقول الكاملة (مثل max_platform_domains)
    تتجاوز حد تيليجرام الصارم لطول callback_data (64 بايت) عند دمجها مع
    بادئة المسار ومعرّف UUID، مما يفشل الرسالة كاملة عند إرسالها. لذلك
    نستخدم رموزاً مختصرة هنا فقط (nm/pr/ms/mb/cd/pd/bk/ft)، بينما تبقى
    الأسماء الكاملة الحقيقية معروفة داخلياً عبر _PLAN_FIELD_SHORT بملف
    handlers.py.
    """
    toggle = btn("🚫 إخفاء الخطة" if is_active else "✅ إظهار الخطة", f"admin:plan_toggle:{pid}")
    rows = [
        row(btn("✏️ الاسم", f"admin:plan_edit:{pid}:nm"), btn("💰 السعر الشهري", f"admin:plan_edit:{pid}:pr")),
        row(btn("🌐 عدد المواقع", f"admin:plan_edit:{pid}:ms"), btn("💾 المساحة MB", f"admin:plan_edit:{pid}:mb")),
        row(btn("🔗 دومينات مخصصة", f"admin:plan_edit:{pid}:cd"), btn("🌐 دومينات منصة", f"admin:plan_edit:{pid}:pd")),
        row(btn("💾 عدد النسخ", f"admin:plan_edit:{pid}:bk"), btn("📝 الميزات", f"admin:plan_edit:{pid}:ft")),
        row(toggle),
    ]
    if not is_default:
        rows.append(row(btn("⭐ تعيين افتراضية", f"admin:plan_set_default:{pid}"), btn("🗑 حذف الخطة", f"admin:plan_del_confirm:{pid}")))
    rows.append(row(btn("⬅️ رجوع لكل الخطط", "admin:plans")))
    return kb(*rows)


def admin_plan_del_confirm_kb(pid: uuid.UUID) -> InlineKeyboardMarkup:
    return kb(row(btn("✅ نعم، احذف الخطة", f"admin:plan_del:{pid}"), btn("❌ إلغاء", f"admin:plan_open:{pid}")))


# ── إدارة دومينات المنصة (أدمن) ────────────────────────────────────────────────
def admin_pdomains_kb(domains: list) -> InlineKeyboardMarkup:
    rows = []
    for d in domains:
        status = "🟢 متاح" if (d.is_active and not d.used_by_site_id) else ("🔵 مُستخدَم" if d.used_by_site_id else "⚪️ معطَّل")
        rows.append(row(btn(f"{status} {d.hostname}", f"admin:pdom_open:{d.id}")))
    rows.append(row(btn("➕ إضافة دومين جديد", "admin:pdom_add")))
    rows.append(row(btn("⬅️ رجوع للوحة الأدمن", "admin:panel")))
    return kb(*rows)


def admin_pdomain_detail_kb(pdid: uuid.UUID, is_active: bool, in_use: bool) -> InlineKeyboardMarkup:
    rows = []
    if not in_use:
        toggle = btn("🚫 تعطيل" if is_active else "✅ تفعيل", f"admin:pdom_toggle:{pdid}")
        rows.append(row(toggle))
        rows.append(row(btn("🗑 حذف الدومين", f"admin:pdom_del:{pdid}")))
    rows.append(row(btn("⬅️ رجوع لكل الدومينات", "admin:pdomains")))
    return kb(*rows)


def maintenance_kb(enabling: bool) -> InlineKeyboardMarkup:
    label = "✅ تأكيد التفعيل" if enabling else "✅ تأكيد الإيقاف"
    return kb(row(btn(label, f"admin:maintenance_ok:{int(enabling)}"), btn("❌ إلغاء", "admin:panel")))


def custom_domains_kb(enabled: bool) -> InlineKeyboardMarkup:
    label = "🚫 تعطيل الميزة" if enabled else "✅ تفعيل الميزة"
    return kb(
        row(btn(label, f"admin:custom_domains_toggle:{int(not enabled)}")),
        row(btn("⬅️ رجوع للوحة الأدمن", "admin:panel")),
    )


def admin_referral_kb() -> InlineKeyboardMarkup:
    return kb(row(btn("⬅️ رجوع للوحة الأدمن", "admin:panel")))


def req_detail_kb(rid: uuid.UUID) -> InlineKeyboardMarkup:
    return kb(
        row(btn("✅ قبول وتفعيل", f"admin:req_approve:{rid}"), btn("❌ رفض", f"admin:req_reject:{rid}")),
        row(btn("⬅️ الطلبات", "admin:requests")),
    )


def cancel_kb(back: str = "menu:main") -> InlineKeyboardMarkup:
    return kb(row(btn("❌ إلغاء", back)))
