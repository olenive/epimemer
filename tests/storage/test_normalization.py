"""Unit tests for the write-time normalization every backend must apply.

These pin the *rules* in one place. The parity suite proves each backend obeys
them; this proves what "them" is, so a new backend has something to read that
is shorter than an adapter.

Background: Python has one "nothing" (`None`). SurrealDB has two — `NULL` (the
key exists, its value is nothing) and `NONE` (the key does not exist) — and the
Python driver can only produce `NONE` for a parameterized value. Every backend
therefore normalizes to absence before writing, so behaviour does not depend on
which backend is configured.
"""

import inspect
import types
import typing

from pydantic import BaseModel

from epimemer.core import types as core_types
from epimemer.core.types import Topic
from epimemer.storage.protocol import drop_none_values, normalize_for_storage


class TestDropNoneValues:
    def test_drops_none_valued_key(self):
        assert drop_none_values({"a": None, "keep": 1}) == {"keep": 1}

    def test_drops_recursively(self):
        assert drop_none_values({"outer": {"a": None, "keep": 1}}) == {"outer": {"keep": 1}}

    def test_drops_inside_dicts_nested_in_lists(self):
        assert drop_none_values({"xs": [{"a": None, "keep": 1}]}) == {"xs": [{"keep": 1}]}

    def test_preserves_none_as_a_list_element(self):
        """Arrays keep their positions; dropping would shift every later index."""
        assert drop_none_values({"xs": [1, None, 2]}) == {"xs": [1, None, 2]}

    def test_preserves_empty_dict(self):
        """An empty dict is a value, not an absence."""
        assert drop_none_values({"a": {}, "keep": 1}) == {"a": {}, "keep": 1}

    def test_preserves_falsy_values(self):
        """Only None is dropped — 0, "" and False are information."""
        payload = {"zero": 0, "empty": "", "false": False, "none": None}
        assert drop_none_values(payload) == {"zero": 0, "empty": "", "false": False}

    def test_does_not_mutate_its_argument(self):
        payload = {"a": None, "nested": {"b": None}}
        drop_none_values(payload)
        assert payload == {"a": None, "nested": {"b": None}}

    def test_passes_scalars_through(self):
        assert drop_none_values("text") == "text"
        assert drop_none_values(3) == 3
        assert drop_none_values(None) is None


class TestNormalizeForStorage:
    def test_normalizes_dict_fields(self):
        topic = Topic(content="x", source_id="s1", metadata={"a": None, "keep": 1})
        assert normalize_for_storage(topic).metadata == {"keep": 1}

    def test_leaves_the_original_untouched(self):
        topic = Topic(content="x", source_id="s1", metadata={"a": None})
        normalize_for_storage(topic)
        assert topic.metadata == {"a": None}

    def test_leaves_declared_none_fields_alone(self):
        """Only dict *contents* are normalized; a nullable field stays None.

        Nullable model fields need no special handling: the key is absent from
        the stored row either way, and Pydantic refills the `= None` default on
        read, so they round-trip unchanged.
        """
        topic = Topic(content="x", source_id=None)
        assert normalize_for_storage(topic).source_id is None

    def test_preserves_non_dict_fields(self):
        topic = Topic(content="x", source_id="s1", metadata={"a": None})
        normalized = normalize_for_storage(topic)
        assert normalized.content == "x"
        assert normalized.id == topic.id


def _nullable_fields():
    """Every (model, field) in core.types whose annotation admits None."""
    for _, model in inspect.getmembers(core_types, inspect.isclass):
        if not issubclass(model, BaseModel) or model is BaseModel:
            continue
        if model.__module__ != core_types.__name__:
            continue
        for name, field in model.model_fields.items():
            annotation = field.annotation
            admits_none = (
                annotation is None
                or (
                    isinstance(annotation, types.UnionType)
                    or typing.get_origin(annotation) is typing.Union
                )
                and type(None) in typing.get_args(annotation)
            )
            if admits_none:
                yield model, name, field


class TestNullableFieldsDefaultToNone:
    """A nullable field must default to None — enforced, not merely observed.

    No backend can store a None: SurrealDB omits the key entirely, and reads
    reconstruct the value from the Pydantic default. That is lossless *only*
    while the default is None. A field declared `x: str | None = "unknown"`
    would be written as None and read back as "unknown" — silent corruption
    that normalization cannot prevent, because the key is absent either way.

    This fails at definition time so the trap is caught when the field is
    written rather than when data is lost.
    """

    def test_every_nullable_field_defaults_to_none(self):
        offenders = [
            f"{model.__name__}.{name} = {field.get_default()!r}"
            for model, name, field in _nullable_fields()
            if field.get_default() is not None
        ]
        assert not offenders, (
            "Nullable fields must default to None so they survive a storage "
            "round trip (the key is absent in the row; Pydantic refills the "
            "default on read). Offending fields: " + ", ".join(offenders)
        )

    def test_the_check_actually_finds_fields(self):
        """Guard against the introspection silently matching nothing."""
        found = {(m.__name__, n) for m, n, _ in _nullable_fields()}
        assert ("Topic", "source_id") in found
        assert len(found) >= 5
