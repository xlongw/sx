#!/bin/bash
# 快速追加一条更新日志到 CHANGELOG.md
# 用法:
#   ./changelog.sh "新增" "添加了增量数据拉取功能"
#   ./changelog.sh "修复" "修复了日志重复写入 bug"
#   ./changelog.sh "变更" "将并行改为串行筛选"
#
# 无参数时打开 CHANGELOG.md 供手动编辑。

# 基于脚本自身位置定位 CHANGELOG.md，跨电脑/跨路径均可用
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHANGELOG="$SCRIPT_DIR/CHANGELOG.md"

# 启动时清理 .pyc 缓存，防止旧版本代码被加载
find "$SCRIPT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find "$SCRIPT_DIR" -type f -name "*.pyc" -delete 2>/dev/null

case "${1:-}" in
    "新增"|"修复"|"变更"|"移除"|"安全")
        TYPE="$1"
        MSG="${2:-未提供描述}"
        DATE=$(date +%Y-%m-%d)
        ENTRY="- **[$TYPE]** $DATE — $MSG"
        # 在 "最后更新" 行之前插入
        sed -i "\|^*最后更新|i $ENTRY" "$CHANGELOG"
        # 更新最后更新日期
        sed -i "s|^*最后更新:.*|*最后更新: $DATE*|" "$CHANGELOG"
        echo "已追加: $ENTRY"
        ;;
    "")
        echo "用法: ./changelog.sh <类型> <描述>"
        echo "类型: 新增 | 修复 | 变更 | 移除 | 安全"
        echo ""
        echo "当前最近5条更新:"
        grep -E '^\-\s\*\*' "$CHANGELOG" | head -5
        ;;
    *)
        echo "未知类型: $1 (支持: 新增 修复 变更 移除 安全)"
        ;;
esac
