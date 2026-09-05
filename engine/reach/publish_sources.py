"""Three compliant content sources for scheduled/manual publish batches."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import (
    Customer,
    Job,
    ReachPublishReservation,
    ReachPublishSchedule,
    ReachPublishTrigger,
    ReachQueueItem,
    RenderOutput,
    ReviewItem,
)
from engine.reach.queue import enqueue


def has_verified_ready_gate(qc_json: dict[str, Any] | None) -> bool:
    """Fail closed: publication candidates need explicit READY_GATE evidence."""
    gate = (qc_json or {}).get("ready_gate") or {}
    return gate.get("ok") is True


def has_current_approved_review(session: Session, output_id: int) -> bool:
    """Publication requires an explicit, unique current approved decision."""
    return (
        session.scalar(
            select(ReviewItem.id).where(
                ReviewItem.render_output_id == output_id,
                ReviewItem.is_current.is_(True),
                ReviewItem.status == "approved",
            )
        )
        is not None
    )


def allocate_occurrences(
    accounts: list[dict[str, Any]],
    *,
    total_count: int,
    allocation_mode: str,
    manual_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Pure, stable occurrence allocation across selected owned accounts."""
    if total_count < 1:
        raise ValueError("总发布次数至少为 1")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in accounts:
        platform = str(raw.get("platform") or "").strip().lower()
        profile = str(raw.get("chrome_profile") or "").strip()
        if not platform or not profile:
            raise ValueError("每个账号必须包含 platform 与 chrome_profile")
        key = f"{platform}:{profile}"
        if key in seen:
            raise ValueError(f"账号重复: {key}")
        seen.add(key)
        normalized.append({"key": key, "platform": platform, "chrome_profile": profile})
    if not normalized:
        raise ValueError("请至少选择一个本人账号")
    counts: dict[str, int] = {}
    mode = allocation_mode.strip().lower()
    if mode == "auto_even":
        base, remainder = divmod(total_count, len(normalized))
        counts = {
            account["key"]: base + (1 if index < remainder else 0)
            for index, account in enumerate(normalized)
        }
    elif mode == "manual":
        requested = manual_counts or {}
        counts = {account["key"]: int(requested.get(account["key"], 0)) for account in normalized}
        if any(value < 0 for value in counts.values()) or sum(counts.values()) != total_count:
            raise ValueError("手动分配次数必须非负且精确等于总发布次数")
    else:
        raise ValueError("allocation_mode 须为 auto_even | manual")
    occurrences: list[dict[str, Any]] = []
    ordinal = 0
    for account in normalized:
        for account_ordinal in range(counts[account["key"]]):
            occurrences.append(
                {
                    "ordinal": ordinal,
                    "account_ordinal": account_ordinal,
                    "account_key": account["key"],
                    "platform": account["platform"],
                    "chrome_profile": account["chrome_profile"],
                    "allocation_reason": mode,
                }
            )
            ordinal += 1
    return occurrences


def keep_eligible_account_occurrences(
    occurrences: list[dict[str, Any]],
    *,
    eligible_platforms: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop unavailable account slots without reallocating them to another account.

    Selection is the user contract. If one selected platform lacks complete
    publish assets, its own occurrences are skipped; another selected account
    must never silently receive those occurrences.
    """
    usable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for occurrence in occurrences:
        platform = str(occurrence.get("platform") or "").strip().lower()
        if platform in eligible_platforms:
            usable.append(occurrence)
        else:
            skipped.append(
                {
                    **occurrence,
                    "skip_reason": "missing_publish_assets",
                }
            )
    return usable, skipped


def assign_content_to_occurrences(
    occurrences: list[dict[str, Any]],
    *,
    content_mode: str,
    candidate_outputs: list[dict[str, Any]],
    selected_output_ids: list[int] | None = None,
    seed: int,
) -> list[dict[str, Any]]:
    """Pure frozen content assignment for all immediate occurrences."""
    by_id = {int(item["output_id"]): item for item in candidate_outputs}
    selected = [int(value) for value in (selected_output_ids or [])]
    mode = content_mode.strip().lower()
    sequence: list[dict[str, Any]]
    if mode == "random_unique":
        sequence = list(by_id.values())
        random.Random(seed).shuffle(sequence)
        assigned: list[dict[str, Any]] = []
        used: set[int] = set()
        for occurrence in occurrences:
            platform = str(occurrence.get("platform") or "").strip().lower()
            picked = next(
                (
                    item
                    for item in sequence
                    if int(item["output_id"]) not in used
                    and (
                        not item.get("eligible_platforms")
                        or platform in item["eligible_platforms"]
                    )
                ),
                None,
            )
            if picked is None:
                available = sum(
                    1
                    for item in sequence
                    if not item.get("eligible_platforms")
                    or platform in item["eligible_platforms"]
                )
                raise ValueError(
                    f"{platform} 随机不重复需要更多视频，当前仅 {available} 条合格可用"
                )
            used.add(int(picked["output_id"]))
            assigned.append(picked)
        sequence = assigned
    elif mode == "selected_cycle":
        if not selected:
            raise ValueError("统一若干视频至少选择 1 条")
        missing = [value for value in selected if value not in by_id]
        if missing:
            raise ValueError(f"所选成片不可用或不在即时候选池: {missing}")
        source = [by_id[value] for value in selected]
        sequence = [source[index % len(source)] for index in range(len(occurrences))]
    elif mode == "single":
        if len(selected) != 1 or selected[0] not in by_id:
            raise ValueError("单个视频模式必须选择 1 条可用成片")
        sequence = [by_id[selected[0]]] * len(occurrences)
    else:
        raise ValueError("content_mode 须为 random_unique | selected_cycle | single")
    for index, occurrence in enumerate(occurrences):
        eligible = sequence[index].get("eligible_platforms") or []
        platform = str(occurrence.get("platform") or "").strip().lower()
        if eligible and platform not in eligible:
            raise ValueError(f"所选成片不适用于平台 {platform}")
    seen_occurrences: set[tuple[int, str, str]] = set()
    for index, occurrence in enumerate(occurrences):
        occurrence_key = (
            int(sequence[index]["output_id"]),
            str(occurrence.get("platform") or "").strip().lower(),
            str(occurrence.get("chrome_profile") or "").strip(),
        )
        if occurrence_key in seen_occurrences:
            raise ValueError(
                "同一成片不得在同一平台/账号跨 occurrence 重复；"
                "请减少次数或选择更多成片"
            )
        seen_occurrences.add(occurrence_key)
    return [
        {**occurrence, "output": dict(sequence[index])}
        for index, occurrence in enumerate(occurrences)
    ]


def _published_output_ids(session: Session, customer_id: int, platform: str) -> set[int]:
    rows = session.scalars(
        select(ReachQueueItem.output_id).where(
            ReachQueueItem.customer_id == customer_id,
            ReachQueueItem.platform == platform,
            ReachQueueItem.status == "published",
            ReachQueueItem.output_id.is_not(None),
        )
    ).all()
    return {int(x) for x in rows if x}


def _output_spec(out: RenderOutput) -> dict[str, Any] | None:
    from engine.reach.business_scope import VIDEO_PLATFORMS

    statuses = out.platform_asset_status if isinstance(out.platform_asset_status, dict) else {}
    if (
        out.pack_status != "ready"
        or not out.pack_dir
        or any((statuses.get(platform) or {}).get("status") != "ready" for platform in VIDEO_PLATFORMS)
    ):
        return None
    pack_dir = Path(out.pack_dir)
    if not pack_dir.is_dir():
        return None
    out_path = Path(str(out.output_path))
    return {
        "output_id": out.id,
        "video_path": out.output_path,
        "pack_dir": str(pack_dir),
        "title": out_path.stem,
    }


def list_immediate_candidates(
    session: Session,
    *,
    customer_id: int,
    platforms: list[str],
    limit: int = 500,
    allow_published: bool = False,
) -> list[dict[str, Any]]:
    """READY, packed, not published on selected platforms, no active publication group.

    Content reservations are abolished (2026-08-03): the ready pool is shared with
    scheduled publish; occupancy is only via in-flight publication groups.

    ``allow_published=True`` temporarily keeps READY 成片 that already have a
    published queue row on the target platform (user-authorized override).
    """
    from engine.reach.cover_templates import resolve_cover_store_for_settings
    from engine.reach.prefill import load_pack_prefill
    from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

    published_by_platform = {
        platform: _published_output_ids(session, customer_id, platform)
        for platform in set(platforms)
    }
    outputs = session.scalars(
        select(RenderOutput)
        .join(Job, RenderOutput.job_id == Job.id)
        .where(RenderOutput.state == "ready", Job.customer_id == customer_id)
        .order_by(RenderOutput.id.desc())
        .limit(limit)
    ).all()
    result: list[dict[str, Any]] = []
    from engine.reach.publication_lifecycle import output_has_active_group

    for output in outputs:
        if output_has_active_group(
            session, customer_id=customer_id, output_id=output.id
        ):
            continue
        if not has_verified_ready_gate(output.qc_json):
            continue
        if not has_current_approved_review(session, output.id):
            continue
        spec = _output_spec(output)
        if not spec:
            continue
        prefill = load_pack_prefill(Path(spec["pack_dir"]))
        eligible_platforms: list[str] = []
        for platform in set(platforms):
            if (
                not allow_published
                and output.id in published_by_platform[platform]
            ):
                continue
            try:
                copy = (prefill.get("platforms") or {}).get(platform) or {}
                require_publish_assets(
                    platform=platform,
                    pack_dir=Path(spec["pack_dir"]),
                    data_root=resolve_cover_store_for_settings(),
                    title=str(copy.get("title") or spec["title"]),
                    body=str(copy.get("body") or ""),
                )
                eligible_platforms.append(platform)
            except PublishAssetsError:
                continue
        if not eligible_platforms:
            continue
        result.append({**spec, "eligible_platforms": sorted(eligible_platforms)})
    return result


def immediate_asset_block_reasons(
    session: Session,
    *,
    customer_id: int,
    platforms: list[str],
    limit: int = 100,
) -> dict[str, str]:
    """Return concrete fail-closed reasons for platforms with no candidate."""
    from engine.reach.cover_templates import resolve_cover_store_for_settings
    from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

    wanted = {str(platform).strip().lower() for platform in platforms if str(platform).strip()}
    reasons: dict[str, str] = {}
    outputs = session.scalars(
        select(RenderOutput)
        .join(Job, RenderOutput.job_id == Job.id)
        .where(
            Job.customer_id == customer_id,
            RenderOutput.state.in_(("ready", "asset_blocked")),
        )
        .order_by(RenderOutput.id.desc())
        .limit(max(1, min(limit, 500)))
    ).all()
    for output in outputs:
        if output.state == "asset_blocked" or output.pack_status != "ready":
            reason = output.pack_error or f"pack_status={output.pack_status}"
            for platform in wanted:
                reasons.setdefault(platform, f"成片 {output.id}：{reason}")
            continue
        spec = _output_spec(output)
        if not spec:
            continue
        for platform in wanted - reasons.keys():
            try:
                require_publish_assets(
                    platform=platform,
                    pack_dir=spec["pack_dir"],
                    data_root=resolve_cover_store_for_settings(),
                )
            except PublishAssetsError as exc:
                reasons[platform] = f"成片 {output.id}：{exc}"
    for platform in wanted:
        reasons.setdefault(platform, "没有 READY_GATE 通过、当前 approved 且四平台物料齐全的成片")
    return reasons


def pick_eligible_ready_random(
    session: Session,
    *,
    customer_id: int,
    platform: str,
    count: int,
    seed: int | None = None,
    cooldown_hours: int = 24,
    heal_assets: bool = True,
) -> dict[str, Any]:
    """Random ready outputs with publish_pack, excluding already published on platform.

    When short, optionally force-rebuild packs for damaged READY rows before counting.
    """
    del cooldown_hours  # reserved

    def _collect() -> list[RenderOutput]:
        published = _published_output_ids(session, customer_id, platform)
        outputs = session.scalars(
            select(RenderOutput)
            .join(Job, RenderOutput.job_id == Job.id)
            .where(
                RenderOutput.state == "ready",
                Job.customer_id == customer_id,
            )
            .order_by(RenderOutput.id.desc())
            .limit(500)
        ).all()
        eligible: list[RenderOutput] = []
        for out in outputs:
            from engine.reach.publication_lifecycle import output_has_active_group

            if output_has_active_group(
                session, customer_id=customer_id, output_id=out.id
            ):
                continue
            if out.id in published:
                continue
            spec = _output_spec(out)
            if spec is None:
                continue
            if not has_verified_ready_gate(out.qc_json):
                continue
            if not has_current_approved_review(session, out.id):
                continue
            from engine.reach.cover_templates import resolve_cover_store_for_settings
            from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

            try:
                require_publish_assets(
                    platform=platform,
                    pack_dir=spec["pack_dir"],
                    data_root=resolve_cover_store_for_settings(),
                )
            except PublishAssetsError:
                continue
            eligible.append(out)
        return eligible

    eligible = _collect()
    healed = 0
    if heal_assets and len(eligible) < max(1, count):
        from engine.reach.publish_asset_heal import heal_ready_pool_for_platform

        healed = heal_ready_pool_for_platform(
            session,
            customer_id=customer_id,
            platform=platform,
            limit=max(12, count * 3),
        )
        if healed:
            session.flush()
            eligible = _collect()
    actual_seed = (
        int(seed) if seed is not None else random.SystemRandom().randrange(1, 1 << 30)
    )
    rng = random.Random(actual_seed)
    rng.shuffle(eligible)
    picked = eligible[: max(1, count)]
    return {
        "ok": bool(picked) and len(picked) >= max(1, count),
        "seed": actual_seed,
        "algorithm": "python_random_shuffle_v1",
        "outputs": [_output_spec(o) for o in picked if _output_spec(o)],
        "eligible_count": len(eligible),
        "healed_packs": healed,
    }


def reserve_trigger_content(
    session: Session,
    schedule: ReachPublishSchedule,
    trigger: ReachPublishTrigger,
) -> list[ReachPublishReservation]:
    """No-op: content reservations abolished (2026-08-03).

    Kept for call-site compatibility; never writes ``reserved`` rows. Selection
    happens at materialize / immediate start time from the shared ready pool.
    """
    del session, schedule, trigger
    return []


def release_all_content_reservations(session: Session) -> int:
    """Release every leftover content reservation (idempotent policy migration)."""
    from datetime import datetime, timezone

    rows = list(
        session.scalars(
            select(ReachPublishReservation).where(
                ReachPublishReservation.status == "reserved"
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        row.status = "released"
        row.updated_at = now
        assignment = dict(row.assignment_json or {})
        assignment["release_reason"] = "policy_no_reserve"
        row.assignment_json = assignment
    return len(rows)


def materialize_manual_ready(
    session: Session,
    *,
    customer_id: int,
    platform: str,
    queue_ids: list[int],
) -> list[ReachQueueItem]:
    items: list[ReachQueueItem] = []
    for qid in queue_ids:
        row = session.get(ReachQueueItem, int(qid))
        if not row or row.customer_id != customer_id:
            raise ValueError(f"队列项 {qid} 不存在或不属于当前客户")
        if row.platform != platform:
            raise ValueError(f"队列项 {qid} 平台 {row.platform} 与计划 {platform} 不匹配")
        if row.status not in ("queued", "awaiting_human"):
            raise ValueError(f"队列项 {qid} 状态 {row.status} 不可发布")
        if row.output_id is None:
            raise ValueError(f"队列项 {qid} 缺少明确 approved 成片")
        output = session.get(RenderOutput, row.output_id)
        if (
            not output
            or output.state != "ready"
            or not has_verified_ready_gate(output.qc_json)
            or not has_current_approved_review(session, output.id)
        ):
            raise ValueError(f"队列项 {qid} 的成片未明确 approved")
        spec = _output_spec(output)
        if spec is None:
            raise ValueError(
                f"队列项 {qid} 的成片发布物料未就绪："
                f"{output.pack_error or output.pack_status or 'unknown'}"
            )
        from engine.reach.cover_templates import resolve_cover_store_for_settings
        from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

        try:
            require_publish_assets(
                platform=platform,
                pack_dir=spec["pack_dir"],
                data_root=resolve_cover_store_for_settings(),
                title=row.title,
                body=row.body,
            )
        except PublishAssetsError as exc:
            raise ValueError(f"队列项 {qid} 发布物料阻断：{exc}") from exc
        items.append(row)
    return items


def materialize_from_schedule(
    session: Session,
    schedule: ReachPublishSchedule,
    *,
    items_per_trigger: int | None = None,
    trigger: ReachPublishTrigger | None = None,
) -> dict[str, Any]:
    """Resolve queue rows for one schedule trigger."""
    plat = schedule.platform.strip().lower()
    src = schedule.content_source.strip().lower()
    cfg = schedule.source_config_json if isinstance(schedule.source_config_json, dict) else {}
    n = items_per_trigger or schedule.items_per_trigger or 1

    if src == "manual_ready":
        qids = [int(x) for x in (cfg.get("queue_ids") or [])]
        if not qids:
            raise ValueError("manual_ready 须指定 queue_ids")
        rows = materialize_manual_ready(
            session, customer_id=schedule.customer_id, platform=plat, queue_ids=qids[:n]
        )
        return {"source": src, "queue_items": rows, "evidence": {"queue_ids": [r.id for r in rows]}}

    if src in ("eligible_ready_random", "generate_then_publish"):
        from engine.reach.prefill import load_pack_prefill

        seed = cfg.get("seed")
        pick = pick_eligible_ready_random(
            session,
            customer_id=schedule.customer_id,
            platform=plat,
            count=n,
            seed=int(seed) if seed is not None else None,
        )
        outputs = [
            ent
            for ent in (pick.get("outputs") or [])
            if isinstance(ent, dict) and ent.get("output_id") is not None
        ]
        # Enough ready → enqueue now. Shortfall → heal again once, then gap production.
        if pick.get("ok") and len(outputs) >= n:
            rows: list[ReachQueueItem] = []
            for ent in outputs[:n]:
                pack = str(ent["pack_dir"])
                prefill = load_pack_prefill(Path(pack))
                platform_copy = (prefill.get("platforms") or {}).get(plat) or {}
                title = str(platform_copy.get("title") or ent.get("title") or "").strip()
                body = str(platform_copy.get("body") or platform_copy.get("caption") or "").strip()
                from engine.reach.cover_templates import resolve_cover_store_for_settings
                from engine.reach.publish_assets import PublishAssetsError, require_publish_assets
                from engine.reach.publish_asset_heal import repair_output_pack

                # Per-output last-chance pack rebuild when copy/assets look empty.
                if not body or not title:
                    oid = ent.get("output_id")
                    if oid:
                        repaired = repair_output_pack(
                            session,
                            customer_id=schedule.customer_id,
                            output_id=int(oid),
                            force=True,
                        )
                        if repaired.get("ok"):
                            pack = str(repaired.get("pack_dir") or pack)
                            prefill = load_pack_prefill(Path(pack))
                            platform_copy = (prefill.get("platforms") or {}).get(plat) or {}
                            title = str(
                                platform_copy.get("title") or ent.get("title") or ""
                            ).strip()
                            body = str(
                                platform_copy.get("body")
                                or platform_copy.get("caption")
                                or ""
                            ).strip()
                            ent = {**ent, "pack_dir": pack, "title": title}
                if not body:
                    # Drop this candidate; force shortfall → gap production below.
                    rows = []
                    outputs = []
                    break
                try:
                    assets = require_publish_assets(
                        platform=plat,
                        pack_dir=pack,
                        data_root=resolve_cover_store_for_settings(),
                        title=title,
                        body=body,
                    )
                except PublishAssetsError:
                    oid = ent.get("output_id")
                    if oid:
                        repaired = repair_output_pack(
                            session,
                            customer_id=schedule.customer_id,
                            output_id=int(oid),
                            force=True,
                        )
                        if repaired.get("ok"):
                            pack = str(repaired.get("pack_dir") or pack)
                            prefill = load_pack_prefill(Path(pack))
                            platform_copy = (prefill.get("platforms") or {}).get(plat) or {}
                            title = str(
                                platform_copy.get("title") or ""
                            ).strip()
                            body = str(
                                platform_copy.get("body")
                                or platform_copy.get("caption")
                                or ""
                            ).strip()
                            try:
                                assets = require_publish_assets(
                                    platform=plat,
                                    pack_dir=pack,
                                    data_root=resolve_cover_store_for_settings(),
                                    title=title,
                                    body=body,
                                )
                            except PublishAssetsError:
                                rows = []
                                outputs = []
                                break
                        else:
                            rows = []
                            outputs = []
                            break
                    else:
                        rows = []
                        outputs = []
                        break
                title = str(assets.get("title") or title).strip()
                body = str(assets.get("body") or body).strip()
                row = enqueue(
                    session,
                    customer_id=schedule.customer_id,
                    platform=plat,
                    title=title,
                    body=body,
                    video_path=prefill.get("video_path") or ent.get("video_path") or "",
                    pack_dir=pack,
                    output_id=ent.get("output_id"),
                    copy_json={"platform": platform_copy},
                    note=f"{src} ready_first seed={pick.get('seed')}",
                )
                rows.append(row)
            if len(rows) >= n:
                return {
                    "source": src,
                    "queue_items": rows,
                    "evidence": {
                        "seed": pick.get("seed"),
                        "eligible_count": pick.get("eligible_count"),
                        "production_skipped": True,
                        "skip_reason": "ready_sufficient",
                        "healed_packs": pick.get("healed_packs") or 0,
                    },
                }

        from engine.jobs.queue import CreateJobRequest, create_job
        from engine.catalog.db import AutomationOccurrence

        # Idempotent: reuse existing occurrence/job for this trigger key.
        occ_key = (trigger.occurrence_key if trigger else None) or (
            f"schedule:{schedule.id}:adhoc"
        )
        existing_occ = session.scalar(
            select(AutomationOccurrence).where(
                AutomationOccurrence.occurrence_key == occ_key
            )
        )
        if existing_occ and existing_occ.production_job_id:
            existing_job = session.get(Job, int(existing_occ.production_job_id))
            if existing_job and existing_job.status not in {
                "cancelled",
                "failed",
                "completed",
            }:
                return {
                    "source": src,
                    "queue_items": [],
                    "evidence": {
                        "job_id": existing_job.id,
                        "template_name": existing_job.template_name,
                        "eligible_count": pick.get("eligible_count") or 0,
                        "gap_production": True,
                        "idempotent_reuse": True,
                    },
                    "pending_generation": True,
                }

        template = str(cfg.get("template_name") or cfg.get("template") or "").strip()
        if not template:
            templates = cfg.get("template_names") or cfg.get("approved_types") or []
            if isinstance(templates, list) and templates:
                seed_text = str(
                    (trigger.random_seed if trigger else None)
                    or cfg.get("seed")
                    or f"schedule:{schedule.id}"
                )
                rng = random.Random(int.from_bytes(seed_text.encode("utf-8"), "little"))
                template = str(rng.choice(templates))
        if not template:
            template = "default-vertical"
        customer_row = (
            session.get(Customer, schedule.customer_id) if schedule.customer_id else None
        )
        # Stamp trigger key into job snapshot for later reconciliation.
        req = CreateJobRequest(
            template_name=template,
            mode="count",
            target_count=max(1, int(n)),
            theme=str(cfg.get("theme") or "default"),
            category=str(cfg.get("category") or "default"),
            customer_name=(customer_row.name if customer_row else None),
        )
        job = create_job(session, req, customer_id=schedule.customer_id)
        try:
            snap = dict(job.config_snapshot_json or {})
            snap["schedule_occurrence_key"] = occ_key
            if trigger is not None:
                snap["schedule_trigger_id"] = int(trigger.id)
            job.config_snapshot_json = snap
            session.commit()
        except Exception:  # noqa: BLE001
            pass
        return {
            "source": src,
            "queue_items": [],
            "evidence": {
                "job_id": job.id,
                "template_name": template,
                "eligible_count": pick.get("eligible_count") or 0,
                "gap_production": True,
            },
            "pending_generation": True,
        }

    raise ValueError(f"未知内容来源: {src}")
