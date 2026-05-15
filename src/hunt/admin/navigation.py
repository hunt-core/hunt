from __future__ import annotations

_DEFAULT_RESOURCE_ICON = (
    "M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 0 1-1.125-1.125M3.375 19.5h1.5"
    "C5.496 19.5 6 18.996 6 18.375m-3.75 0V5.625m0 12.75v-1.5c0-.621.504-1.125"
    " 1.125-1.125m18.375 2.625V5.625m0 12.75c0 .621-.504 1.125-1.125 1.125h-1.5"
    "m3.375-13.5A1.125 1.125 0 0 0 20.625 4.5h-1.5m3.375 1.125v12m-15 0h7.5"
    "m-7.5 0a1.125 1.125 0 0 1-1.125-1.125M4.5 18.375V5.625m0 12.75h1.5"
    "m12 0h1.5m-1.5 0a1.125 1.125 0 0 1-1.125-1.125M18 5.625v12.75M6 5.625h12"
)

_DEFAULT_LINK_ICON = (
    "M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364"
    "l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5"
    "a4.5 4.5 0 0 0 1.242 7.244"
)

_DEFAULT_TOOL_ICON = (
    "M11.42 15.17 17.25 21A2.652 2.652 0 0 0 21 17.25l-5.877-5.877"
    "M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766"
    "M11.42 15.17l-4.655 5.653a2.548 2.548 0 1 1-3.586-3.586l5.654-4.654"
    "m5.5-2.998 3.033-2.496c.14-.469-.382-.89-.766-1.208L15.17 11.42m0 0-5 5"
)


class NavGroup:
    """A named group in the sidebar containing NavResource and NavLink items."""

    item_type = "group"

    def __init__(self, label: str, items: list) -> None:
        self.label = label
        self.items = items


class NavResource:
    """A resource entry in the sidebar, optionally with a custom icon or label."""

    item_type = "resource"

    def __init__(
        self,
        resource_cls: type,
        icon: str | None = None,
        label: str | None = None,
    ) -> None:
        self.resource_cls = resource_cls
        self._icon = icon
        self._label = label

    @property
    def label(self) -> str:
        return self._label if self._label is not None else self.resource_cls.get_label_plural()

    def effective_icon(self) -> str:
        if self._icon is not None:
            return self._icon
        return getattr(self.resource_cls, "icon", _DEFAULT_RESOURCE_ICON)


class NavLink:
    """An arbitrary link in the sidebar — internal or external."""

    item_type = "link"

    def __init__(
        self,
        label: str,
        url: str,
        icon: str | None = None,
        external: bool = False,
    ) -> None:
        self.label = label
        self.url = url
        self.external = external
        self._icon = icon

    def effective_icon(self) -> str:
        return self._icon if self._icon is not None else _DEFAULT_LINK_ICON
