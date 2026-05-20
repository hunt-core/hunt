from __future__ import annotations

from hunt.support.str import Str


class ActionResponse:
    """Result returned from an Action.handle() call."""

    def __init__(
        self,
        type: str,
        text: str = "",
        url: str = "",
        message_type: str = "success",
    ) -> None:
        self.type = type  # "message" | "redirect" | "download"
        self.text = text
        self.url = url
        self.message_type = message_type  # "success" | "error" | "warning" | "info"
        # download-specific fields
        self.download_content: str = ""
        self.download_filename: str = "export.csv"
        self.download_content_type: str = "text/csv"

    @classmethod
    def success(cls, text: str) -> ActionResponse:
        return cls(type="message", text=text, message_type="success")

    @classmethod
    def error(cls, text: str) -> ActionResponse:
        return cls(type="message", text=text, message_type="error")

    @classmethod
    def message(cls, text: str, type: str = "success") -> ActionResponse:
        return cls(type="message", text=text, message_type=type)

    @classmethod
    def redirect(cls, url: str) -> ActionResponse:
        return cls(type="redirect", url=url)

    @classmethod
    def download(
        cls,
        content: str,
        filename: str = "export.csv",
        content_type: str = "text/csv; charset=utf-8",
    ) -> ActionResponse:
        r = cls(type="download")
        r.download_content = content
        r.download_filename = filename
        r.download_content_type = content_type
        return r

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "text": self.text,
            "url": self.url,
            "message_type": self.message_type,
        }


class Action:
    """Base class for admin actions that operate on one or more model instances."""

    name: str = "Action"
    destructive: bool = False
    confirmation_text: str = ""

    @classmethod
    def slug(cls) -> str:
        return Str.snake(cls.name).replace(" ", "_").lower()

    def handle(self, request: object, models: list) -> ActionResponse:
        raise NotImplementedError(f"{type(self).__name__} must implement handle()")


class BulkDeleteAction(Action):
    """Built-in action that deletes all selected records."""

    name: str = "Delete Selected"
    destructive: bool = True
    confirmation_text: str = "Are you sure you want to delete the selected records? This cannot be undone."

    def handle(self, request: object, models: list) -> ActionResponse:
        deleted = 0
        for instance in models:
            try:
                instance.delete()
                deleted += 1
            except Exception:
                pass
        return ActionResponse.success(f"{deleted} record(s) deleted successfully.")


class RestoreAction(Action):
    """Built-in action that restores soft-deleted records."""

    name: str = "Restore Selected"
    destructive: bool = False
    confirmation_text: str = ""

    def handle(self, request: object, models: list) -> ActionResponse:
        restored = 0
        for instance in models:
            try:
                instance.restore()
                restored += 1
            except Exception:
                pass
        if restored == 0:
            return ActionResponse.error("No records could be restored.")
        return ActionResponse.success(f"{restored} record(s) restored successfully.")


class ExportCsvAction(Action):
    """Built-in action that downloads selected records as a CSV file."""

    name: str = "Export CSV"
    destructive: bool = False
    filename: str = "export.csv"

    def handle(self, request: object, models: list) -> ActionResponse:
        import csv
        import io

        if not models:
            return ActionResponse.error("No records selected for export.")

        headers = list(models[0]._attributes.keys())
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for instance in models:
            writer.writerow({k: (v if v is not None else "") for k, v in instance._attributes.items()})
        return ActionResponse.download(buf.getvalue(), filename=self.filename)
