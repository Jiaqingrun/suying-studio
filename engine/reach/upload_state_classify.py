"""Pure upload-page text classifier (no browser).

Mirrors the fail-closed rules inside ``cdp_publish.wait_upload_ready`` probe JS:
never treat bare「上传中」or help-text「验证」as hard signals.
"""

from __future__ import annotations

import re
from typing import Any


_NEED_LOGIN = re.compile(
    r"扫码登录|手机号登录|短信登录|登录后免费|发送验证码|收不到验证码"
)
_UPLOAD_FAILED = re.compile(r"上传失败|网络错误，请稍后|上传出错")
_PROGRESS = re.compile(r"取消上传|转码中|正在上传|视频处理中|上传进度|封面生成中")
_NEED_HUMAN = re.compile(
    r"接收短信验证码|短信验证码|为确保是本人操作|请完成安全验证|请拖动滑块|向右拖动滑块"
)
_PERCENT_UPLOAD = re.compile(r"\d+%\s*(取消上传|上传)")
_EMPTY_UPLOAD = re.compile(r"拖拽视频|点击上传|上传视频")
_FORM_GENERIC = re.compile(r"发布|作品描述|标题|封面|编辑")
_HELP_UPLOADING_ONLY = re.compile(r"如作品还在上传中，请勿关闭页面")


def classify_upload_page_text(
    text: str,
    *,
    has_video: bool = False,
    has_delete: bool = False,
    href: str = "",
) -> dict[str, Any]:
    """Classify creator upload/edit page from visible text + cheap DOM flags.

    Returns ``{"state": ...}`` compatible with wait_upload_ready probe states.
    """
    t = str(text or "")
    href_l = str(href or "")

    if _NEED_LOGIN.search(t) or re.search(r"/login\b|redirectReason=401", href_l, re.I):
        return {"state": "need_login"}
    if _UPLOAD_FAILED.search(t):
        return {"state": "upload_failed"}

    # Help copy alone must NOT keep the page in uploading.
    help_only = bool(_HELP_UPLOADING_ONLY.search(t)) and not _PROGRESS.search(t)

    if _PROGRESS.search(t) or re.search(r"\b0%\b", t):
        return {"state": "uploading", "reason": "progress"}
    if has_video and "封面预览" in t and "生成中" in t and not has_delete:
        return {"state": "uploading", "reason": "channels_cover_generating"}
    if has_video and (
        "封面预览" in t
        or "个人主页和分享卡片" in t
        or "删除" in t
        or "视频描述" in t
        or "添加描述" in t
        or has_delete
    ):
        return {"state": "form", "reason": "channels_preview"}
    if "设置封面" in t and "作品描述" in t and has_video and "上传失败" not in t:
        return {"state": "form", "reason": "xhs_video"}
    if "设置封面" in t and "重新上传" in t and not _UPLOAD_FAILED.search(t):
        return {"state": "form", "reason": "xhs_chip"}
    if has_video and _FORM_GENERIC.search(t):
        return {"state": "form", "reason": "generic_preview"}
    if _NEED_HUMAN.search(t):
        return {"state": "need_human"}
    if _PERCENT_UPLOAD.search(t):
        return {"state": "uploading", "reason": "percent"}
    if _EMPTY_UPLOAD.search(t) and not has_video:
        return {"state": "upload"}
    if help_only and has_video:
        return {"state": "form", "reason": "help_upload_copy_ignored"}
    return {
        "state": "waiting",
        "hasVideo": has_video,
        "hasDelete": has_delete,
        "textLen": len(t),
    }


def bare_uploading_help_is_not_progress(text: str) -> bool:
    """Douyin keeps「如作品还在上传中…」after the file is ready — not progress."""
    t = str(text or "")
    if not _HELP_UPLOADING_ONLY.search(t):
        return False
    return not bool(_PROGRESS.search(t))
