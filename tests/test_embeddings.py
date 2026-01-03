"""Tests for embeddings loading behavior.

These tests ensure unit tests don't accidentally import heavyweight ML stacks
(torch/transformers) unless explicitly opted-in.
"""

from __future__ import annotations

import builtins


def test_try_load_skips_heavy_imports_under_pytest(monkeypatch):
    from azurerbac.airecommender.embeddings import EmbeddingModel

    monkeypatch.delenv("AZURERBAC_DISABLE_EMBEDDINGS", raising=False)
    monkeypatch.delenv("AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS", raising=False)

    # Be explicit so this test remains robust even if PYTEST_CURRENT_TEST changes.
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_embeddings.py::test_try_load")

    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith(("sentence_transformers", "transformers", "torch")):
            raise AssertionError(f"Unexpected heavy import during tests: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    model = EmbeddingModel()
    assert model.try_load() is False


def test_try_load_respects_disable_env(monkeypatch):
    from azurerbac.airecommender.embeddings import EmbeddingModel

    monkeypatch.setenv("AZURERBAC_DISABLE_EMBEDDINGS", "1")
    monkeypatch.setenv("AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS", "1")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_embeddings.py::test_try_load")

    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith(("sentence_transformers", "transformers", "torch")):
            raise AssertionError(f"Unexpected heavy import when disabled: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    model = EmbeddingModel()
    assert model.try_load() is False
