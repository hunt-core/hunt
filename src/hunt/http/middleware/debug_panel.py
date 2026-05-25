from __future__ import annotations

import html
import os
import time
from typing import Any

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response

_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "x-api-key", "x-auth-token", "x-csrf-token"})

_PRIVATE_SESSION_KEYS = frozenset(
    {
        "_csrf_token",
        "_flash_new",
        "_flash_old",
        "_auth_id",
        "_2fa_pending",
        "_2fa_pending_secret",
    }
)


def _is_debug() -> bool:
    return os.environ.get("APP_DEBUG", "false").lower() == "true"


class DebugPanel(Middleware):
    """Injects an HTML debug toolbar into every text/html response when APP_DEBUG=true."""

    async def handle(self, request: Request, next: Next) -> Response:
        if not _is_debug():
            return await next(request)

        t0 = time.monotonic()
        response = await next(request)
        elapsed_ms = (time.monotonic() - t0) * 1000

        if "text/html" not in response.content_type:
            return response

        body = response._body.decode("utf-8", errors="replace")
        close_idx = body.lower().rfind("</body>")
        if close_idx == -1:
            return response

        panel = _build_panel(request, response, elapsed_ms)
        response._body = (body[:close_idx] + panel + body[close_idx:]).encode("utf-8")
        return response


# ---------------------------------------------------------------------------
# Panel builder
# ---------------------------------------------------------------------------


def _e(value: Any) -> str:
    """HTML-escape a value."""
    return html.escape(str(value))


def _build_panel(request: Request, response: Response, elapsed_ms: float) -> str:
    from hunt.database.debug import get_query_log

    queries = get_query_log()
    query_count = len(queries)
    query_total_ms = sum(ms for _, ms in queries)

    method = _e(request.method)
    path = _e(request.path)
    status = response.status
    status_color = "#22c55e" if status < 300 else "#eab308" if status < 400 else "#ef4444"

    route_name = _e(getattr(request, "_debug_route_name", None) or "")
    route_uri = _e(getattr(request, "_debug_route_uri", None) or request.path)
    route_params = getattr(request, "_path_params", {}) or {}

    headers = {k: v for k, v in request.headers().items() if k.lower() not in _SENSITIVE_HEADERS}

    session_store = getattr(request, "_session", None)
    session_data: dict = {}
    if session_store is not None:
        session_data = {k: v for k, v in session_store.all().items() if k not in _PRIVATE_SESSION_KEYS}

    return f"""
<script>
(function(){{
  if(window.__huntDbgLoaded)return;
  window.__huntDbgLoaded=true;
  // Tailwind CDN for panel styling
  var s=document.createElement('script');
  s.src='https://cdn.tailwindcss.com';
  document.head.appendChild(s);
}})();
</script>
<div id="_hunt_dbg" style="position:fixed;bottom:0;left:0;right:0;z-index:2147483647;font-family:ui-monospace,monospace;font-size:12px;">
  <div onclick="var b=document.getElementById('_hunt_dbg_body');b.style.display=b.style.display==='none'?'block':'none'"
       style="cursor:pointer;background:#111827;color:#fff;display:flex;align-items:center;gap:16px;padding:6px 16px;border-top:1px solid #374151;">
    <span style="font-weight:700;color:#34d399;">hunt</span>
    <span style="color:#fde047;">{method}</span>
    <span style="color:#d1d5db;">{path}</span>
    <span style="color:{status_color};font-weight:600;">{status}</span>
    <span style="color:#9ca3af;">{elapsed_ms:.1f}ms</span>
    <span style="color:#60a5fa;">{query_count}Q&nbsp;/&nbsp;{query_total_ms:.1f}ms&nbsp;DB</span>
    <span style="flex:1;"></span>
    <span style="color:#6b7280;font-size:10px;">&#9650; debug</span>
  </div>
  <div id="_hunt_dbg_body" style="display:none;background:#030712;border-top:1px solid #374151;max-height:380px;overflow:auto;">
    <div style="display:flex;background:#111827;border-bottom:1px solid #374151;">
      {_tab_btn("request", "Request")}
      {_tab_btn("route", "Route")}
      {_tab_btn("queries", f"Queries ({query_count})")}
      {_tab_btn("session", "Session")}
    </div>
    <div id="_hunt_tab_request" style="display:block;padding:12px;">
      {_request_tab(method, path, status, elapsed_ms, headers)}
    </div>
    <div id="_hunt_tab_route" style="display:none;padding:12px;">
      {_route_tab(route_name, route_uri, route_params)}
    </div>
    <div id="_hunt_tab_queries" style="display:none;padding:12px;">
      {_queries_tab(queries)}
    </div>
    <div id="_hunt_tab_session" style="display:none;padding:12px;">
      {_session_tab(session_data)}
    </div>
  </div>
</div>
<script>
(function(){{
  var tabs=['request','route','queries','session'];
  window._huntDbgTab=function(t){{
    tabs.forEach(function(id){{
      var el=document.getElementById('_hunt_tab_'+id);
      var btn=document.getElementById('_hunt_tab_btn_'+id);
      if(el)el.style.display=id===t?'block':'none';
      if(btn){{
        btn.style.background=id===t?'#1f2937':'#111827';
        btn.style.color=id===t?'#fff':'#9ca3af';
      }}
    }});
  }};
}})();
</script>
"""


def _tab_btn(tab_id: str, label: str) -> str:
    return (
        f'<button id="_hunt_tab_btn_{tab_id}" '
        f"onclick=\"window._huntDbgTab('{tab_id}')\" "
        f'style="padding:6px 16px;background:#111827;color:#9ca3af;border:none;'
        f'border-right:1px solid #374151;cursor:pointer;font-size:12px;font-family:inherit;">'
        f"{html.escape(label)}</button>"
    )


def _kv_table(rows: list[tuple[str, Any]]) -> str:
    if not rows:
        return '<p style="color:#6b7280;">—</p>'
    cells = "".join(
        f"<tr>"
        f'<td style="color:#9ca3af;padding:2px 12px 2px 0;white-space:nowrap;vertical-align:top;">{_e(k)}</td>'
        f'<td style="color:#e5e7eb;word-break:break-all;">{_e(v)}</td>'
        f"</tr>"
        for k, v in rows
    )
    return f'<table style="border-collapse:collapse;width:100%;">{cells}</table>'


def _request_tab(method: str, path: str, status: int, elapsed_ms: float, headers: dict) -> str:
    meta = [
        ("Method", method),
        ("Path", path),
        ("Status", status),
        ("Elapsed", f"{elapsed_ms:.2f} ms"),
    ]
    header_rows = [(k, v) for k, v in sorted(headers.items())]
    return (
        '<p style="color:#34d399;margin-bottom:8px;font-weight:600;">Request</p>'
        + _kv_table(meta)
        + '<p style="color:#6b7280;margin:10px 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;">Headers</p>'
        + _kv_table(header_rows)
    )


def _route_tab(route_name: str, route_uri: str, route_params: dict) -> str:
    meta = [
        ("Name", route_name or "(unnamed)"),
        ("URI", route_uri),
    ]
    param_rows = list(route_params.items())
    return (
        '<p style="color:#34d399;margin-bottom:8px;font-weight:600;">Route</p>'
        + _kv_table(meta)
        + '<p style="color:#6b7280;margin:10px 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;">Parameters</p>'
        + (_kv_table(param_rows) if param_rows else '<p style="color:#6b7280;">—</p>')
    )


def _queries_tab(queries: list[tuple[str, float]]) -> str:
    if not queries:
        return '<p style="color:#6b7280;">No queries executed.</p>'
    total_ms = sum(ms for _, ms in queries)
    rows = "".join(
        f"<tr>"
        f'<td style="color:#9ca3af;padding:2px 8px 2px 0;white-space:nowrap;vertical-align:top;">{i + 1}.</td>'
        f'<td style="color:#e5e7eb;word-break:break-all;padding-right:12px;">{_e(sql)}</td>'
        f'<td style="color:#fde047;white-space:nowrap;vertical-align:top;">{ms:.2f}ms</td>'
        f"</tr>"
        for i, (sql, ms) in enumerate(queries)
    )
    return (
        f'<p style="color:#34d399;margin-bottom:8px;font-weight:600;">'
        f"{len(queries)} queries &mdash; {total_ms:.2f}ms total</p>"
        f'<table style="border-collapse:collapse;width:100%;">{rows}</table>'
    )


def _session_tab(session_data: dict) -> str:
    if not session_data:
        return '<p style="color:#6b7280;">Session is empty (or StartSession middleware is not active).</p>'
    import json

    rows = [(k, json.dumps(v, default=str)) for k, v in sorted(session_data.items())]
    return '<p style="color:#34d399;margin-bottom:8px;font-weight:600;">Session</p>' + _kv_table(rows)
