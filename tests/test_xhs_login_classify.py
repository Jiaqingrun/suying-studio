"""XHS login vs captcha classification — avoid help-text false stops."""

from __future__ import annotations

import unittest

from engine.reach.vision_reach import (
    STATE_FORM,
    STATE_NEED_HUMAN,
    STATE_NEED_LOGIN,
    classify_publish_state,
)


class XhsLoginClassifyTests(unittest.TestCase):
    def test_xhs_sms_login_page_is_need_login_not_captcha(self) -> None:
        text = (
            "创作服务平台\n解锁创作者专属功能\n短信登录\n发送验证码\n收不到验证码？\n登 录\n"
            "登录即同意用户协议和隐私政策"
        )
        url = (
            "https://creator.xiaohongshu.com/login?source=&redirectReason=401"
            "&lastUrl=%252Fpublish%252Fpublish%253Ffrom%253Dwebsite%2526target%253Dvideo"
        )
        self.assertEqual(
            classify_publish_state(url=url, text=text),
            STATE_NEED_LOGIN,
        )

    def test_real_slider_is_need_human(self) -> None:
        self.assertEqual(
            classify_publish_state(
                url="https://creator.xiaohongshu.com/publish/publish",
                text="请拖动滑块完成安全验证",
            ),
            STATE_NEED_HUMAN,
        )

    def test_publish_form_not_blocked_by_bare_verify_word(self) -> None:
        # Help copy may mention 验证; must not hard-stop when form is ready.
        text = "设置封面\n作品描述\n发布笔记\n内容安全验证说明请参阅帮助中心"
        state = classify_publish_state(
            url="https://creator.xiaohongshu.com/publish/publish?from=website&target=video",
            text=text,
        )
        self.assertNotEqual(state, STATE_NEED_HUMAN)
        self.assertNotEqual(state, STATE_NEED_LOGIN)
        self.assertEqual(state, STATE_FORM)

    def test_pre_redirect_publish_url_with_login_body_is_login(self) -> None:
        # Race: launch URL still /publish but body already SMS login wall.
        state = classify_publish_state(
            url="https://creator.xiaohongshu.com/publish/publish?from=website&target=video",
            text="短信登录\n发送验证码\n收不到验证码？\n登录",
        )
        self.assertEqual(state, STATE_NEED_LOGIN)


if __name__ == "__main__":
    unittest.main()
