# 质量门与常用命令。全绿才许 commit（CLAUDE.md 铁律 2）。

.PHONY: check fmt test serve

check:
	uv run ruff format --check src tests
	uv run ruff check src tests
	uv run mypy
	uv run pytest -q

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

test:
	uv run pytest -q

serve:
	uv run grok-search --transport http --port 8000

demo-search:
	uv run --env-file .env python -m grok_search.demo $(q)
