"""Hook pool + role query variants. Hooks load from industry pack (not hardcoded brand copy)."""

from __future__ import annotations

from engine.catalog.industry_pack import hooks_for_pack

# Role queries are product capability (slot semantics), not customer copy.
ROLE_QUERIES = {
    "hook": "开场 吸引注意 动作强 主体清晰 {hook} {keyword}",
    "body": "过程 细节 特写 场景 {theme} {keyword}",
    "cta": "收尾 联系 服务承诺 收束 {brand} {keyword}",
}


def default_hooks(pack_id: str | None = None) -> list[str]:
    return hooks_for_pack(pack_id)


# Back-compat alias — neutral blank pack (not a paying customer's copy)
DEFAULT_HOOKS = default_hooks("_blank")


def pick_hook(rng, hooks: list[str] | None = None, *, exclude: set[str] | None = None, pack_id: str | None = None) -> str:
    pool = list(hooks or default_hooks(pack_id))
    if exclude:
        filtered = [h for h in pool if h not in exclude]
        if filtered:
            pool = filtered
    if not pool:
        pool = ["今日推荐"]
    return rng.choice(pool)


def query_for_role(
    role: str | None,
    *,
    theme: str,
    keyword: str,
    hook: str,
    brand: str,
    fallback: str,
) -> str:
    tpl = ROLE_QUERIES.get(role or "", "")
    if not tpl:
        return fallback
    return tpl.format(theme=theme, keyword=keyword, hook=hook, brand=brand)
