from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from astrbot_plugin_beszel.core.analysis import (
    DEFAULT_ANALYSIS_PROMPT,
    DIAGNOSIS_PREFIX,
    extract_analysis_context,
    format_analysis_reply,
)
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryPoint,
    ContainerStats,
    HistoryRange,
    SystemDetails,
    SystemDetailView,
    SystemHistoryPoint,
    SystemHistoryView,
    SystemMetrics,
    SystemSummary,
)
from astrbot_plugin_beszel.core.webhook.models import (
    NormalizedNotification,
    NotificationSource,
)


def test_default_analysis_prompt_content() -> None:
    assert "你是一名谨慎的 SRE/Linux 运维诊断助手" in DEFAULT_ANALYSIS_PROMPT
    assert "只依据已提供的数据，区分观测事实与原因推测" in DEFAULT_ANALYSIS_PROMPT
    assert "百分比读数之差使用“百分点”" in DEFAULT_ANALYSIS_PROMPT
    assert "目标篇幅为 150~250 字，总长不超过 250 字" in DEFAULT_ANALYSIS_PROMPT
    assert "可能原因：" in DEFAULT_ANALYSIS_PROMPT
    assert "优先排查：" in DEFAULT_ANALYSIS_PROMPT


def test_format_analysis_reply_boundaries() -> None:
    assert format_analysis_reply(None) == ""
    assert format_analysis_reply("") == ""
    assert format_analysis_reply("   ") == ""

    normal_text = "这是测试诊断结果，建议排查 Nginx 进程。"
    assert format_analysis_reply(normal_text) == f"{DIAGNOSIS_PREFIX}{normal_text}"

    long_text = "A" * 300
    formatted = format_analysis_reply(long_text)
    assert formatted.startswith(DIAGNOSIS_PREFIX)
    # The body text after prefix should be 249 chars + "…"
    body = formatted[len(DIAGNOSIS_PREFIX) :]
    assert len(body) == 250
    assert body.endswith("…")


def test_extract_analysis_context_full_flow(analysis_data) -> None:
    t_ref = datetime.fromisoformat(analysis_data["reference_time"])

    summary = SystemSummary.model_validate(analysis_data["system_summary"])
    details = SystemDetails.model_validate(analysis_data["system_details"])
    metrics = SystemMetrics.model_validate(analysis_data["detail_metrics"])

    history_points = [
        SystemHistoryPoint.model_validate(p) for p in analysis_data["history_points"]
    ]
    container_points = [
        ContainerHistoryPoint.model_validate(p)
        for p in analysis_data["container_history_points"]
    ]

    detail_view = SystemDetailView(
        summary=summary,
        details=details,
        metrics=metrics,
        containers=[],
    )

    history_view = SystemHistoryView(
        summary=summary,
        range=HistoryRange.ONE_HOUR,
        points=history_points,
        container_points=container_points,
        details=details,
    )

    notification = NormalizedNotification(
        request_id="test-req-01",
        source=NotificationSource.BESZEL,
        title="Atlas Gateway CPU threshold exceeded",
        message="CPU exceeded 90% threshold. Open /system/pubatlas0000001",
        send_analysis=True,
    )

    user_json = extract_analysis_context(
        notification=notification,
        detail=detail_view,
        history=history_view,
        reference_time=t_ref,
    )

    assert user_json is not None
    data = json.loads(user_json)

    # 1. Alert fields
    assert data["alert"]["source"] == "beszel"
    assert data["alert"]["title"] == "Atlas Gateway CPU threshold exceeded"

    # 2. Node & Hardware
    assert data["node"]["id"] == "pubatlas0000001"
    assert data["node"]["name"] == "Atlas Gateway"
    assert data["node"]["status"] == "up"
    assert data["node"]["hardware"]["os"] == "Linux"
    assert data["node"]["hardware"]["cores"] == 4
    assert data["node"]["hardware"]["memory_gib"] == 16.0

    # 3. CPU Metrics
    cpu = data["metrics"]["cpu"]
    # Baseline points: 00:10 (12.0), 00:20 (15.0), 00:30 (0.0), 00:40 (13.0) -> mean = 10.0
    assert cpu["baseline_pct"] == 10.0
    assert cpu["baseline_samples"] == 4
    # Recent points: 00:50 (90.0), 00:55 (95.0) -> peak = 95.0
    assert cpu["recent_peak_pct"] == 95.0
    assert cpu["recent_samples"] == 2
    assert cpu["hour_peak_pct"] == 95.0
    assert cpu["delta_pp"] == 85.0
    # Latest from snapshot
    assert cpu["latest_pct"] == 92.5
    assert cpu["latest_source"] == "snapshot"

    # 4. Containers
    containers = data["containers"]
    assert len(containers) == 3
    # c_zero with 0 cpu and 0 mem should be excluded
    names = [c["name"] for c in containers]
    assert "c_zero" not in names
    # c_high_cpu and c_high_mem both have score 1.0
    assert containers[0]["name"] == "c_high_cpu"
    assert containers[0]["score"] == 1.0
    assert containers[0]["hour_cpu_peak_pct"] == 95.0
    assert containers[1]["name"] == "c_high_mem"
    assert containers[1]["score"] == 1.0
    assert containers[1]["hour_mem_peak_mib"] == 4000.0
    # Tied containers: c_tied_a before c_tied_b
    assert containers[2]["name"] == "c_tied_a"


def test_extract_analysis_context_insufficient_baseline() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    summary = SystemSummary(id="sys-1", name="Node 1", status="up")
    # Only 2 points in baseline window
    history_points = [
        SystemHistoryPoint(
            created=datetime(2026, 9, 14, 0, 10, 0, tzinfo=UTC),
            stats={"cpu": 15.0},
        ),
        SystemHistoryPoint(
            created=datetime(2026, 9, 14, 0, 20, 0, tzinfo=UTC),
            stats={"cpu": 25.0},
        ),
        SystemHistoryPoint(
            created=datetime(2026, 9, 14, 0, 50, 0, tzinfo=UTC),
            stats={"cpu": 80.0},
        ),
    ]

    history_view = SystemHistoryView(
        summary=summary,
        range=HistoryRange.ONE_HOUR,
        points=history_points,
    )
    notification = NormalizedNotification(
        request_id="req-2",
        source=NotificationSource.BESZEL,
        title="Alert",
        message="CPU alert",
        send_analysis=True,
    )

    user_json = extract_analysis_context(
        notification=notification,
        detail=None,
        history=history_view,
        reference_time=t_ref,
    )

    assert user_json is not None
    data = json.loads(user_json)
    cpu = data["metrics"]["cpu"]
    assert cpu["baseline_samples"] == 2
    assert cpu["baseline_pct"] is None
    assert cpu["delta_pp"] is None
    assert data["quality"]["insufficient_baseline"] is True
    # Latest falls back to history sample
    assert cpu["latest_pct"] == 80.0
    assert cpu["latest_source"] == "history_sample"


def test_extract_analysis_context_snapshot_only_containers() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    summary = SystemSummary(id="sys-1", name="Node 1", status="up")
    detail_view = SystemDetailView(
        summary=summary,
        metrics=SystemMetrics(stats={"cpu": 50.0, "mp": 60.0}),
        containers=[
            ContainerStats(n="redis", c=10.0, m=256.0),
            ContainerStats(n="mysql", c=40.0, m=1024.0),
        ],
    )
    notification = NormalizedNotification(
        request_id="req-3",
        source=NotificationSource.BESZEL,
        title="Alert",
        message="High usage",
        send_analysis=True,
    )

    user_json = extract_analysis_context(
        notification=notification,
        detail=detail_view,
        history=None,
        reference_time=t_ref,
    )

    assert user_json is not None
    data = json.loads(user_json)
    containers = data["containers"]
    assert len(containers) == 2
    assert containers[0]["name"] == "mysql"
    assert containers[0]["source"] == "snapshot"
    assert containers[1]["name"] == "redis"
    assert containers[1]["source"] == "snapshot"
    # Snapshot values must not be presented as hourly peaks or baselines.
    for container in containers:
        assert "hour_cpu_peak_pct" not in container
        assert "hour_mem_peak_mib" not in container
        assert "baseline_cpu_pct" not in container


def test_extract_analysis_context_no_valid_metrics_returns_none() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    summary = SystemSummary(id="sys-1", name="Node 1", status="up")
    detail_view = SystemDetailView(summary=summary, metrics=None, containers=[])
    notification = NormalizedNotification(
        request_id="req-4",
        source=NotificationSource.BESZEL,
        title="Alert",
        message="Only title",
        send_analysis=True,
    )

    user_json = extract_analysis_context(
        notification=notification,
        detail=detail_view,
        history=None,
        reference_time=t_ref,
    )
    assert user_json is None


def test_extract_analysis_context_progressive_compression() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    details = SystemDetails(
        os="Very Long Linux Operating System Distribution Name 1234567890",
        cores=64,
        memory=1099511627776,
    )
    # Use multi-byte characters to push JSON size over 1800 bytes initially
    containers = [
        ContainerStats(
            n=f"非常长的主机容器服务名称序号_{i}_测试服务" * 2, c=10.0 + i, m=500.0
        )
        for i in range(10)
    ]
    detail_view = SystemDetailView(
        summary=SystemSummary(id="sys-1", name="测试监控节点名称_" * 4, status="up"),
        details=details,
        metrics=SystemMetrics(stats={"cpu": 80.0, "mp": 70.0, "dp": 50.0}),
        containers=containers,
    )
    notification = NormalizedNotification(
        request_id="req-5",
        source=NotificationSource.BESZEL,
        title="测试告警标题超长前缀_" * 5,
        message="这是非常长的一段模拟业务告警详细描述信息内容，用于测试字符编码与压缩机制。"
        * 5,
        send_analysis=True,
    )

    user_json = extract_analysis_context(
        notification=notification,
        detail=detail_view,
        history=None,
        reference_time=t_ref,
    )

    assert user_json is not None
    assert len(user_json.encode("utf-8")) <= 1800
    data = json.loads(user_json)
    # Must have compressed (truncated = True)
    assert data["truncated"] is True


def test_extract_analysis_context_disk_metrics() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    summary = SystemSummary(id="sys-disk", name="Disk Node", status="up")
    detail_view = SystemDetailView(
        summary=summary,
        metrics=SystemMetrics(stats={"cpu": 10.0, "dp": 85.5, "du": 85.5, "d": 100.0}),
        containers=[],
    )
    notification = NormalizedNotification(
        request_id="req-disk",
        source=NotificationSource.BESZEL,
        title="Disk Alert",
        message="High disk usage",
        send_analysis=True,
    )
    user_json = extract_analysis_context(
        notification=notification,
        detail=detail_view,
        history=None,
        reference_time=t_ref,
    )
    assert user_json is not None
    data = json.loads(user_json)
    disk = data["metrics"]["disk"]
    assert disk["latest_pct"] == 85.5
    assert disk["latest_source"] == "snapshot"
    assert disk["latest_used_gib"] == 85.5
    assert disk["latest_total_gib"] == 100.0


def _edge_notification(**overrides) -> NormalizedNotification:
    values = {
        "request_id": "req-edge",
        "source": NotificationSource.BESZEL,
        "title": "CPU alert",
        "message": "CPU usage exceeded the threshold",
        "send_analysis": True,
        "history_system_id": "sys-1",
    }
    values.update(overrides)
    return NormalizedNotification(**values)


def test_extract_analysis_context_reports_regular_sparse_samples_as_gaps() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    history = SystemHistoryView(
        summary=SystemSummary(id="sys-1", name="Node 1", status="up"),
        range=HistoryRange.ONE_HOUR,
        points=[
            SystemHistoryPoint(
                created=t_ref - timedelta(minutes=offset), stats={"cpu": 10.0}
            )
            for offset in (50, 40, 30, 20, 10, 0)
        ],
    )

    user_json = extract_analysis_context(_edge_notification(), None, history, t_ref)

    assert user_json is not None
    # Ten-minute spacing exceeds 1.5x the one-minute interval of the 1h range.
    assert json.loads(user_json)["quality"]["gaps_detected"] is True


def test_extract_analysis_context_keeps_reference_time_and_sample_age() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    history = SystemHistoryView(
        summary=SystemSummary(id="sys-1", name="Node 1", status="down"),
        range=HistoryRange.ONE_HOUR,
        points=[
            SystemHistoryPoint(
                created=t_ref - timedelta(minutes=40), stats={"cpu": 80.0}
            )
        ],
    )

    user_json = extract_analysis_context(_edge_notification(), None, history, t_ref)

    assert user_json is not None
    time_info = json.loads(user_json)["time"]
    assert time_info["reference_time"] == "2026-09-14T01:00:00Z"
    assert time_info["latest_sample_time"] == "2026-09-14T00:20:00Z"
    assert time_info["latest_sample_age_seconds"] == 2400


def test_extract_analysis_context_marks_initial_field_truncation() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    detail = SystemDetailView(
        summary=SystemSummary(id="sys-1", name="Node 1", status="up"),
        metrics=SystemMetrics(stats={"cpu": 50.0}),
    )

    short_json = extract_analysis_context(_edge_notification(), detail, None, t_ref)
    long_json = extract_analysis_context(
        _edge_notification(message="x" * 300), detail, None, t_ref
    )

    assert short_json is not None
    assert long_json is not None
    assert json.loads(short_json)["truncated"] is False
    long_data = json.loads(long_json)
    assert long_data["truncated"] is True
    assert len(long_data["alert"]["message"]) == 240
