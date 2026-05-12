# Hunt Nova — Admin Panel Implementation Plan

Laravel Nova-inspired admin panel for the hunt framework. Server-rendered Jinja2
templates styled with Tailwind CSS (CDN) and Alpine.js for minimal interactivity.
Dark sidebar layout throughout.

---

## Design Decisions

| Concern | Choice | Reason |
|---|---|---|
| Rendering | Server-rendered Jinja2 | Consistent with hunt's view layer; zero build step |
| CSS | Tailwind CSS via CDN (play CDN) | No build tooling required in the framework |
| Interactivity | Alpine.js via CDN | Handles dropdowns, modals, sidebar toggle — 15KB |
| Icons | Heroicons inline SVG | Same icon set as the JSX templates; no runtime dep |
| Base layout | `04-dark-sidebar-with-header.jsx` → Jinja2 | Exact dark sidebar the user wants |
| Resource table | `lists/tables/01-simple.jsx` + `09-with-checkboxes.jsx` | Sortable cols, bulk select |
| Forms | `forms/form-layouts/01-stacked.jsx` | Clean stacked layout for create/edit |
| Stat cards | `data-display/stats/01-with-trending.jsx` | 4-col grid with trend indicators |
| Modals | `overlays/modal-dialogs/03-simple-alert.jsx` | `<dialog>` element + Alpine toggle |
| Pagination | `navigation/pagination/01-card-footer-with-page-buttons.jsx` | Standard prev/next with page numbers |
| Badges | `elements/badges/08-flat.jsx` | Status chips on index rows |

---

## Module Location

```
hunt/src/hunt/admin/           ← entire admin module
```

Opt-in: developers call `Admin.register_to(app, prefix="/nova")` in `bootstrap/app.py`.
The module adds its own route group; nothing bleeds into the application's routes.

---

## Directory Structure

```
hunt/src/hunt/admin/
├── __init__.py
├── application.py             ← Admin singleton: register_resource(), register_to()
├── resource.py                ← AdminResource base class
├── field.py                   ← Field base class + modifiers (showOnIndex, sortable…)
├── action.py                  ← Action base class
├── filter.py                  ← Filter base class
├── lens.py                    ← Lens base class (custom table views)
│
├── fields/
│   ├── __init__.py
│   ├── text.py                ← Text, Email, Password, Slug
│   ├── textarea.py            ← Textarea, Markdown (plain textarea)
│   ├── number.py              ← Number, Currency
│   ├── boolean.py             ← Boolean (toggle in form, badge on index)
│   ├── select.py              ← Select (options list or callable)
│   ├── datetime_.py           ← DateTime, Date, Time
│   ├── badge.py               ← Badge (display-only, colour map)
│   ├── belongs_to.py          ← BelongsTo (dropdown in form, link on show)
│   └── has_many.py            ← HasMany (panel on show page, no form)
│
├── metrics/
│   ├── __init__.py
│   ├── value.py               ← ValueMetric: single number + trend
│   ├── trend.py               ← TrendMetric: value by period (chart.js sparkline)
│   └── partition.py           ← PartitionMetric: labeled slices (chart.js doughnut)
│
├── controllers/
│   ├── __init__.py
│   ├── dashboard.py           ← GET /nova
│   ├── resource.py            ← index / show / create / store / edit / update / destroy
│   ├── action.py              ← POST /nova/resources/{key}/actions/{action}
│   └── search.py             ← GET /nova/search  (global search JSON endpoint)
│
├── middleware/
│   ├── __init__.py
│   └── gate.py                ← AdminGate: blocks non-admin users; configurable callable
│
├── templates/
│   ├── admin/
│   │   ├── layout.html        ← Dark sidebar shell (converted from 04-dark-sidebar-with-header.jsx)
│   │   ├── dashboard.html     ← Metrics grid + recent activity
│   │   ├── resource/
│   │   │   ├── index.html     ← Sortable table + filters + bulk actions + pagination
│   │   │   ├── show.html      ← Detail panel + HasMany relationship panels
│   │   │   ├── create.html    ← Stacked form (create)
│   │   │   └── edit.html      ← Stacked form (pre-filled)
│   │   ├── partials/
│   │   │   ├── stat_card.html ← Single metric card partial
│   │   │   ├── table.html     ← Reusable table + header row + body rows
│   │   │   ├── pagination.html
│   │   │   ├── modal_confirm.html   ← Delete / action confirmation
│   │   │   ├── flash.html     ← Success/error flash banner
│   │   │   └── sidebar_nav.html    ← Sidebar nav items loop
│   │   └── fields/
│   │       ├── index/         ← Per-field partials for table cell display
│   │       │   ├── text.html, boolean.html, badge.html, datetime.html, belongs_to.html
│   │       └── form/          ← Per-field partials for create/edit form inputs
│   │           ├── text.html, email.html, password.html, textarea.html
│   │           ├── number.html, currency.html, boolean.html, select.html
│   │           ├── datetime.html, date.html, belongs_to.html
│   │           └── slug.html
│
└── console/
    └── make_admin_resource.py  ← hunt make:admin-resource {Name} [--model={Model}]
```

---

## Phase 1 — Core Foundation

**Goal:** Mount the admin at `/nova`, show a working dark-sidebar shell with a
dashboard stub. No resource management yet.

### 1.1 `admin/application.py` — Admin singleton

```python
class _Admin:
    _resources: list[type]     = []
    _dashboard_cards: list     = []
    _gate: callable | None     = None
    prefix: str                = "/nova"
    brand_name: str            = "Admin"

    def resource(self, cls: type) -> type:
        """Decorator: @Admin.resource"""
        self._resources.append(cls)
        return cls

    def dashboard(self, *cards) -> None:
        self._dashboard_cards = list(cards)

    def gate(self, fn: callable) -> None:
        """fn(request) -> bool. Deny non-admins."""
        self._gate = fn

    def register_to(self, router, prefix: str = "/nova") -> None:
        self.prefix = prefix
        # registers all routes under prefix with AdminGate middleware

Admin = _Admin()
```

### 1.2 Route registration

Routes added by `Admin.register_to(router)`:

```
GET  {prefix}                                → dashboard.index
GET  {prefix}/resources/{key}                → resource.index
GET  {prefix}/resources/{key}/create         → resource.create
POST {prefix}/resources/{key}                → resource.store
GET  {prefix}/resources/{key}/{id}           → resource.show
GET  {prefix}/resources/{key}/{id}/edit      → resource.edit
PUT  {prefix}/resources/{key}/{id}           → resource.update
DELETE {prefix}/resources/{key}/{id}         → resource.destroy
POST {prefix}/resources/{key}/actions/{slug} → action.run
GET  {prefix}/search                         → search.index (JSON)
```

### 1.3 `admin/middleware/gate.py` — AdminGate

```python
class AdminGate(Middleware):
    async def handle(self, request, next):
        from hunt.admin.application import Admin
        gate_fn = Admin._gate or (lambda req: Auth.check())
        if not gate_fn(request):
            if request.expects_json():
                raise HttpException(403)
            return RedirectResponse(route("login"))
        return await next(request)
```

### 1.4 Dark sidebar layout template

**Source:** `application-ui/application-shells/sidebar/04-dark-sidebar-with-header.jsx`
→ converted to Jinja2 HTML

Key classes preserved:
- Sidebar shell: `bg-gray-900` fixed left column, `w-72`
- Logo area: `flex h-16 items-center px-6`
- Nav items: `group flex gap-x-3 rounded-md p-2 text-sm font-semibold`
  - Inactive: `text-gray-400 hover:bg-gray-800 hover:text-white`
  - Active: `bg-gray-800 text-white`
- Section headers: `text-xs font-semibold text-gray-400 uppercase tracking-wider`
- Main content area: `lg:pl-72` with `bg-gray-950` body
- Top bar: `sticky top-0 z-40 bg-gray-900/80 backdrop-blur border-b border-white/10`
  with search input, notification bell, user avatar dropdown

Alpine.js drives:
- Mobile sidebar toggle (`x-data="{ open: false }"`)
- User dropdown menu
- Notification panel

Nav items generated from registered resources automatically. Sidebar sections:

```
■ Dashboard
─────────────────
Resources
  ■ Users
  ■ Posts
  ■ [each registered resource]
─────────────────
Tools
  ■ Settings
```

---

## Phase 2 — Resource CRUD

**Goal:** Full index/show/create/edit/destroy for any registered resource.

### 2.1 `admin/resource.py` — AdminResource base class

```python
class AdminResource:
    model: type                          # required: the Model subclass
    label: str | None         = None     # "Post" (defaults to model name)
    label_plural: str | None  = None     # "Posts"
    per_page: int             = 15
    search_columns: list[str] = []       # columns to search against
    orderable_columns: list[str] = []    # columns with sort links
    default_order: tuple      = ("id", "desc")
    actions_: list            = []       # bulk actions

    def fields(self) -> list:
        """Return list of Field instances for this resource."""
        raise NotImplementedError

    def filters(self) -> list:
        return []

    def actions(self) -> list:
        return []

    def metrics(self) -> list:
        return []

    @classmethod
    def slug(cls) -> str:
        """URL key, e.g. 'posts'"""
        return Str.plural(Str.snake(cls.model.__name__)).lower()

    def title(self, instance) -> str:
        """Human label for a row (used in breadcrumbs, BelongsTo dropdowns)."""
        return str(instance._attributes.get("name") or instance._attributes.get("title") or instance._attributes.get("id"))
```

### 2.2 Index view

**Source:** `lists/tables/01-simple.jsx` + `09-with-checkboxes.jsx` + `navigation/pagination/01-card-footer-with-page-buttons.jsx`

Features:
- Column headers are clickable sort links (appends `?sort=col&dir=asc|desc`)
- Search bar at top right (`?search=…`)
- Filter sidebar or dropdown (from resource's `filters()`)
- Checkbox column for bulk selection
- Bulk action dropdown (run on checked rows)
- Per-page selector (15 / 25 / 50 / 100)
- Pagination footer: `Showing 1–15 of 143 results` + prev/next + page numbers
- Each row: field cells, then Edit link + Delete button
- Delete triggers inline `<dialog>` confirmation modal (Alpine `x-data`)

Table header row (`bg-gray-800/50`, text `text-gray-400 text-xs uppercase`).
Rows: `divide-y divide-white/5`, hover `hover:bg-white/[0.02]`.

### 2.3 Show view (detail page)

**Source:** `data-display/description-lists/03-left-aligned-in-card.jsx`

Layout:
- Page heading with resource title + Edit / Delete buttons
- Description list card: field label (left) + rendered value (right)
  - `dt`: `text-sm font-medium text-gray-400`
  - `dd`: `text-sm text-white`
- HasMany relationship panels below: each renders as a sub-table with
  an "Add" button that opens a create modal

### 2.4 Create / Edit views

**Source:** `forms/form-layouts/01-stacked.jsx`

Layout:
- Page heading: "Create Post" / "Edit Post #42"
- Sections grouped by field `panel` attribute (default: no grouping)
- Each field rendered by its form partial (see fields system)
- Error messages inline under each field (red `text-sm text-red-400`)
- Footer: Save button (`bg-indigo-600`) + Cancel link

### 2.5 Delete confirmation

Pure HTML `<dialog>` element toggled by Alpine:
- `x-data="{ open: false }"`
- `@click.outside="open = false"`
- Warning icon (SVG), message, "Delete" (red) + "Cancel"
- Form POST with `_method=DELETE` hidden field

---

## Phase 3 — Fields System

### 3.1 Field base class

```python
class Field:
    def __init__(self, name: str, attribute: str | None = None, label: str | None = None):
        self.name      = name
        self.attribute = attribute or Str.snake(name)
        self.label     = label or name
        self._show_on_index  = True
        self._show_on_detail = True
        self._show_on_create = True
        self._show_on_edit   = True
        self._sortable       = False
        self._readonly       = False
        self._help_text      = None
        self._rules          = ""
        self._nullable       = False

    # Fluent modifiers
    def hide_from_index(self)   -> "Field": ...
    def hide_from_detail(self)  -> "Field": ...
    def only_on_forms(self)     -> "Field": ...
    def only_on_index(self)     -> "Field": ...
    def sortable(self)          -> "Field": ...
    def readonly(self)          -> "Field": ...
    def help(self, text: str)   -> "Field": ...
    def rules(self, *r: str)    -> "Field": ...
    def nullable(self)          -> "Field": ...

    def value_for(self, instance) -> Any:
        return instance._attributes.get(self.attribute)

    def render_index(self, instance) -> str:
        """Returns HTML string for table cell."""
        ...

    def render_form(self, instance | None, errors) -> str:
        """Returns HTML string for form field."""
        ...
```

### 3.2 Field catalogue

| Class | Index display | Form input | Notes |
|---|---|---|---|
| `Text(name)` | Plain text | `<input type="text">` | |
| `Email(name)` | Mailto link | `<input type="email">` | |
| `Password(name)` | `••••••••` | `<input type="password">` | Never shown on index/detail |
| `Slug(name)` | Text | `<input>` + JS slug-from-title | |
| `Textarea(name)` | Truncated | `<textarea>` | `.rows(n)` modifier |
| `Number(name)` | Right-aligned | `<input type="number">` | |
| `Currency(name)` | `$1,234.56` | Number input | `.currency("USD")` |
| `Boolean(name)` | Green/red dot badge | Toggle switch | |
| `Select(name, options)` | Label | `<select>` | options: list or callable |
| `Date(name)` | Formatted | `<input type="date">` | |
| `DateTime(name)` | Formatted + tz | `<input type="datetime-local">` | |
| `Badge(name, map)` | Coloured chip | Read-only on form | `map={"active":"green"}` |
| `BelongsTo(name, Resource)` | Link to related show page | Searchable `<select>` | `.searchable()` → AJAX |
| `HasMany(name, Resource)` | — (detail only) | — (panel only) | Sub-table on show page |

### 3.3 Field rendering

Each field has two Jinja2 partials:

- `admin/fields/index/{type}.html` — rendered inside `<td>`
- `admin/fields/form/{type}.html` — rendered inside form stacked row

Template partials receive context: `field`, `value`, `instance`, `errors`, `resource`.

Example `fields/form/text.html`:
```html
<div>
  <label for="{{ field.attribute }}" class="block text-sm font-medium text-gray-300">
    {{ field.label }}{% if 'required' in field._rules %} <span class="text-red-400">*</span>{% endif %}
  </label>
  <div class="mt-2">
    <input
      type="text"
      name="{{ field.attribute }}"
      id="{{ field.attribute }}"
      value="{{ value or '' }}"
      {% if field._readonly %}readonly{% endif %}
      class="block w-full rounded-md bg-white/5 px-3 py-1.5 text-sm text-white outline-1 -outline-offset-1
             outline-white/10 placeholder:text-gray-500 focus:outline-2 focus:-outline-offset-2
             focus:outline-indigo-500 {% if errors.has(field.attribute) %}outline-red-500{% endif %}"
    >
  </div>
  {% if errors.has(field.attribute) %}
    <p class="mt-1 text-sm text-red-400">{{ errors.first(field.attribute) }}</p>
  {% endif %}
  {% if field._help_text %}
    <p class="mt-1 text-sm text-gray-500">{{ field._help_text }}</p>
  {% endif %}
</div>
```

---

## Phase 4 — Actions & Filters

### 4.1 Actions

```python
class Action:
    name: str                              # "Publish Posts"
    slug: str | None = None               # auto from name
    destructive: bool = False             # shows red confirm modal
    confirmation_text: str = ""           # shown in confirm modal

    def handle(self, models: list) -> ActionResponse:
        """Process the action on the given model instances."""
        raise NotImplementedError
```

`ActionResponse`:
- `Action.message("Done.")` — flash success
- `Action.redirect(url)` — redirect after
- `Action.download(path)` — trigger file download

On the index page: "Actions" dropdown above the table. Selecting one and clicking
"Run Action" POSTs selected IDs to `/nova/resources/{key}/actions/{slug}`.
Destructive actions show a confirmation modal first (Alpine `x-data`).

Built-in actions provided by the framework:
- `DeleteAction` — soft delete or hard delete
- `RestoreAction` — restore soft-deleted records
- `ExportCsvAction` — download filtered rows as CSV

### 4.2 Filters

```python
class Filter:
    name: str
    attribute: str | None = None

    def apply(self, query: QueryBuilder, value: Any) -> QueryBuilder:
        raise NotImplementedError

    def options(self) -> list[dict]:
        """[{"label": "Active", "value": "1"}, …]"""
        return []
```

Filter UI: collapsible sidebar panel or popover button above the table.
Each active filter shown as a removable chip. URL-persisted (`?filter[status]=active`).

Built-in filters:
- `SelectFilter(name, options)` — dropdown
- `BooleanFilter(name)` — yes/no toggle
- `DateRangeFilter(name)` — from/to date inputs
- `TrashedFilter` — show / hide / only soft-deleted rows

---

## Phase 5 — Dashboard & Metrics

### 5.1 Dashboard registration

```python
Admin.dashboard(
    ValueMetric("Total Users", lambda: User.count(), trend="week"),
    ValueMetric("Revenue", lambda: Order.sum("total"), prefix="$", trend="month"),
    TrendMetric("New Orders", lambda period: Order.count_by_period(period)),
    PartitionMetric("Users by Role", lambda: User.group_count("role")),
)
```

Dashboard page (`admin/dashboard.html`):
- Metrics grid: 4-col on xl, 2-col on sm, 1-col on mobile
  - Based on `data-display/stats/01-with-trending.jsx` classes:
    `bg-gray-900 px-4 py-10 border border-white/5 rounded-lg`
- Recent activity table below metrics (last 10 created/updated records across all resources)
- "Quick Links" row: New {Resource} button for each registered resource

### 5.2 ValueMetric

```python
class ValueMetric:
    def __init__(self, name: str, resolver: callable, prefix="", suffix="", trend: str | None = None):
        ...

    def calculate(self) -> dict:
        # returns {"value": 1234, "previous_value": 1100, "trend": "+12.1%", "positive": True}
```

Template (`partials/stat_card.html`):

```html
<div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2
            bg-gray-900 border border-white/5 rounded-lg px-4 py-10 sm:px-6 xl:px-8">
  <dt class="text-sm/6 font-medium text-gray-400">{{ metric.name }}</dt>
  {% if metric.trend %}
    <dd class="text-xs font-medium {% if positive %}text-emerald-400{% else %}text-rose-400{% endif %}">
      {{ trend }}
    </dd>
  {% endif %}
  <dd class="w-full flex-none text-3xl/10 font-medium tracking-tight text-white">
    {{ prefix }}{{ value }}{{ suffix }}
  </dd>
</div>
```

### 5.3 TrendMetric

Renders a `<canvas>` element. Chart.js loaded from CDN in admin layout only when
a TrendMetric is present on the page. Sparkline data injected as `data-values` JSON
attribute; inline `<script>` initialises Chart.js after DOM ready.

```html
<canvas id="trend-{{ metric.slug }}"
        data-values="{{ values | tojson }}"
        data-labels="{{ labels | tojson }}"
        class="h-16 w-full"></canvas>
```

### 5.4 PartitionMetric

Doughnut chart via Chart.js. Colour palette: indigo, emerald, rose, amber, violet.
Legend rendered as `<ul>` beside the canvas.

---

## Phase 6 — Search & Relationships

### 6.1 Global search

`GET /nova/search?q=…` returns JSON:
```json
{
  "results": [
    {"resource": "users", "label": "Users", "items": [
      {"id": 1, "title": "John Doe", "url": "/nova/resources/users/1"}
    ]}
  ]
}
```

Search bar in top header sends requests via `fetch()` (15 lines of vanilla JS).
Results dropdown styled as command palette (`overlays/modal-dialogs` pattern).
Searches each resource's `search_columns` using LIKE.

### 6.2 HasMany relationship panels

On the show page, after the main description list, one panel per `HasMany` field:

```
┌─ Comments (14) ──────────────────────────────────── [+ Add Comment] ─┐
│  Body                  Author       Created At                        │
│  "Great post!"         Alice        2025-01-10                        │
│  "Thanks for sharing"  Bob          2025-01-11                        │
│  [1] [2] [3] … Next                                                   │
└───────────────────────────────────────────────────────────────────────┘
```

Each panel paginates independently (query param `?{relation}_page=2`).
"Add" button links to the related resource's create page with the foreign key
pre-filled as a hidden field.

### 6.3 BelongsTo searchable select

When `.searchable()` is called on a `BelongsTo` field:
- Form renders a text input + hidden ID field instead of a `<select>`
- Typing sends `GET /nova/resources/{key}/search?q=…` and shows a dropdown
- ~30 lines of Alpine.js + `fetch()`

---

## Phase 7 — Polish & CLI

### 7.1 Flash notifications

All admin controllers flash success/error messages. `partials/flash.html`:

```html
{% if flash.get('admin_success') %}
<div x-data="{ show: true }" x-show="show" x-init="setTimeout(() => show = false, 4000)"
     class="fixed bottom-4 right-4 z-50 flex items-center gap-3 rounded-lg bg-emerald-500/10
            border border-emerald-500/20 px-4 py-3 text-sm text-emerald-400">
  <!-- checkmark SVG -->
  {{ flash['admin_success'] }}
  <button @click="show = false" class="ml-auto text-emerald-400/60 hover:text-emerald-400">✕</button>
</div>
{% endif %}
```

### 7.2 Breadcrumbs

Every admin page has a breadcrumb bar below the top header:
`Admin > Users > #42 — John Doe > Edit`

### 7.3 Responsive mobile sidebar

Alpine `x-data="{ sidebarOpen: false }"` on the root `<div>`. Hamburger button in
the mobile top bar toggles `sidebarOpen`. Sidebar slides in from the left with a
dark overlay (same pattern as `04-dark-sidebar-with-header.jsx`'s Dialog).

### 7.4 Settings page (Tool)

A "Settings" entry always appears at the bottom of the sidebar. It renders a
configurable tool page (stacked form). Developers can register custom tool pages:

```python
Admin.tool("Settings", SettingsTool)
```

`SettingsTool` is a controller-like class with `index()` and `store()` methods.

### 7.5 CLI command — `hunt make:admin-resource`

```bash
hunt make:admin-resource PostResource --model=Post
```

Generates `app/admin/post_resource.py`:

```python
from hunt.admin.resource import AdminResource
from hunt.admin.fields import Text, Textarea, DateTime, Boolean, BelongsTo
from hunt.admin import Admin

@Admin.resource
class PostResource(AdminResource):
    model = Post
    search_columns = ["title", "slug"]
    per_page = 25

    def fields(self):
        return [
            Text("Title").sortable().rules("required", "string", "max:255"),
            Text("Slug").readonly(),
            Textarea("Body").rules("required"),
            Boolean("Published"),
            DateTime("Published At").nullable(),
        ]
```

### 7.6 `hunt admin:publish`

Copies admin templates into `resources/views/admin/` so the developer can
customise them. Subsequent admin renders prefer the published copies.

---

## Phase 8 — Authorization

### 8.1 Resource-level policies

Each AdminResource can declare:

```python
def can_view_any(self, request) -> bool: return True
def can_view(self, request, instance) -> bool: return True
def can_create(self, request) -> bool: return True
def can_update(self, request, instance) -> bool: return True
def can_delete(self, request, instance) -> bool: return True
def can_run_action(self, request, action, models) -> bool: return True
```

The resource controller calls these before every operation and responds with 403
if denied. Index silently hides rows that `can_view()` returns False for.

### 8.2 Admin gate

Default gate: `Auth.check()`. Override:

```python
Admin.gate(lambda req: Auth.check() and Auth.user()._attributes.get("is_admin"))
```

---

## Template Reference Map

| Admin view | Source JSX template |
|---|---|
| Sidebar shell | `application-shells/sidebar/04-dark-sidebar-with-header.jsx` |
| Dashboard stats | `data-display/stats/01-with-trending.jsx` |
| Resource table | `lists/tables/09-with-checkboxes.jsx` |
| Sortable table headers | `lists/tables/15-with-sortable-columns.jsx` |
| Show description list | `data-display/description-lists/03-left-aligned-in-card.jsx` |
| Create/edit form | `forms/form-layouts/01-stacked.jsx` |
| Input field | `forms/input-groups/07-input-with-leading-icon.jsx` |
| Select field | `forms/select-menus/01-simple.jsx` |
| Toggle field | `forms/toggles/01-simple.jsx` |
| Delete modal | `overlays/modal-dialogs/03-simple-alert.jsx` |
| Action modal | `overlays/modal-dialogs/02-centered-with-wide-buttons.jsx` |
| Pagination | `navigation/pagination/01-card-footer-with-page-buttons.jsx` |
| Sidebar nav items | `navigation/sidebar-navigation/02-dark.jsx` |
| Flash notification | `feedback/alerts/04-with-dismiss-button.jsx` |
| Empty state | `feedback/empty-states/01-simple.jsx` |
| Breadcrumbs | `navigation/breadcrumbs/01-contained.jsx` |
| Global search | `navigation/command-palettes/01-simple.jsx` |
| Badges (status) | `elements/badges/08-flat.jsx` |
| Trend sparkline | Chart.js 4.x via CDN (canvas element) |

---

## Implementation Order

```
Phase 1  Core foundation, routing, dark sidebar layout, dashboard stub
Phase 2  ResourceController — index (table) + create/store + edit/update + destroy
Phase 3  Fields system — all field types + form/index partials
Phase 4  Actions (bulk) + Filters
Phase 5  Dashboard metrics — ValueMetric, TrendMetric, PartitionMetric
Phase 6  Global search, HasMany panels, BelongsTo searchable
Phase 7  Flash, breadcrumbs, mobile responsive, CLI command, publish command
Phase 8  Per-resource authorization policies
```

Each phase is independently mergeable and testable. Phases 1–3 produce a fully
usable CRUD admin. Phases 4–8 add the Nova-level polish.

---

## Dependencies Added

```toml
# No new Python packages required.
# CDN assets loaded in admin layout only (not application layout):
#   - Tailwind CSS Play CDN  (development) or compiled CSS (production)
#   - Alpine.js 3.x          https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js
#   - Chart.js 4.x           https://cdn.jsdelivr.net/npm/chart.js  (dashboard only)
#   - Inter font             https://rsms.me/inter/inter.css
```

For production, add a `hunt admin:build` command that runs Tailwind CLI over the
admin templates and writes a compiled `admin.css` (eliminating the Play CDN).
