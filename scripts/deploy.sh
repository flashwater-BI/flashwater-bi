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
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# 企微告警推送（失败时调用，无论周几）
push_wecom_alert() {
    local ALERT_MSG="$1"
    local WEBHOOK_FILE="$PROJECT_DIR/data/.webhook"
    if [ ! -f "$WEBHOOK_FILE" ]; then
        log "  ⚠ 未配置企微webhook，跳过告警"
        return 0
    fi
    local WEBHOOK_URL=$(cat "$WEBHOOK_FILE")
    local TMP_JSON="$PROJECT_DIR/tmp/wecom_alert.json"
    local TMP_JSON_WIN=$(to_win_path "$TMP_JSON")
    mkdir -p "$PROJECT_DIR/tmp"
    $PYTHON -c "
import json
data = {'msgtype': 'markdown', 'markdown': {'content': '$ALERT_MSG'}}
with open(r'$TMP_JSON_WIN', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False)
" >> "$LOG_FILE" 2>&1 || true
    curl -s "$WEBHOOK_URL" -H 'Content-Type: application/json; charset=utf-8' -d "@$TMP_JSON_WIN" >> "$LOG_FILE" 2>&1 || true
    rm -f "$TMP_JSON"
    log "  ✓ 失败告警已推送企微"
}

# 错误处理：任何步骤失败时推送企微告警后退出
STEP_NAME="初始化"
on_error() {
    log "✗ 部署失败: $STEP_NAME"
    push_wecom_alert "## ⚠️ FlashWater BI 看板部署失败\n> 失败步骤：$STEP_NAME\n> 时间：$(date '+%Y-%m-%d %H:%M')\n> 请检查 deploy.log，修复后手动重跑 scripts/deploy.sh"
    exit 1
}
trap on_error ERR

cd "$PROJECT_DIR"

log "========== FlashWater BI 每日部署开始 =========="

# ============================================================
# Step 0: 密码管理（每周一自动轮换）
# ============================================================
PASSWORD_FILE="$PROJECT_DIR/data/.password"
PASSWORD=""

# 检查是否周一且密码尚未轮换（防止周一多次部署重复生成）
WEEKDAY=$(date +%u 2>/dev/null || python3 -c "from datetime import date; print(date.today().weekday()+1)" 2>/dev/null)
TODAY=$(date +%Y-%m-%d)
PASSWORD_DATE=""
if [ -f "$PASSWORD_FILE" ]; then
    # 读取密码文件的修改日期（粗略判断是否今日已轮换）
    PASSWORD_DATE=$(date -r "$PASSWORD_FILE" +%Y-%m-%d 2>/dev/null || echo "")
fi

if ([ "$WEEKDAY" = "1" ] && [ "$PASSWORD_DATE" != "$TODAY" ]) || [ ! -f "$PASSWORD_FILE" ]; then
    # 周一且今日尚未轮换 或 密码文件不存在 → 生成新密码
    log "[密码] 今天是周${WEEKDAY:-?}，生成新随机密码..."
    # 生成16位随机密码: 排除易混淆字符 0/O/I/l/1
    PASSWORD=$(cat /dev/urandom 2>/dev/null | tr -dc 'A-HJ-NP-Za-km-z2-9' | head -c16 || \
               python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(16)))")
    
    if [ -z "$PASSWORD" ] || [ ${#PASSWORD} -lt 14 ]; then
        # 兜底: 用 Python 生成
        PASSWORD=$($PYTHON -c "import secrets,string; safe=''.join(c for c in string.ascii_letters+string.digits if c not in '0OoIl1'); print(''.join(secrets.choice(safe) for _ in range(16)))")
    fi
    
    echo "$PASSWORD" > "$PASSWORD_FILE"
    log "  ✓ 新密码已生成并保存"
else
    # 非周一 → 沿用已有密码
    PASSWORD=$(cat "$PASSWORD_FILE")
    log "[密码] 非周一，沿用上周密码"
fi

# ============================================================
# Step 1: 增量同步万里牛数据（订单API）
# ============================================================
STEP_NAME="同步万里牛订单数据"
log "[1/7] 同步万里牛订单数据..."
$PYTHON scripts/sync_incremental.py >> "$LOG_FILE" 2>&1
log "  ✓ 订单同步完成"

# ============================================================
# Step 1.5: 同步拼多多出库单（出库单API，拼多多不走订单API）
# ============================================================
STEP_NAME="同步拼多多出库单"
log "[2/7] 同步拼多多出库单..."
$PYTHON scripts/sync_pdd_outbound.py >> "$LOG_FILE" 2>&1
log "  ✓ 拼多多同步完成"

# ============================================================
# Step 2: 导出看板数据JSON
# ============================================================
STEP_NAME="导出看板数据"
log "[3/7] 导出看板数据..."
$PYTHON scripts/export_data.py >> "$LOG_FILE" 2>&1
log "  ✓ 数据导出完成"

# ============================================================
# Step 3: 构建看板HTML（数据注入模板）
# ============================================================
STEP_NAME="构建看板HTML"
log "[4/7] 构建看板HTML..."
$PYTHON scripts/build_dashboard.py >> "$LOG_FILE" 2>&1
log "  ✓ 看板构建完成"

# ============================================================
# Step 4: staticrypt AES-256加密
# ============================================================
STEP_NAME="加密看板"
log "[5/7] 加密看板..."
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
STEP_NAME="部署到GitHub Pages"
log "[6/7] 部署到GitHub Pages..."

cp "$ENCRYPT_DIR/dashboard_v3.html" "$PROJECT_DIR/docs/index.html"

# 提交并推送
cd "$PROJECT_DIR"
git add docs/index.html data/dashboard_data.json data/dimension_data.json
git commit -m "自动更新: $(date '+%Y-%m-%d %H:%M')" || log "  (无变更，跳过commit)"

# git push（github.com:443 国内可能被墙，失败时降级到 API push）
PUSH_SUCCESS=0

# 方案1: git push（绕过 credential helper）
CRED_FILE="$HOME/.git-credentials"
PUSH_URL=""
if [ -f "$CRED_FILE" ]; then
    TOKEN_LINE=$(grep 'github.com' "$CRED_FILE" | head -1 2>/dev/null)
    if [ -n "$TOKEN_LINE" ]; then
        USER_TOKEN=$(echo "$TOKEN_LINE" | sed 's|https://\(.*\)@github.com.*|\1|')
        PUSH_URL="https://${USER_TOKEN}@github.com/flashwater-BI/flashwater-bi.git"
    fi
fi

if [ -n "$PUSH_URL" ]; then
    GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c credential.store= push "$PUSH_URL" master 2>&1 | while IFS= read -r line; do echo "$line"; echo "$line" >> "$LOG_FILE"; done
    PUSH_EXIT=${PIPESTATUS[0]}
else
    log "  ⚠ 未找到 GitHub 凭证，尝试默认推送..."
    GIT_TERMINAL_PROMPT=0 git push origin master 2>&1 | while IFS= read -r line; do echo "$line"; echo "$line" >> "$LOG_FILE"; done
    PUSH_EXIT=${PIPESTATUS[0]}
fi

if [ "$PUSH_EXIT" -ne 0 ]; then
    log "  ⚠ git push 失败 (exit $PUSH_EXIT)，降级到 GitHub API push..."
    # 方案2: 通过 GitHub Contents API 推送（绕过 github.com:443 被墙）
    # 注意: || 捕获退出码，避免 set -e 直接终止脚本导致跳过企微告警
    STEP_NAME="GitHub API push 降级推送"
    API_EXIT=0
    $PYTHON scripts/api_push.py docs/index.html data/dashboard_data.json data/dimension_data.json >> "$LOG_FILE" 2>&1 || API_EXIT=$?
    if [ "$API_EXIT" -eq 0 ]; then
        log "  ✓ API push 完成"
        PUSH_SUCCESS=1
        # 撤销本地 commit（远程已通过 API 更新，避免下次 push 冲突）
        git reset --mixed HEAD~1 >> "$LOG_FILE" 2>&1 || true
        git checkout -- docs/index.html data/dashboard_data.json data/dimension_data.json >> "$LOG_FILE" 2>&1 || true
    else
        log "  ✗ API push 也失败了"
        on_error  # 推送失败告警并退出
    fi
else
    log "  ✓ git push 完成"
    PUSH_SUCCESS=1
fi

# ============================================================
# Step 6: 周一推送访问地址和本周密码到企微群
# ============================================================
STEP_NAME="推送企微群消息"
WEBHOOK_FILE="$PROJECT_DIR/data/.webhook"
if [ "$WEEKDAY" = "1" ] && [ -f "$WEBHOOK_FILE" ]; then
    log "[7/7] 推送企微群消息..."
    WEBHOOK_URL=$(cat "$WEBHOOK_FILE")
    # 用 Python 写 UTF-8 JSON 文件，curl 从文件读取避免中文编码问题
    TMP_JSON="$PROJECT_DIR/tmp/wecom_push.json"
    TMP_JSON_WIN=$(to_win_path "$TMP_JSON")
    mkdir -p "$PROJECT_DIR/tmp"
    $PYTHON -c "
import json
data = {
    'msgtype': 'markdown',
    'markdown': {
        'content': '## 📊 FlashWater BI 看板 本周已更新\n> 数据已同步至 $(date '+%m月%d日')，看板已部署\n> 访问地址：[flashwater-BI.github.io](https://flashwater-BI.github.io/flashwater-bi/)\n> 本周密码：<font color=\"warning\">$PASSWORD</font>'
    }
}
with open(r'$TMP_JSON_WIN', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False)
" >> "$LOG_FILE" 2>&1 || true
    curl -s "$WEBHOOK_URL" \
        -H 'Content-Type: application/json; charset=utf-8' \
        -d "@$TMP_JSON_WIN" \
        >> "$LOG_FILE" 2>&1 || true
    rm -f "$TMP_JSON"
    log "  ✓ 企微群消息已推送"
elif [ "$WEEKDAY" = "1" ]; then
    log "[7/7] ⚠ 今天是周一但未配置企微webhook (data/.webhook)，跳过推送"
fi
# 非周一不做任何推送（失败告警由 trap on_error 处理，不受此限制）

# ============================================================
# 完成
# ============================================================
log "========== 部署完成 =========="
log "访问地址: https://flashwater-BI.github.io/flashwater-bi/"
log "本周密码: $PASSWORD"
log "密码文件: $PASSWORD_FILE"
log ""
