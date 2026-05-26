from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# BelongsToMany field
# ---------------------------------------------------------------------------


class TestBelongsToManyField:
    def test_field_type(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")
        assert f.field_type == "belongs_to_many"

    def test_attribute_defaults_to_relation_method(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")
        assert f.attribute == "tags"

    def test_attribute_override(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags", attribute="custom_attr")
        assert f.attribute == "custom_attr"

    def test_hidden_from_index(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")
        assert f._show_on_index is False

    def test_shown_on_detail(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")
        assert f._show_on_detail is True

    def test_shown_on_edit(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")
        assert f._show_on_edit is True

    def test_shown_on_create(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")
        assert f._show_on_create is True

    def test_exported_from_fields_package(self):
        from hunt.admin.fields import BelongsToMany

        assert BelongsToMany is not None

    def test_get_current_items_returns_list(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        tag1 = MagicMock()
        tag1._attributes = {"id": 1}
        tag2 = MagicMock()
        tag2._attributes = {"id": 2}

        related_resource_inst = MagicMock()
        related_resource_inst.title.side_effect = lambda x: (x._attributes["id"] == 1 and "Alpha") or "Beta"

        MockResource = MagicMock(return_value=related_resource_inst)

        rel_obj = MagicMock()
        rel_obj.get_results.return_value = [tag1, tag2]

        instance = MagicMock()
        instance.tags.return_value = rel_obj

        f = BelongsToMany("Tags", MockResource, "tags")
        items = f.get_current_items(instance)
        assert isinstance(items, list)
        assert len(items) == 2
        assert items[0][0] == "1"

    def test_get_current_items_returns_empty_on_error(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        instance = MagicMock()
        instance.tags.side_effect = AttributeError("no such method")

        f = BelongsToMany("Tags", MockResource, "tags")
        assert f.get_current_items(instance) == []

    def test_get_options_returns_list(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        tag1 = MagicMock()
        tag1._attributes = {"id": 10}

        related_resource_inst = MagicMock()
        related_resource_inst.title.return_value = "Ten"
        related_resource_inst.model.query.return_value.limit.return_value.get.return_value = [tag1]

        MockResource = MagicMock(return_value=related_resource_inst)
        f = BelongsToMany("Tags", MockResource, "tags")
        opts = f.get_options()
        assert opts == [("10", "Ten")]

    def test_get_options_returns_empty_on_error(self):
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock(side_effect=Exception("db down"))
        f = BelongsToMany("Tags", MockResource, "tags")
        assert f.get_options() == []


# ---------------------------------------------------------------------------
# BelongsTo empty-string → None fix in _collect_data
# ---------------------------------------------------------------------------


class TestCollectDataBelongsToFix:
    def _make_request(self, form_data: dict) -> MagicMock:
        req = MagicMock()
        req.all.return_value = form_data
        req.file.return_value = None
        return req

    def test_empty_belongs_to_becomes_none(self):
        from hunt.admin.controllers.resource import _collect_data
        from hunt.admin.fields.belongs_to import BelongsTo

        MockResource = MagicMock()
        f = BelongsTo("Course", MockResource, attribute="course_id")
        request = self._make_request({"course_id": ""})
        data = _collect_data(request, [f])
        assert data["course_id"] is None

    def test_populated_belongs_to_passes_through(self):
        from hunt.admin.controllers.resource import _collect_data
        from hunt.admin.fields.belongs_to import BelongsTo

        MockResource = MagicMock()
        f = BelongsTo("Course", MockResource, attribute="course_id")
        request = self._make_request({"course_id": "42"})
        data = _collect_data(request, [f])
        assert data["course_id"] == "42"

    def test_belongs_to_many_excluded_from_collect_data(self):
        from hunt.admin.controllers.resource import _collect_data
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")
        request = self._make_request({"tags": "1,2,3"})
        data = _collect_data(request, [f])
        assert "tags" not in data

    def test_readonly_belongs_to_not_modified(self):
        from hunt.admin.controllers.resource import _collect_data
        from hunt.admin.fields.belongs_to import BelongsTo

        MockResource = MagicMock()
        f = BelongsTo("Course", MockResource, attribute="course_id").readonly()
        request = self._make_request({"course_id": ""})
        data = _collect_data(request, [f])
        # readonly fields excluded from allowed_attrs → not in data
        assert "course_id" not in data


# ---------------------------------------------------------------------------
# _sync_belongs_to_many
# ---------------------------------------------------------------------------


class TestSyncBelongsToMany:
    def _make_request(self, inputs: dict) -> MagicMock:
        req = MagicMock()
        req.input.side_effect = lambda key, default=None: inputs.get(key, default)
        return req

    def test_detach_and_reattach(self):
        from hunt.admin.controllers.resource import _sync_belongs_to_many
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")

        rel_obj = MagicMock()
        instance = MagicMock()
        instance.tags.return_value = rel_obj

        request = self._make_request({"tags": "1,3,5"})
        _sync_belongs_to_many(request, instance, [f])

        rel_obj.detach.assert_called_once_with()
        assert rel_obj.attach.call_count == 3
        rel_obj.attach.assert_any_call(1)
        rel_obj.attach.assert_any_call(3)
        rel_obj.attach.assert_any_call(5)

    def test_empty_csv_detaches_all(self):
        from hunt.admin.controllers.resource import _sync_belongs_to_many
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")

        rel_obj = MagicMock()
        instance = MagicMock()
        instance.tags.return_value = rel_obj

        request = self._make_request({"tags": ""})
        _sync_belongs_to_many(request, instance, [f])

        rel_obj.detach.assert_called_once_with()
        rel_obj.attach.assert_not_called()

    def test_none_instance_skipped(self):
        from hunt.admin.controllers.resource import _sync_belongs_to_many
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")
        request = self._make_request({"tags": "1,2"})
        # Should not raise
        _sync_belongs_to_many(request, None, [f])

    def test_exception_is_silenced(self):
        from hunt.admin.controllers.resource import _sync_belongs_to_many
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags")

        instance = MagicMock()
        instance.tags.side_effect = Exception("db error")

        request = self._make_request({"tags": "1"})
        # Should not raise
        _sync_belongs_to_many(request, instance, [f])

    def test_readonly_field_skipped(self):
        from hunt.admin.controllers.resource import _sync_belongs_to_many
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockResource = MagicMock()
        f = BelongsToMany("Tags", MockResource, "tags").readonly()

        rel_obj = MagicMock()
        instance = MagicMock()
        instance.tags.return_value = rel_obj

        request = self._make_request({"tags": "1,2"})
        _sync_belongs_to_many(request, instance, [f])

        rel_obj.detach.assert_not_called()
        rel_obj.attach.assert_not_called()


# ---------------------------------------------------------------------------
# relation_search — BelongsToMany support
# ---------------------------------------------------------------------------


class TestRelationSearchBelongsToMany:
    def _make_request(self, field: str = "", q: str = "") -> MagicMock:
        req = MagicMock()
        req.query.side_effect = lambda key, default=None: {"field": field, "q": q}.get(key, default)
        return req

    def test_returns_results_for_belongs_to_many_field(self):
        from hunt.admin.controllers.relation_search import search_relation
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        tag1 = MagicMock()
        tag1._attributes = {"id": 7}
        related_resource_inst = MagicMock()
        related_resource_inst.title.return_value = "Python"
        related_resource_inst.search_columns = ["name"]
        related_resource_inst.model.query.return_value.where.return_value.limit.return_value.get.return_value = [tag1]
        related_resource_inst.model.query.return_value.limit.return_value.get.return_value = [tag1]

        MockRelatedResource = MagicMock(return_value=related_resource_inst)

        f = BelongsToMany("Tags", MockRelatedResource, "tags")

        resource_inst = MagicMock()
        resource_inst.fields.return_value = [f]

        MockResourceCls = MagicMock(return_value=resource_inst)

        with patch("hunt.admin.application.Admin") as MockAdmin:
            MockAdmin.find_resource.return_value = MockResourceCls
            request = self._make_request(field="tags", q="py")
            response = search_relation(request, "posts")

        assert response.status == 200

    def test_returns_empty_for_unknown_field(self):
        from hunt.admin.controllers.relation_search import search_relation
        from hunt.admin.fields.belongs_to_many import BelongsToMany

        MockRelatedResource = MagicMock()
        f = BelongsToMany("Tags", MockRelatedResource, "tags")

        resource_inst = MagicMock()
        resource_inst.fields.return_value = [f]
        MockResourceCls = MagicMock(return_value=resource_inst)

        with patch("hunt.admin.application.Admin") as MockAdmin:
            MockAdmin.find_resource.return_value = MockResourceCls
            request = self._make_request(field="nonexistent", q="")
            response = search_relation(request, "posts")

        assert response.status == 200
