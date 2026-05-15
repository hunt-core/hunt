from __future__ import annotations

from typing import Any

from hunt.admin.field import Field


class Image(Field):
    field_type: str = "image"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._disk: str = "public"
        self._path: str = "uploads"
        self._max_kb: int = 5120  # 5 MB default
        self._accepted: str = "image/*"
        # Default to safe raster types; SVG excluded (can carry inline scripts)
        self._rules: list[str] = ["mimes:jpg,jpeg,png,gif,webp"]
        self.hide_from_index()

    def disk(self, disk: str) -> Image:
        self._disk = disk
        return self

    def path(self, path: str) -> Image:
        self._path = path
        return self

    def max_size(self, kb: int) -> Image:
        self._max_kb = kb
        return self

    def accepts(self, types: str) -> Image:
        """Set the accepted MIME types, e.g. 'image/png,image/jpeg'."""
        self._accepted = types
        return self

    def display_value(self, instance: Any) -> str:
        val = self.value_for(instance)
        return str(val) if val else ""

    def url_for(self, instance: Any) -> str | None:
        val = self.value_for(instance)
        if not val:
            return None
        try:
            from hunt.storage.manager import Storage

            return Storage.disk(self._disk).url(str(val))
        except Exception:
            return str(val)
