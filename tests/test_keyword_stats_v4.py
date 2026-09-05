"""keyword_stats must understand v4 keyword_pool.themes (not only legacy top-level themes)."""

from __future__ import annotations

from engine.ops.keyword_stats import _count_themes, keyword_stats


def test_count_themes_v4_keyword_pool():
    data = {
        "meta": {"version": 4},
        "keyword_pool": {
            "themes": {
                "default": {
                    "label": "默认",
                    "keywords": [{"text": "始峰五金", "weight": 1}, {"text": "现货", "weight": 1}],
                },
                "五金耗材": {"label": "五金", "keywords": [{"text": "螺丝"}]},
            },
            "hooks": ["工地缺料"],
        },
    }
    themes, total = _count_themes(data)
    assert total == 3
    assert themes["default"] == 2
    assert themes["五金耗材"] == 1


def test_count_themes_legacy_top_level():
    data = {"themes": {"default": ["a", "b", "c"], "配送": ["d"]}}
    themes, total = _count_themes(data)
    assert total == 4
    assert themes["default"] == 3


def test_keyword_stats_prefers_active_pack(monkeypatch):
    class FakePack:
        def __init__(self, status: str, data_json: dict, version: int = 1):
            self.status = status
            self.data_json = data_json
            self.version = version
            self.name = "default"

    packs = [
        FakePack("archived", {"themes": {"default": ["old"]}}),
        FakePack(
            "active",
            {
                "meta": {"version": 4},
                "keyword_pool": {
                    "themes": {"default": {"keywords": [{"text": "a"}, {"text": "b"}]}}
                },
            },
            version=4,
        ),
    ]

    class _ScalarResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    class FakeSession:
        def scalars(self, _stmt):
            return _ScalarResult(packs)

        def scalar(self, _stmt):
            return 0

    out = keyword_stats(FakeSession(), customer_id=1, keyword_pack_path=None)
    assert out["empty"] is False
    assert out["total"] == 2
    assert out["version"] == 4
