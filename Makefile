.PHONY: sync-addon build-addon test clean

# Sync package code to addon vendor directory
sync-addon:
	@echo "Syncing package to addon vendor..."
	rm -rf blender_addons/meshcat_html_importer/vendor/meshcat_html_importer
	cp -r packages/meshcat-html-importer/src/meshcat_html_importer \
		blender_addons/meshcat_html_importer/vendor/
	@echo "Done. Addon vendor synced."

# Build addon zip for distribution
build-addon: sync-addon
	@echo "Building addon zip..."
	cd blender_addons && zip -r ../meshcat_html_importer.zip meshcat_html_importer
	@echo "Created meshcat_html_importer.zip"

# Run all tests
test:
	uv run pytest

# Clean build artifacts
clean:
	rm -f meshcat_html_importer.zip
	rm -rf blender_addons/meshcat_html_importer/vendor/meshcat_html_importer
