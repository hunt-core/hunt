from __future__ import annotations

from typing import Any

from hunt.admin.field import Field


class MediaBrowser(Field):
    """A display-only field that opens the media manager modal.

    Does not persist any value to the database — it exists purely to let
    users browse uploaded media and copy image URLs while editing a record.
    """

    field_type: str = "media_browser"

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.hide_from_index()
        self.hide_from_detail()

    def value_for(self, instance: Any) -> Any:
        return None
