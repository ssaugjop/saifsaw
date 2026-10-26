#!/data/data/com.termux/files/usr/bin/bash
# SAWHost — تحديث كامل بأمر واحد من Termux
#   مرة واحدة:   termux-setup-storage
#   كل تحديث:    bash ~/storage/downloads/update.sh            (أو مرّر مسار ملف ZIP يدوياً)
set -e
REPO_URL="https://github.com/ssaugjop/saifsaw.git"
BRANCH="main"
WORK="$HOME/saifsaw"
ZIP="${1:-$HOME/storage/downloads/sawhost_full_updated.zip}"

command -v git   >/dev/null 2>&1 || pkg install -y git
command -v unzip >/dev/null 2>&1 || pkg install -y unzip
[ -f "$ZIP" ] || { echo "❌ لم أجد الملف: $ZIP"; echo "   نزّل sawhost_full_updated.zip إلى مجلد التنزيلات أو مرّر مساره: bash update.sh /المسار/الملف.zip"; exit 1; }

[ -d "$WORK/.git" ] || git clone "$REPO_URL" "$WORK"
cd "$WORK"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

unzip -o -q "$ZIP" -d "$WORK"            # يستبدل الملفات الموجودة ويضيف الجديدة

git add -A
if git diff --cached --quiet; then echo "ℹ️ لا توجد تغييرات جديدة للرفع."; exit 0; fi
git -c user.name="${GIT_NAME:-saif}" -c user.email="${GIT_EMAIL:-saif@users.noreply.github.com}" \
    commit -q -m "تحديث SAWHost: تخزين v2 وإشعارات منفصلة واشتراك صارم وويب فيو"
git push origin "$BRANCH"
echo "✅ تم الرفع. افتح GitHub ← Actions، ألغِ الدورة الحالية ثم اضغط Run workflow."
