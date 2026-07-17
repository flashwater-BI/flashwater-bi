#!/bin/bash
# ============================================================
# FlashWater BI 看板 — 每日自动部署脚本
# 流程: 同步数据 → 导出JSON → 构建HTML → 推送到GitHub Pages
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="C:/Users/altermind/.workbuddy/binaries/python/versions/3.13.12/python.exe"
LOG_FILE="$PROJECT_DIR/deploy.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

cd "$PROJECT_DIR"

log "========== FlashWater BI 每日部署开始 =========="

# Step 1: 增量同步万里牛数据
log "[1/4] 同步万里牛最新数据..."
$PYTHON scripts/sync_incremental.py >> "$LOG_FILE" 2>&1
log "  ✓ 数据同步完成"

# Step 2: 导出看板数据JSON
log "[2/4] 导出看板数据..."
$PYTHON scripts/export_data.py >> "$LOG_FILE" 2>&1
log "  ✓ 数据导出完成"

# Step 3: 构建看板HTML（数据注入模板）
log "[3/4] 构建看板HTML..."
$PYTHON scripts/build_dashboard.py >> "$LOG_FILE" 2>&1
log "  ✓ 看板构建完成"

# Step 4: 复制到docs目录并推送到GitHub Pages
log "[4/4] 部署到GitHub Pages..."

# 复制构建产物到 docs/ (GitHub Pages 默认目录)
cp "$PROJECT_DIR/outputs/dashboard_v3.html" "$PROJECT_DIR/docs/index.html"

# 提交并推送
cd "$PROJECT_DIR"
git add docs/index.html data/dashboard_data.json data/dimension_data.json
git commit -m "自动更新: $(date '+%Y-%m-%d %H:%M')" || log "  (无变更，跳过commit)"

# 推送到GitHub
git push origin master 2>&1 | tee -a "$LOG_FILE"
log "  ✓ 推送完成"

log "========== 部署完成 =========="
log "访问地址: https://flashwater-BI.github.io/flashwater-bi/"
log ""
