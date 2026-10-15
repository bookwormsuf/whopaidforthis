"""Tests for the collection-time CI gate in tests/conftest.py (sprint 9 review
item 5): in CI, an unreachable Postgres must fail collection, not silently skip
the store tests. Imports the conftest module directly rather than relying on
pytest's fixture machinery, since `_REACHABLE` is computed once at import time.
"""

from __future__ import annotations

import pytest

import tests.conftest as conftest_module


class _FakeItem:
    def __init__(self, *, postgres: bool) -> None:
        self.keywords = {"postgres": True} if postgres else {}
        self.markers: list[object] = []

    def add_marker(self, marker: object) -> None:
        self.markers.append(marker)


def test_host_and_port_never_includes_the_full_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conftest_module,
        "TEST_DATABASE_URL",
        "postgresql://someuser:secretpass@db.example.com:5433/whopaid_test",
    )
    result = conftest_module._host_and_port()
    assert result == "db.example.com:5433"
    assert "someuser" not in result
    assert "secretpass" not in result


def test_ci_env_set_and_unreachable_fails_collection_instead_of_skipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conftest_module, "_REACHABLE", False)
    monkeypatch.setenv("CI", "true")
    items = [_FakeItem(postgres=True)]

    with pytest.raises(pytest.UsageError) as exc_info:
        conftest_module.pytest_collection_modifyitems(config=None, items=items)  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert conftest_module.TEST_DATABASE_URL not in message
    assert conftest_module._host_and_port() in message


def test_no_ci_env_and_unreachable_still_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest_module, "_REACHABLE", False)
    monkeypatch.delenv("CI", raising=False)
    item = _FakeItem(postgres=True)

    conftest_module.pytest_collection_modifyitems(config=None, items=[item])  # type: ignore[arg-type]

    assert len(item.markers) == 1
