#!/bin/bash
# 快速提交并推送至 GitHub
# 用法:
#   bash quick-push.sh "提交信息"
#   bash quick-push.sh "提交信息" --log "新增"     # 同时追加到更新日志
#
# 示例:
#   bash quick-push.sh "修复K线图日期格式bug"
#   bash quick-push.sh "添加周线筛选功能" --log "新增"

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BRANCH="main"
REMOTE="origin"

cd "$REPO_DIR"

# ── 参数解析 ──
COMMIT_MSG=""
LOG_TYPE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --log)
            LOG_TYPE="$2"
            shift 2
            ;;
        *)
            COMMIT_MSG="$1"
            shift
            ;;
    esac
done

if [ -z "$COMMIT_MSG" ]; then
    echo "用法: bash quick-push.sh \"提交信息\" [--log 类型]"
    echo "类型: 新增 | 修复 | 变更 | 移除 | 安全"
    exit 1
fi

# ── 1. 检查状态 ──
echo "═══════════════════════════════════════════"
echo "  📋 当前变更:"
git status --short
echo ""

# 无变更则退出
if git diff --quiet && git diff --cached --quiet; then
    echo "  ⚠️  没有需要提交的变更"
    exit 0
fi

# ── 2. 暂存 ──
echo "───────────────────────────────────────────"
echo "  📦 暂存所有变更..."
git add -A

# ── 3. 提交 ──
echo "  ✅ 提交: $COMMIT_MSG"
git commit -m "$COMMIT_MSG"

# ── 4. 推送 ──
echo "  🚀 推送至 $REMOTE/$BRANCH ..."
git push "$REMOTE" "$BRANCH"

# ── 5. 可选：追加更新日志 ──
if [ -n "$LOG_TYPE" ]; then
    if [ -f "changelog.sh" ]; then
        bash changelog.sh "$LOG_TYPE" "$COMMIT_MSG"
        git add CHANGELOG.md
        git commit -m "📝 更新 CHANGELOG: $COMMIT_MSG" 2>/dev/null || true
        git push "$REMOTE" "$BRANCH"
        echo "  📝 已同步更新 CHANGELOG.md"
    fi
fi

echo "───────────────────────────────────────────"
echo "  ✅ 完成!"
echo "═══════════════════════════════════════════"
