"""Tests unitarios de SourceManager."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from core.book.source_manager import SourceManager
from core.database import get_db, init_db


@pytest.fixture(autouse=True)
def _init_db(tmp_path: str) -> None:
    db_path = os.path.join(tmp_path, "test_space_lair.db")
    os.environ["SPACE_LAIR_DB_PATH"] = db_path
    init_db()
    yield
    os.environ.pop("SPACE_LAIR_DB_PATH", None)
    conn = get_db()
    try:
        conn.execute("DELETE FROM sources")
        conn.commit()
    finally:
        conn.close()


def test_add_source_returns_dict() -> None:
    source = SourceManager.add_source(
        url="https://example.com/1",
        title="Ejemplo",
        source_type="web",
        relevance=7,
        chapter_ids=[1, 2],
    )
    assert source is not None
    assert source["url"] == "https://example.com/1"
    assert source["chapter_ids"] == [1, 2]
    assert source["accessed_at"] is not None


def test_add_source_deduplicates_by_hash() -> None:
    s1 = SourceManager.add_source(url="https://example.com/x", title="T1")
    s2 = SourceManager.add_source(url="https://example.com/x", title="T2")
    assert s1["id"] == s2["id"]
    assert s2["title"] == "T1"


def test_add_source_appends_chapter_ids() -> None:
    s1 = SourceManager.add_source(url="https://example.com/ch", chapter_ids=[1])
    s2 = SourceManager.add_source(url="https://example.com/ch", chapter_ids=[2])
    assert sorted(s2["chapter_ids"]) == [1, 2]


def test_get_source() -> None:
    s = SourceManager.add_source(url="https://example.com/get", title="Get")
    fetched = SourceManager.get_source(s["id"])
    assert fetched["url"] == "https://example.com/get"


def test_get_source_missing() -> None:
    assert SourceManager.get_source(99999) is None


def test_search_sources_by_query() -> None:
    SourceManager.add_source(url="https://a.com", title="Python", notes="lenguaje")
    SourceManager.add_source(url="https://b.com", title="Rust", notes="sistemas")
    results = SourceManager.search_sources(query="Python")
    assert len(results) == 1
    assert results[0]["url"] == "https://a.com"


def test_search_sources_by_type() -> None:
    SourceManager.add_source(url="https://a.com", source_type="academic")
    SourceManager.add_source(url="https://b.com", source_type="official")
    results = SourceManager.search_sources(source_type="academic")
    assert len(results) == 1
    assert results[0]["source_type"] == "academic"


def test_update_source() -> None:
    s = SourceManager.add_source(url="https://example.com/upd")
    updated = SourceManager.update_source(s["id"], notes="actualizada", relevance=9)
    assert updated["notes"] == "actualizada"
    assert updated["relevance"] == 9


def test_update_source_forbidden_fields() -> None:
    s = SourceManager.add_source(url="https://example.com/forbidden")
    with pytest.raises(ValueError):
        SourceManager.update_source(s["id"], url="https://other.com")


def test_mark_verified_and_conflicting() -> None:
    s = SourceManager.add_source(url="https://example.com/mark")
    verified = SourceManager.mark_verified(s["id"])
    assert verified["notes"] == "verified=True"

    conflict = SourceManager.mark_conflicting(s["id"], "datos contradictorios")
    assert conflict["notes"].startswith("conflict:")


def test_delete_source_protects_chapter_associations() -> None:
    s = SourceManager.add_source(url="https://example.com/del", chapter_ids=[1])
    with pytest.raises(ValueError):
        SourceManager.delete_source(s["id"])


def test_delete_source_without_associations() -> None:
    s = SourceManager.add_source(url="https://example.com/del2")
    ok = SourceManager.delete_source(s["id"])
    assert ok is True
    assert SourceManager.get_source(s["id"]) is None


def test_get_chapter_sources() -> None:
    SourceManager.add_source(url="https://example.com/c1", chapter_ids=[10])
    SourceManager.add_source(url="https://example.com/c2", chapter_ids=[20])
    SourceManager.add_source(url="https://example.com/c12", chapter_ids=[10, 20])
    sources = SourceManager.get_chapter_sources(10)
    urls = {x["url"] for x in sources}
    assert urls == {"https://example.com/c1", "https://example.com/c12"}


def test_associate_chapter() -> None:
    s = SourceManager.add_source(url="https://example.com/assoc", chapter_ids=[1])
    updated = SourceManager.associate_chapter(s["id"], 2)
    assert sorted(updated["chapter_ids"]) == [1, 2]


def test_calculate_relevance() -> None:
    base = {
        "source_type": "official",
        "publication_date": "2024-01-01",
        "author": "Autor",
        "publisher": "Editor",
        "relevance": 4,
    }
    assert SourceManager.calculate_relevance(base) == 8


def test_export_sources_json(tmp_path: str) -> None:
    out = os.path.join(str(tmp_path), "sources.json")
    SourceManager.add_source(url="https://example.com/exp", title="Exportable")
    path = SourceManager.export_sources_json(out)
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["url"] == "https://example.com/exp"
    assert "relevance" in data[0]
