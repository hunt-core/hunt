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
        self.type = type  # "message" | "redirect"
        self.text = text
        self.url = url
        self.message_type = message_type  # "success" | "error" | "warning" | "info"

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
