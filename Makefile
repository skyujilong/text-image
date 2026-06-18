# 代码质量检查脚本

.PHONY: lint typecheck format quality test precommit

# Lint 检查（自动修复）
lint:
	uv run ruff check apps/backend packages/novel2media-core/src tests --fix

# 类型检查
typecheck:
	npx pyright

# 代码格式化
format:
	uv run ruff format apps/backend packages/novel2media-core/src tests

# 全量质量检查（格式化 + Lint + 类型检查）
quality:
	make format
	make lint
	make typecheck
	@echo "✅ 所有代码质量检查通过！"

# 运行所有测试
test:
	uv run pytest tests/ -v

# 提交前执行的完整检查
precommit:
	make format
	make lint
	make typecheck
	uv run pytest tests/ -v --tb=short
	@echo "✅ 所有提交前检查通过！"
