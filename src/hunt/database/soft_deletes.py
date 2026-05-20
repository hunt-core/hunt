from __future__ import annotations


class SoftDeletes:
    """Mixin that enables soft-delete behaviour on a Model subclass.

    Usage::

        from hunt.database.model import Model
        from hunt.database.soft_deletes import SoftDeletes

        class Post(SoftDeletes, Model):
            table = "posts"

    ``delete()`` sets ``deleted_at`` instead of removing the row.
    ``restore()`` clears ``deleted_at``.
    ``force_delete()`` removes the row permanently.
    ``Post.query().only_trashed()`` restricts to soft-deleted rows.
    ``Post.query().with_trashed()`` includes soft-deleted rows.
    """

    _soft_deletes: bool = True
