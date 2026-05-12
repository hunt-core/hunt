from __future__ import annotations

from hunt.http.request import Request
from hunt.http.response import Response


def index(request: Request) -> Response:
    from hunt.admin.application import Admin

    ctx = Admin._base_context(request)
    metrics = []
    for card in Admin._dashboard_cards:
        try:
            data = card.calculate()
            data["metric_type"] = card.metric_type
            metrics.append(data)
        except Exception:
            pass
    ctx["metrics"] = metrics
    ctx["title"] = "Dashboard"
    return Admin._render("admin/dashboard.html", ctx)
