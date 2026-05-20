from __future__ import annotations

from typing import Any


# Sentinel returned by when() when the condition is False and no default is given.
# to_response() / resolve() strips keys carrying this value from the output dict.
class _Omit:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<omit>"


_OMIT = _Omit()
_MISSING = object()


class ApiResource:
    """Transform a single model instance into a JSON-ready dict.

    Subclass and implement :meth:`to_array`::

        class UserResource(ApiResource):
            def to_array(self, request=None):
                return {
                    "id": self.instance.id,
                    "name": self.instance.name,
                    "email": self.when(request and request.user.is_admin,
                                       self.instance.email),
                    **self.merge_when(request and request.user.is_admin,
                                      {"role": self.instance.role}),
                }

    Return from a controller — hunt's kernel calls :meth:`to_response`
    automatically::

        def show(self, request, id):
            return UserResource(User.find_or_fail(id))

    Wrap a list::

        def index(self, request):
            return UserResource.collection(User.all().get())
    """

    def __init__(self, instance: Any) -> None:
        self.instance = instance

    # ------------------------------------------------------------------
    # Override in subclass
    # ------------------------------------------------------------------

    def to_array(self, request: Any = None) -> dict:
        raise NotImplementedError(f"{type(self).__name__} must implement to_array()")

    # ------------------------------------------------------------------
    # Conditional helpers
    # ------------------------------------------------------------------

    def when(self, condition: Any, value: Any, default: Any = _MISSING) -> Any:
        """Include *value* when *condition* is truthy, omit the key otherwise.

        Pass *default* to substitute a different value instead of omitting::

            "score": self.when(show_score, self.instance.score, default=0)
        """
        if condition:
            return value() if callable(value) else value
        if default is _MISSING:
            return _OMIT
        return default() if callable(default) else default

    def merge_when(self, condition: Any, data: Any) -> dict:
        """Merge a dict of attributes when *condition* is truthy, else merge nothing.

        Use with ``**`` unpacking in :meth:`to_array`::

            **self.merge_when(is_admin, {"admin_note": self.instance.note})
        """
        if callable(data):
            data = data()
        return data if condition else {}

    # ------------------------------------------------------------------
    # Resolution and response
    # ------------------------------------------------------------------

    def _resolve_array(self, d: dict) -> dict:
        """Strip keys whose value is the _OMIT sentinel."""
        return {k: v for k, v in d.items() if not isinstance(v, _Omit)}

    def resolve(self, request: Any = None) -> dict:
        """Return the cleaned array (sentinel values stripped)."""
        return self._resolve_array(self.to_array(request))

    def to_response(self, request: Any = None):
        from hunt.http.response import JsonResponse

        return JsonResponse(self.resolve(request))

    # ------------------------------------------------------------------
    # Collection shorthand
    # ------------------------------------------------------------------

    @classmethod
    def collection(cls, items: Any) -> ApiResourceCollection:
        """Wrap a list (or PaginationResult) of instances in a collection."""
        return ApiResourceCollection(items, cls)


class ApiResourceCollection:
    """Transform a list or paginated set of instances into a JSON response.

    Returned from a controller::

        def index(self, request):
            return UserResource.collection(User.all().get())

    With pagination::

        def index(self, request):
            page = int(request.query("page", "1"))
            result = User.query().paginate(per_page=20, page=page)
            return UserResource.collection(result)
    """

    def __init__(self, items: Any, resource_class: type[ApiResource]) -> None:
        from hunt.pagination import PaginationResult

        self._resource_class = resource_class
        if isinstance(items, PaginationResult):
            self._items = items.data
            self._pagination_meta = {
                "total": items.total,
                "per_page": items.per_page,
                "current_page": items.current_page,
                "last_page": items.last_page,
            }
        else:
            self._items = list(items)
            self._pagination_meta = None

    def to_array(self, request: Any = None) -> dict:
        data = [self._resource_class(item).resolve(request) for item in self._items]
        result: dict = {"data": data}
        if self._pagination_meta is not None:
            result["meta"] = {k: v for k, v in self._pagination_meta.items() if v is not None}
        return result

    def to_response(self, request: Any = None):
        from hunt.http.response import JsonResponse

        return JsonResponse(self.to_array(request))
