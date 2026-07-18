#!/bin/bash
# ============================================================
# FlashWater BI 看板 — 每日自动部署脚本（含加密）
# 流程: 同步数据 → 导出JSON → 构建HTML → staticrypt加密 → 推送到GitHub Pages
# 密码策略: 每周一自动生成新随机密码，非周一沿用上周密码
# ============================================================
set -e

# 项目根目录 — deploy.sh 始终从项目根目录被调用
PROJECT_DIR="$(pwd)"

# 路径转换: Git Bash Unix路径 → Windows路径（供 Node/Python 使用）
to_win_path() {
    # 将 /d/path/to/file 转为 D:/path/to/file
    if command -v cygpath &>/dev/null; then
        cygpath -w "$1"
    else
        # 手动转换: /d/xxx -> D:/xxx
        echo "$1" | sed 's|^/\([a-zA-Z]\)/|\1:/|'
    fi
}
PYTHON="C:/Users/altermind/.workbuddy/binaries/python/versions/3.13.12/python.exe"
NODE="C:/Users/altermind/.workbuddy/binaries/node/versions/22.22.2/node.exe"
STATICRYPT="C:/Users/altermind/.workbuddy/binaries/node/workspace/node_modules/staticrypt/cli/index.js"
LOG_FILE="$PROJECT_DIR/deploy.log"

# staticrypt 固定盐值（保证同一密码产生相同密文）
STATICRYPT_SALT="b53b85cdfddbf54dc1146c146f768a80"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

cd "$PROJECT_DIR"

log "========== FlashWater BI 每日部署开始 =========="

# ============================================================
# Step 0: 密码管理（每周一自动轮换）
# ============================================================
PASSWORD_FILE="$PROJECT_DIR/data/.password"
PASSWORD=""

# 检查是否周一（weekday: 1=周一）
WEEKDAY=$(date +%u 2>/dev/null || python3 -c "from datetime import date; print(date.today().weekday()+1)" 2>/dev/null)

if [ "$WEEKDAY" = "1" ] || [ ! -f "$PASSWORD_FILE" ]; then
    # 周一 或 密码文件不存在 → 生成新密码
    log "[密码] 今天是周${WEEKDAY:-?}，生成新随机密码..."
    # 生成16位随机密码: 大写字母+数字+小写字母混合
    PASSWORD=$(cat /dev/urandom 2>/dev/null | tr -dc 'A-Za-z0-9' | head -c16 || \
               python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(16)))")
    
    if [ -z "$PASSWORD" ] || [ ${#PASSWORD} -lt 14 ]; then
        # 兜底: 用 Python 生成
        PASSWORD=$($PYTHON -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(16)))")
    fi
    
    echo "$PASSWORD" > "$PASSWORD_FILE"
    log "  ✓ 新密码已生成并保存"
else
    # 非周一 → 沿用已有密码
    PASSWORD=$(cat "$PASSWORD_FILE")
    log "[密码] 非周一，沿用上周密码"
fi

# ============================================================
# Step 1: 增量同步万里牛数据
# ============================================================
log "[1/5] 同步万里牛最新数据..."
$PYTHON scripts/sync_incremental.py >> "$LOG_FILE" 2>&1
log "  ✓ 数据同步完成"

# ============================================================
# Step 2: 导出看板数据JSON
# ============================================================
log "[2/5] 导出看板数据..."
$PYTHON scripts/export_data.py >> "$LOG_FILE" 2>&1
log "  ✓ 数据导出完成"

# ============================================================
# Step 3: 构建看板HTML（数据注入模板）
# ============================================================
log "[3/5] 构建看板HTML..."
$PYTHON scripts/build_dashboard.py >> "$LOG_FILE" 2>&1
log "  ✓ 看板构建完成"

# ============================================================
# Step 4: staticrypt AES-256加密
# ============================================================
log "[4/5] 加密看板..."
BUILD_HTML="$PROJECT_DIR/outputs/dashboard_v3.html"
ENCRYPT_DIR="$PROJECT_DIR/outputs/encrypted"

$NODE "$STATICRYPT" "$(to_win_path "$BUILD_HTML")" \
    -p "$PASSWORD" \
    -s "$STATICRYPT_SALT" \
    -d "$(to_win_path "$ENCRYPT_DIR")" \
    --template-title "FlashWater BI 看板" \
    --template-instructions "请输入访问密码以查看运营数据" \
    --template-button "解锁看板" \
    --template-error "密码错误，请重试" \
    --template-placeholder "输入密码..." \
    --short \
    >> "$LOG_FILE" 2>&1

log "  ✓ 加密完成"

# ============================================================
# Step 5: 复制到docs目录并推送到GitHub Pages
# ============================================================
log "[5/5] 部署到GitHub Pages..."

cp "$ENCRYPT_DIR/dashboard_v3.html" "$PROJECT_DIR/docs/index.html"

# 提交并推送
cd "$PROJECT_DIR"
git add docs/index.html data/dashboard_data.json data/dimension_data.json
git commit -m "自动更新: $(date '+%Y-%m-%d %H:%M')" || log "  (无变更，跳过commit)"

# 从 ~/.git-credentials 提取 token 绕过 credential-store helper 失效问题
CRED_FILE="$HOME/.git-credentials"
PUSH_URL=""
if [ -f "$CRED_FILE" ]; then
    TOKEN_LINE=$(grep 'github.com' "$CRED_FILE" | head -1 2>/dev/null)
    if [ -n "$TOKEN_LINE" ]; then
        # 提取 https://user:token@github.com/... 格式的凭证行，直接用作 push URL
        PUSH_URL="@github.com/flashwater-BI/flashwater-bi.git"
        USER_TOKEN=$(echo "$TOKEN_LINE" | sed 's|https://\(.*\)@github.com.*|\1|')
        PUSH_URL="https://${USER_TOKEN}@github.com/flashwater-BI/flashwater-bi.git"
    fi
fi

if [ -n "$PUSH_URL" ]; then
    git push "$PUSH_URL" master 2>&1 | tee -a "$LOG_FILE"
else
    log "  ⚠ 未找到 GitHub 凭证，尝试默认推送..."
    git push origin master 2>&1 | tee -a "$LOG_FILE"
fi
log "  ✓ 推送完成"

# ============================================================
# Step 6: 周一推送访问地址和本周密码到企微群
# ============================================================
WEBHOOK_FILE="$PROJECT_DIR/data/.webhook"
if [ "$WEEKDAY" = "1" ] && [ -f "$WEBHOOK_FILE" ]; then
    log "[6/6] 推送企微群消息..."
    WEBHOOK_URL=$(cat "$WEBHOOK_FILE")
    curl -s "$WEBHOOK_URL" \
        -H 'Content-Type: application/json' \
        -d "{\"msgtype\":\"markdown\",\"markdown\":{\"content\":\"## 📊 FlashWater BI 看板 本周已更新\n> 数据已同步至 $(date '+%m月%d日')，看板已部署\n> 访问地址：[flashwater-BI.github.io](https://flashwater-BI.github.io/flashwater-bi/)\n> 本周密码：<font color=\\\"warning\\\">$PASSWORD</font>\"}}" \
        >> "$LOG_FILE" 2>&1
    log "  ✓ 企微群消息已推送"
elif [ "$WEEKDAY" = "1" ]; then
    log "[6/6] ⚠ 今天是周一但未配置企微webhook (data/.webhook)，跳过推送"
fi
# 非周一不做任何推送

# ============================================================
# 完成
# ============================================================
log "========== 部署完成 =========="
log "访问地址: https://flashwater-BI.github.io/flashwater-bi/"
log "本周密码: $PASSWORD"
log "密码文件: $PASSWORD_FILE"
log ""
