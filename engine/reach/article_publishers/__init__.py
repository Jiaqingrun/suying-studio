"""Article publishers — browser adapters with dry-run and human handoff."""

from __future__ import annotations

from typing import Any, Protocol

from engine.content.platforms import get_platform


class ArticlePublisher(Protocol):
    platform: str

    def publish(
        self,
        *,
        title: str,
        body_md: str,
        payload: dict[str, Any],
        profile_name: str = "",
        dry_run: bool = True,
        resume: bool = False,
    ) -> dict[str, Any]: ...


class BaseArticlePublisher:
    platform = "generic"

    def validate_payload(self, title: str, body_md: str, payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not (title or "").strip():
            errors.append("标题为空")
        if len((body_md or "").strip()) < 20:
            errors.append("正文过短")
        return errors

    def publish(
        self,
        *,
        title: str,
        body_md: str,
        payload: dict[str, Any],
        profile_name: str = "",
        dry_run: bool = True,
        resume: bool = False,
    ) -> dict[str, Any]:
        spec = get_platform(self.platform) if self.platform != "generic" else {}
        errors = self.validate_payload(title, body_md, payload)
        if errors:
            return {
                "outcome": "failed",
                "phase": "validate",
                "detail": "; ".join(errors),
                "evidence": {},
            }
        if dry_run:
            return {
                "outcome": "published",
                "phase": "dry_run",
                "detail": "dry_run 校验通过，未启动浏览器",
                "published_url": "",
                "review_id": "",
                "evidence": {
                    "title_len": len(title),
                    "body_len": len(body_md),
                    "profile_name": profile_name,
                    "resume": resume,
                    "transport": (payload or {}).get("transport") or spec.get("transport"),
                },
            }
        # Live browser path: stop for human unless a calibrated CDP adapter exists.
        resume_url = str(spec.get("publish_url") or "")
        return {
            "outcome": "need_human",
            "phase": "open_editor",
            "detail": "请在可见 Chrome 完成登录/验证或确认编辑器后点继续；当前适配器未做全自动点发",
            "resume_url": resume_url,
            "evidence": {"adapter": spec.get("adapter"), "profile_name": profile_name},
        }


class WebsitePublisher(BaseArticlePublisher):
    platform = "website"


class BaijiahaoPublisher(BaseArticlePublisher):
    platform = "baijiahao"

    def validate_payload(self, title: str, body_md: str, payload: dict[str, Any]) -> list[str]:
        errors = super().validate_payload(title, body_md, payload)
        if len(title) < 8:
            errors.append("百家号标题建议 8–40 字")
        if len(title) > 40:
            errors.append("百家号标题超过 40 字")
        return errors


class ToutiaoPublisher(BaseArticlePublisher):
    platform = "toutiao"


class WechatMpPublisher(BaseArticlePublisher):
    platform = "wechat_mp"


class ZhihuPublisher(BaseArticlePublisher):
    platform = "zhihu"


class SearchSubmitPublisher(BaseArticlePublisher):
    """URL submission helpers (Baidu / 360) — dry_run only stores intent."""

    platform = "baidu_ziyuan"

    def publish(
        self,
        *,
        title: str,
        body_md: str,
        payload: dict[str, Any],
        profile_name: str = "",
        dry_run: bool = True,
        resume: bool = False,
    ) -> dict[str, Any]:
        urls = list((payload or {}).get("urls") or [])
        if not urls and (payload or {}).get("canonical_url"):
            urls = [str(payload.get("canonical_url"))]
        if dry_run:
            return {
                "outcome": "published",
                "phase": "search_submit_dry_run",
                "detail": f"将提交 {len(urls)} 条 URL（dry_run）",
                "published_url": urls[0] if urls else "",
                "evidence": {"urls": urls[:20], "platform": self.platform},
            }
        if not urls:
            return {
                "outcome": "failed",
                "phase": "search_submit",
                "detail": "缺少待提交 URL；请在站点资料中配置 canonical/sitemap",
                "evidence": {},
            }
        # Live token-based push requires secret_ref — stop for human config.
        return {
            "outcome": "need_human",
            "phase": "search_submit",
            "detail": "请配置站长平台 token（钥匙串 secret_ref）后继续；不在库内存明文",
            "resume_url": "https://ziyuan.baidu.com/" if self.platform == "baidu_ziyuan" else "https://zhanzhang.so.com/",
            "evidence": {"urls": urls[:20]},
        }


class So360Publisher(SearchSubmitPublisher):
    platform = "so_360"


class HumanOnlyPublisher(BaseArticlePublisher):
    def __init__(self, platform: str = "baike") -> None:
        self.platform = platform

    def publish(
        self,
        *,
        title: str,
        body_md: str,
        payload: dict[str, Any],
        profile_name: str = "",
        dry_run: bool = True,
        resume: bool = False,
    ) -> dict[str, Any]:
        try:
            url = str(get_platform(self.platform).get("publish_url") or "")
        except ValueError:
            url = ""
        return {
            "outcome": "need_human",
            "phase": "human_only",
            "detail": "该平台仅人工；已生成证据包/草稿，请打开官方入口",
            "resume_url": url or "https://baike.baidu.com/",
            "evidence": {"title": title[:80], "platform": self.platform},
        }


_PUBLISHERS: dict[str, ArticlePublisher] = {
    "website": WebsitePublisher(),
    "baijiahao": BaijiahaoPublisher(),
    "toutiao": ToutiaoPublisher(),
    "wechat_mp": WechatMpPublisher(),
    "zhihu": ZhihuPublisher(),
    "baidu_ziyuan": SearchSubmitPublisher(),
    "so_360": So360Publisher(),
    "baike": HumanOnlyPublisher("baike"),
    "cnblogs": HumanOnlyPublisher("cnblogs"),
}


def publisher_for(platform: str) -> ArticlePublisher:
    key = (platform or "").strip().lower()
    if key in _PUBLISHERS:
        return _PUBLISHERS[key]
    # experimental platforms share generic handoff
    pub = BaseArticlePublisher()
    pub.platform = key or "generic"  # type: ignore[attr-defined]
    return pub
