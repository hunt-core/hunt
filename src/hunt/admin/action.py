from __future__ import annotations

from typing import Any

from hunt.support.str import Str

# Leading characters that make a spreadsheet treat a CSV cell as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: Any) -> Any:
    """Neutralise formula injection: prefix risky text cells with an apostrophe."""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


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
    # Set True for actions that must also see soft-deleted records (restore).
    with_trashed: bool = False
    # The AdminResource the action is running for; set by the controller.
    resource: Any = None

    @classmethod
    def slug(cls) -> str:
        return Str.snake(cls.name).replace(" ", "_").lower()

    def authorize(self, request: object, resource: Any, instance: Any) -> bool:
        """Whether this action may run on ``instance``. Checked for every selected record.

        Defaults to the resource's update permission; override for actions
        that need a different one.
        """
        return resource.can_update(request, instance)

    def handle(self, request: object, models: list) -> ActionResponse:
        raise NotImplementedError(f"{type(self).__name__} must implement handle()")


class BulkDeleteAction(Action):
    """Built-in action that deletes all selected records."""

    name: str = "Delete Selected"
    destructive: bool = True
    confirmation_text: str = "Are you sure you want to delete the selected records? This cannot be undone."

    def authorize(self, request: object, resource: Any, instance: Any) -> bool:
        return resource.can_delete(request, instance)

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
    with_trashed: bool = True

    def authorize(self, request: object, resource: Any, instance: Any) -> bool:
        return resource.can_update(request, instance)

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

    def authorize(self, request: object, resource: Any, instance: Any) -> bool:
        # Exporting is a read: it needs view permission, not update.
        return resource.can_view_any(request)

    def _columns(self, models: list) -> list[str]:
        """Columns to export: the resource's index/detail fields, never hidden or sensitive ones."""
        from hunt.admin.audit import sensitive_attributes

        resource = self.resource
        blocked = sensitive_attributes(resource, type(models[0]) if resource is None else None)
        columns: list[str] = []
        if resource is not None:
            try:
                for field in resource.fields():
                    if not (field._show_on_index or field._show_on_detail):
                        continue
                    if field.field_type in ("has_many", "belongs_to_many", "media_browser", "password"):
                        continue
                    if field.attribute not in columns:
                        columns.append(field.attribute)
            except Exception:
                columns = []
        if not columns:
            columns = list(models[0]._attributes.keys())
        return [c for c in columns if c not in blocked]

    def handle(self, request: object, models: list) -> ActionResponse:
        import csv
        import io

        if not models:
            return ActionResponse.error("No records selected for export.")

        headers = self._columns(models)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for instance in models:
            writer.writerow({k: _csv_cell(instance._attributes.get(k)) for k in headers})
        return ActionResponse.download(buf.getvalue(), filename=self.filename)
