#!/bin/bash

# novel2media 一键启动脚本

echo "🚀 启动 novel2media 开发环境..."
echo ""

# 检查并创建 data 目录
if [ ! -d "data" ]; then
    echo "📁 创建 data 目录..."
    mkdir -p data
fi

# 检查并创建 workspace 目录
if [ ! -d "workspace" ]; then
    echo "📁 创建 workspace 目录..."
    mkdir -p workspace/{novels,outputs,temp}
fi

echo ""
echo "📋 可用命令："
echo "  pnpm dev:backend    - 启动后端服务 (http://localhost:8000)"
echo "  pnpm dev:frontend   - 启动前端服务 (http://localhost:5173)"
echo "  pnpm dev            - 启动后端服务"
echo "  pnpm test           - 运行所有测试"
echo ""
echo "💡 提示：需要两个终端分别启动前后端"
echo ""
