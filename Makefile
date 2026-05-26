.PHONY: assets css watch test lint fmt

# Download and patch all vendor JS/CSS (Trix, EasyMDE, Font Awesome)
assets:
	python scripts/build_admin_assets.py

# Build admin.css from Tailwind (requires `npm install` first)
css:
	npm run build:css

# Watch mode for Tailwind during development
watch:
	npm run build:css:watch

# Full front-end build: vendor assets + Tailwind CSS
build-fe: assets css

# Install JS dev dependencies (Tailwind)
npm-install:
	npm install

# Python tests
test:
	python -m pytest tests/ -q

# Lint
lint:
	uv tool run ruff check src/

# Format
fmt:
	uv tool run ruff format src/
