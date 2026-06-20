# Novel2Media 开发脚本
.PHONY: dev backend frontend lint typecheck format quality test precommit

# ──────────────────────────────────────────────────────────────────────
# 开发启动
# ──────────────────────────────────────────────────────────────────────

# 启动后端（默认）
dev: backend

# 启动后端服务
backend:
	cd apps/backend && uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 启动前端服务
frontend:
	cd apps/frontend && pnpm dev

# ──────────────────────────────────────────────────────────────────────
# 代码质量
# ──────────────────────────────────────────────────────────────────────

# Lint 检查（自动修复）
lint:
	uv run ruff check apps/backend packages/novel2media-core/src packages/novel2media-logging/src tests --fix

# 类型检查
typecheck:
	npx pyright

# 代码格式化
format:
	uv run ruff format apps/backend packages/novel2media-core/src packages/novel2media-logging/src tests

# 全量质量检查（格式化 + Lint + 类型检查）
quality:
	make format
	make lint
	make typecheck
	@echo "✅ 所有代码质量检查通过！"

# ──────────────────────────────────────────────────────────────────────
# 测试
# ──────────────────────────────────────────────────────────────────────

# 运行所有测试
test:
	uv run pytest tests/ -v

# 仅后端测试
test-backend:
	uv run pytest tests/backend -v

# 仅核心库测试
test-core:
	uv run pytest tests/novel2media-core -v

# ──────────────────────────────────────────────────────────────────────
# 提交前完整检查
# ──────────────────────────────────────────────────────────────────────

precommit:
	make format
	make lint
	make typecheck
	uv run pytest tests/ -v --tb=short
	@echo "✅ 所有提交前检查通过！"
