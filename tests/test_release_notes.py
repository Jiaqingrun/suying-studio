from __future__ import annotations

from engine.ops.release_notes import (
    compact_notes,
    load_catalog,
    render_changelog_markdown,
    render_notes_markdown,
)


def test_catalog_covers_known_t2s_versions() -> None:
    catalog = load_catalog()
    versions = catalog["versions"]
    for ver in ("0.2.0", "0.6.11", "0.7.11", "0.7.21", "0.7.24"):
        assert ver in versions
        entry = versions[ver]
        assert entry["title"]
        assert entry["summary"]
        assert entry["highlights"]
    notes = compact_notes("0.7.24")
    assert "轮换" in notes
    assert len(notes) <= 4000
    md = render_notes_markdown(
        "0.7.24",
        build_date="20260812T081910Z",
        release_seq=69,
        backfilled=True,
    )
    assert "0.7.24" in md
    assert "发布序号 `69`" in md
    assert "不改动" in md
    clone = render_notes_markdown("0.6.1", build_date="20260801T184658Z")
    assert "clone" in clone
    changelog = render_changelog_markdown()
    assert changelog.startswith("# 速影 Studio 更新说明")
    assert "## 0.7.24" in changelog
    assert changelog.index("## 0.7.24") < changelog.index("## 0.2.0")
    t2s = render_changelog_markdown(for_t2s=True)
    assert "packaging/release_notes.json" not in t2s
    assert "## 0.7.26" in t2s
