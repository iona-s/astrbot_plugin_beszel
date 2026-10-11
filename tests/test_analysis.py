from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

from astrbot_plugin_beszel.core.analysis import (
    DEFAULT_ANALYSIS_PROMPT,
    DIAGNOSIS_PREFIX,
    MAX_COMPLETION_CHARS,
    MAX_USER_JSON_BYTES,
    AnalysisRecord,
    add_analysis_history,
    extract_analysis_context,
    format_analysis_reply,
    summarize_analysis,
)
from astrbot_plugin_beszel.core.beszel.models import (
    ContainerHistoryPoint,
    ContainerState,
    ContainerStats,
    HistoryRange,
    ServiceState,
    SmartDeviceState,
    SystemDetails,
    SystemDetailView,
    SystemHealthView,
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
    assert "你是一名谨慎的 SRE/运维诊断助手" in DEFAULT_ANALYSIS_PROMPT
    assert "node.os" in DEFAULT_ANALYSIS_PROMPT
    assert "只依据已提供的数据，区分观测事实与原因推测" in DEFAULT_ANALYSIS_PROMPT
    assert "百分比读数之差使用“百分点”" in DEFAULT_ANALYSIS_PROMPT
    assert "目标篇幅为 150~250 字，总长不超过 250 字" in DEFAULT_ANALYSIS_PROMPT
    assert "可能原因：" in DEFAULT_ANALYSIS_PROMPT
    assert "优先排查：" in DEFAULT_ANALYSIS_PROMPT
    assert "history" in DEFAULT_ANALYSIS_PROMPT
    assert "before_current_seconds" in DEFAULT_ANALYSIS_PROMPT
    assert "peak_15m" in DEFAULT_ANALYSIS_PROMPT
    assert "uptime_seconds" in DEFAULT_ANALYSIS_PROMPT
    assert "cpu_iowait_pct" in DEFAULT_ANALYSIS_PROMPT


def test_format_analysis_reply_boundaries() -> None:
    assert format_analysis_reply(None) == ""
    assert format_analysis_reply("") == ""
    assert format_analysis_reply("   ") == ""

    normal_text = "这是测试诊断结果，建议排查 Nginx 进程。"
    assert format_analysis_reply(normal_text) == f"{DIAGNOSIS_PREFIX}{normal_text}"

    # Slightly long replies are kept; the limit only stops runaway output.
    assert format_analysis_reply("A" * 300) == f"{DIAGNOSIS_PREFIX}{'A' * 300}"

    def body(text: str) -> str:
        return format_analysis_reply(text).removeprefix(DIAGNOSIS_PREFIX)

    assert body("A" * 500) == "A" * (MAX_COMPLETION_CHARS - 1) + "…"
    # Cut after the last full stop or line break in the second half.
    assert body("一二三四五六七八九。" * 50) == "一二三四五六七八九。" * 39 + "…"
    assert body(("x" * 99 + "\n") * 5) == ("x" * 99 + "\n") * 2 + "x" * 99 + "…"
    early_stop = "短句。" + "A" * 500
    assert body(early_stop) == early_stop[: MAX_COMPLETION_CHARS - 1] + "…"


def _fixture_views(
    analysis_data,
) -> tuple[SystemDetailView, SystemHistoryView, SystemHealthView]:
    summary = SystemSummary.model_validate(analysis_data["system_summary"])
    details = SystemDetails.model_validate(analysis_data["system_details"])
    detail = SystemDetailView(
        summary=summary,
        details=details,
        metrics=SystemMetrics.model_validate(analysis_data["detail_metrics"]),
    )
    history = SystemHistoryView(
        summary=summary,
        range=HistoryRange.ONE_HOUR,
        points=[
            SystemHistoryPoint.model_validate(p)
            for p in analysis_data["history_points"]
        ],
        container_points=[
            ContainerHistoryPoint.model_validate(p)
            for p in analysis_data["container_history_points"]
        ],
        details=details,
    )
    return detail, history, SystemHealthView.model_validate(analysis_data["health"])


def test_extract_analysis_context_full_flow(analysis_data) -> None:
    t_ref = datetime.fromisoformat(analysis_data["reference_time"])
    detail, history, health = _fixture_views(analysis_data)
    notification = NormalizedNotification(
        request_id="test-req-01",
        source=NotificationSource.BESZEL,
        title="Atlas Gateway CPU threshold exceeded",
        message="CPU exceeded 90% threshold. Open /system/pubatlas0000001",
        send_analysis=True,
    )

    user_json = extract_analysis_context(notification, detail, history, t_ref, health)

    assert user_json is not None
    assert len(user_json.encode("utf-8")) <= MAX_USER_JSON_BYTES
    data = json.loads(user_json)
    assert data["truncated"] is False
    assert data["alert"] == {
        "source": "beszel",
        "title": "Atlas Gateway CPU threshold exceeded",
        "message": "CPU exceeded 90% threshold. Open /system/pubatlas0000001",
    }
    assert data["time"]["snapshot_time"] == "2026-09-14T00:59:30Z"
    assert data["time"]["snapshot_age_seconds"] == 30
    assert data["node"] == {
        "id": "pubatlas0000001",
        "name": "Atlas Gateway",
        "status": "up",
        "os": "Linux",
        "uptime_seconds": 420,
        "hardware": {"cores": 4, "memory_gib": 16.0, "disk_gib": 100.0},
        "services": {"total": 120, "failed": 1, "failed_names": ["nginx.service"]},
        "smart_failed": [{"name": "/dev/sda", "model": "Fixture SSD"}],
    }

    metrics = data["metrics"]
    # Baseline: 00:10 (12.0 overwrites 10.0), 00:20, 00:30, 00:40 -> mean 10.0.
    # The last 15 minutes hold 00:50 and 00:55; latest comes from the snapshot.
    assert metrics["cpu_pct"] == {
        "latest": 92.5,
        "baseline": 10.0,
        "peak_15m": 95.0,
        "peak_60m": 95.0,
    }
    assert metrics["mem_pct"] == {
        "latest": 85.0,
        "baseline": 25.0,
        "peak_15m": 85.0,
        "peak_60m": 85.0,
    }
    assert metrics["disk_pct"] == {"latest": 50.0}
    assert metrics["load_1m"] == {
        "latest": 6.4,
        "baseline": 0.75,
        "peak_15m": 6.0,
        "peak_60m": 6.0,
        "threads": 8,
    }
    assert metrics["swap_pct"] == {"latest": 25.0}
    assert metrics["cpu_iowait_pct"] == {
        "latest": 12.5,
        "baseline": 1.0,
        "peak_15m": 15.0,
        "peak_60m": 15.0,
    }
    # Steal time is zero throughout, so it is left out.
    assert "cpu_steal_pct" not in metrics
    # Byte rates become MiB/s; await is the slower of read and write. The
    # history points omit the I/O fields, which Beszel does for zero values.
    idle = {"baseline": 0.0, "peak_15m": 0.0, "peak_60m": 0.0}
    assert metrics["disk_read_mib_s"] == {"latest": 2.0, **idle}
    assert metrics["disk_write_mib_s"] == {"latest": 1.0, **idle}
    assert metrics["disk_util_pct"] == {"latest": 65.5, **idle}
    assert metrics["disk_await_ms"] == {"latest": 8.8, **idle}
    assert metrics["net_sent_mib_s"]["latest"] == 0.5
    assert metrics["net_recv_mib_s"]["latest"] == 1.0
    assert metrics["temp_max_c"] == {"latest": 71.5, "sensor": "cpu_thermal"}
    assert data["extra_disks"] == [
        {"name": "data", "used_pct": 91.2},
        {"name": "backup", "used_pct": 40.0},
    ]

    containers = data["containers"]
    # c_zero has no non-zero reading; c_tied_a sorts before c_tied_b.
    assert [c["name"] for c in containers] == ["c_high_cpu", "c_high_mem", "c_tied_a"]
    assert containers[0] == {
        "name": "c_high_cpu",
        "cpu_pct": {"baseline": 12.3, "peak_15m": 95.0, "peak_60m": 95.0},
        "mem_mib": {"baseline": 110, "peak_15m": 200, "peak_60m": 200},
        "status": "Up 3 minutes",
        "health": "unhealthy",
    }
    assert containers[1]["mem_mib"]["peak_60m"] == 4000
    # Healthy and unknown health states are not repeated.
    assert "health" not in containers[1]
    assert "health" not in containers[2]
    assert data["unhealthy_containers"] == ["sidecar"]


def test_extract_analysis_context_omits_zero_and_missing_data() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    detail = SystemDetailView(
        summary=SystemSummary(
            id="sys-1", name="Node 1", status="up", info={"sv": [10, 2]}
        ),
        metrics=SystemMetrics(
            stats={
                "cpu": 0.0,
                "mp": 40.0,
                "la": [0.0, 0.0, 0.0],
                "cpub": [0.0, 0.0, 0.0, 0.0, 100.0],
                "dio": [0, 0],
                "t": {},
            }
        ),
    )
    # Only the container query succeeded.
    health = SystemHealthView(containers=[], failed_services=None)

    user_json = extract_analysis_context(
        _edge_notification(), detail, None, t_ref, health
    )

    assert user_json is not None
    data = json.loads(user_json)
    # CPU, memory, and disk stay even at zero; other all-zero metrics do not.
    assert data["metrics"] == {"cpu_pct": {"latest": 0.0}, "mem_pct": {"latest": 40.0}}
    # The failed count still comes from the system record without names.
    assert data["node"]["services"] == {"total": 10, "failed": 2}
    for key in ("uptime_seconds", "hardware", "smart_failed"):
        assert key not in data["node"]
    assert "extra_disks" not in data
    assert "unhealthy_containers" not in data


def test_extract_analysis_context_reads_omitted_fields_as_zero() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    idle = {"cpu": 5.0, "mp": 30.0, "s": 4.0}
    burst = {
        **idle,
        "dio": [5 * 1048576, 0],
        "dios": [0.0, 0.0, 90.0, 20.0, 0.0, 0.0],
        "su": 2.0,
    }
    summary = SystemSummary(id="sys-1", name="Node 1", status="up")
    history = SystemHistoryView(
        summary=summary,
        range=HistoryRange.ONE_HOUR,
        points=[
            SystemHistoryPoint(
                created=t_ref - timedelta(minutes=minutes),
                stats=burst if minutes == 30 else idle,
            )
            for minutes in range(60, 0, -1)
        ],
    )
    detail = SystemDetailView(summary=summary, metrics=SystemMetrics(stats=idle))

    user_json = extract_analysis_context(_edge_notification(), detail, history, t_ref)

    assert user_json is not None
    metrics = json.loads(user_json)["metrics"]
    # An earlier burst must not be reported as the current reading; the idle
    # minutes count as zeros in the 45-sample baseline.
    for name, baseline, peak in (
        ("disk_read_mib_s", 0.11, 5.0),
        ("disk_util_pct", 2.0, 90.0),
        ("disk_await_ms", 0.4, 20.0),
        ("swap_pct", 1.1, 50.0),
    ):
        assert metrics[name] == {
            "latest": 0.0,
            "baseline": baseline,
            "peak_15m": 0.0,
            "peak_60m": peak,
        }, name


def test_extract_analysis_context_insufficient_baseline() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    history_view = SystemHistoryView(
        summary=SystemSummary(id="sys-1", name="Node 1", status="up"),
        range=HistoryRange.ONE_HOUR,
        # Only two points before the last 15 minutes.
        points=[
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
        ],
    )

    user_json = extract_analysis_context(
        _edge_notification(), None, history_view, t_ref
    )

    assert user_json is not None
    data = json.loads(user_json)
    # Latest falls back to the newest history sample; no baseline from 2 samples.
    assert data["metrics"]["cpu_pct"] == {
        "latest": 80.0,
        "peak_15m": 80.0,
        "peak_60m": 80.0,
    }
    assert data["quality"]["baseline_points"] == 2
    assert data["quality"]["insufficient_baseline"] is True


def test_extract_analysis_context_snapshot_only_containers() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    detail_view = SystemDetailView(
        summary=SystemSummary(id="sys-1", name="Node 1", status="up"),
        metrics=SystemMetrics(stats={"cpu": 50.0, "mp": 60.0}),
        containers=[
            ContainerStats(n="redis", c=10.0, m=256.0),
            ContainerStats(n="mysql", c=40.0, m=1024.0),
        ],
    )

    user_json = extract_analysis_context(_edge_notification(), detail_view, None, t_ref)

    assert user_json is not None
    # Snapshot values are only presented as latest readings.
    assert json.loads(user_json)["containers"] == [
        {"name": "mysql", "cpu_pct": {"latest": 40.0}, "mem_mib": {"latest": 1024}},
        {"name": "redis", "cpu_pct": {"latest": 10.0}, "mem_mib": {"latest": 256}},
    ]


def test_extract_analysis_context_no_valid_metrics_returns_none() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    summary = SystemSummary(id="sys-1", name="Node 1", status="up")
    detail_view = SystemDetailView(summary=summary, metrics=None, containers=[])

    assert (
        extract_analysis_context(_edge_notification(), detail_view, None, t_ref) is None
    )


def _crowded_detail(t_ref: datetime, name_length: int) -> SystemDetailView:
    return SystemDetailView(
        summary=SystemSummary(
            id="sys-1",
            name="测试监控节点名称_" * 4,
            status="up",
            info={"efs": {f"挂载点_{i}" * 4: 50.0 + i for i in range(3)}},
        ),
        details=SystemDetails(os="Windows", cores=64, memory=1099511627776),
        metrics=SystemMetrics(
            created=t_ref - timedelta(seconds=30),
            stats={
                "cpu": 80.0,
                "mp": 70.0,
                "dp": 50.0,
                "la": [3.0, 2.0, 1.0],
                "s": 8.0,
                "su": 2.0,
                "cpub": [50.0, 10.0, 5.0, 2.0, 33.0],
                "dio": [1048576, 2097152],
                "dios": [1.0, 2.0, 30.0, 3.0, 4.0, 5.0],
                "b": [1048576, 2097152],
                "t": {"传感器_" * 6: 60.0},
            },
        ),
        containers=[
            ContainerStats(n=f"容器服务_{i}_" * name_length, c=10.0 + i, m=500.0)
            for i in range(10)
        ],
    )


def _long_alert() -> NormalizedNotification:
    return _edge_notification(
        title="测试告警标题超长前缀_" * 5, message="模拟业务告警详细描述信息内容。" * 16
    )


def test_extract_analysis_context_compression_drops_hardware_and_alert_text_first() -> (
    None
):
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    health = SystemHealthView(
        failed_services=[
            ServiceState(name=f"失败服务名称{i}" * 10, state=2) for i in range(4)
        ]
    )

    user_json = extract_analysis_context(
        _long_alert(), _crowded_detail(t_ref, name_length=8), None, t_ref, health
    )

    assert user_json is not None
    assert len(user_json.encode("utf-8")) <= MAX_USER_JSON_BYTES
    data = json.loads(user_json)
    assert data["truncated"] is True
    assert "hardware" not in data["node"]
    assert data["node"]["os"] == "Windows"
    assert len(data["alert"]["message"]) == 120
    # Metrics and containers survive when the earlier steps are enough.
    assert len(data["metrics"]) == 14
    assert "extra_disks" in data
    assert len(data["containers"]) == 3
    assert data["time"]["snapshot_age_seconds"] == 30


def test_extract_analysis_context_compression_keeps_primary_data_last() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    detail = _crowded_detail(t_ref, name_length=8)
    detail.summary.info["sv"] = [80, 5]
    health = SystemHealthView(
        containers=[
            ContainerState(name=f"异常容器名称{i}" * 10, status="Up 1 minute", health=3)
            for i in range(5)
        ],
        failed_services=[
            ServiceState(name=f"失败服务名称{i}" * 10, state=2) for i in range(5)
        ],
        failed_smart_devices=[
            SmartDeviceState(
                name=f"硬盘设备{i}" * 10, model="型号" * 30, state="FAILED"
            )
            for i in range(3)
        ],
    )

    user_json = extract_analysis_context(_long_alert(), detail, None, t_ref, health)

    assert user_json is not None
    assert len(user_json.encode("utf-8")) <= MAX_USER_JSON_BYTES
    data = json.loads(user_json)
    # Extra disks and secondary I/O metrics go before any container is dropped.
    assert "extra_disks" not in data
    assert set(data["metrics"]) == {
        "cpu_pct",
        "mem_pct",
        "disk_pct",
        "load_1m",
        "swap_pct",
        "cpu_iowait_pct",
    }
    assert len(data["containers"]) < 3
    # Every name list is shortened before whole lists are removed.
    names = [
        data["node"]["name"],
        *(c["name"] for c in data["containers"]),
        *(d["name"] for d in data["node"].get("smart_failed", ())),
        *data.get("unhealthy_containers", ()),
        *data["node"]["services"].get("failed_names", ()),
    ]
    assert all(len(name) <= 32 for name in names)


def test_extract_analysis_context_disk_metrics() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    detail_view = SystemDetailView(
        summary=SystemSummary(id="sys-disk", name="Disk Node", status="up"),
        metrics=SystemMetrics(stats={"cpu": 10.0, "dp": 85.5, "du": 85.5, "d": 100.0}),
        containers=[],
    )

    user_json = extract_analysis_context(_edge_notification(), detail_view, None, t_ref)

    assert user_json is not None
    data = json.loads(user_json)
    assert data["metrics"]["disk_pct"] == {"latest": 85.5}
    assert data["node"]["hardware"] == {"disk_gib": 100.0}


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
    assert "snapshot_time" not in time_info
    assert "snapshot_age_seconds" not in time_info


def test_extract_analysis_context_keeps_stale_snapshot_with_its_age() -> None:
    t_ref = datetime(2026, 9, 14, 1, 0, 0, tzinfo=UTC)
    summary = SystemSummary(id="sys-1", name="Node 1", status="down")
    stale = SystemDetailView(
        summary=summary,
        metrics=SystemMetrics(
            created=datetime(2026, 9, 14, 7, 0, 0, tzinfo=timezone(timedelta(hours=8))),
            stats={"cpu": 50.0},
        ),
        containers=[ContainerStats(n="redis", c=10.0, m=256.0)],
    )
    undated = SystemDetailView(
        summary=summary, metrics=SystemMetrics(stats={"cpu": 50.0})
    )

    stale_json = extract_analysis_context(_edge_notification(), stale, None, t_ref)
    undated_json = extract_analysis_context(_edge_notification(), undated, None, t_ref)

    assert stale_json is not None
    stale_data = json.loads(stale_json)
    assert stale_data["time"]["snapshot_time"] == "2026-09-13T23:00:00Z"
    assert stale_data["time"]["snapshot_age_seconds"] == 7200
    # Stale snapshots stay as dated facts instead of being dropped.
    assert stale_data["metrics"]["cpu_pct"] == {"latest": 50.0}
    assert stale_data["containers"] == [
        {"name": "redis", "cpu_pct": {"latest": 10.0}, "mem_mib": {"latest": 256}}
    ]

    assert undated_json is not None
    undated_time = json.loads(undated_json)["time"]
    assert "snapshot_time" not in undated_time
    assert "snapshot_age_seconds" not in undated_time


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


def _record(t_ref: datetime, hours_ago: float, diagnosis: str) -> AnalysisRecord:
    return AnalysisRecord(
        time=t_ref - timedelta(hours=hours_ago),
        summary={"title": diagnosis, "diagnosis": diagnosis},
    )


def _history(prompt: str) -> list[dict]:
    return json.loads(prompt).get("history", [])


def test_add_analysis_history_uses_all_recent_records_in_order() -> None:
    t_ref = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    user_prompt = json.dumps({"alert": {"title": "now"}, "time": {}})
    records = [
        _record(t_ref, 30, "too old"),
        _record(t_ref, 8, "older"),
        _record(t_ref, 5, "recent 1"),
        _record(t_ref, 0.5, "recent 2"),
    ]

    prompt, count = add_analysis_history(
        user_prompt, records, t_ref, recent_hours=6, context_hours=24
    )

    assert count == 2
    assert _history(prompt) == [
        {
            "before_current_seconds": 5 * 3600,
            "title": "recent 1",
            "diagnosis": "recent 1",
        },
        {"before_current_seconds": 1800, "title": "recent 2", "diagnosis": "recent 2"},
    ]
    # The current alert is kept as is.
    assert {k: v for k, v in json.loads(prompt).items() if k != "history"} == (
        json.loads(user_prompt)
    )


def test_add_analysis_history_falls_back_to_newest_record_in_window() -> None:
    t_ref = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    user_prompt = json.dumps({"alert": {"title": "now"}})
    records = [
        _record(t_ref, 30, "too old"),
        _record(t_ref, 20, "older"),
        _record(t_ref, 18, "newest"),
    ]

    prompt, count = add_analysis_history(
        user_prompt, records, t_ref, recent_hours=6, context_hours=24
    )

    assert count == 1
    assert [
        (h["before_current_seconds"], h["diagnosis"]) for h in _history(prompt)
    ] == [(18 * 3600, "newest")]
    assert add_analysis_history(
        user_prompt, records[:1], t_ref, recent_hours=6, context_hours=24
    ) == (user_prompt, 0)


def test_add_analysis_history_window_limits() -> None:
    t_ref = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    user_prompt = json.dumps({"alert": {"title": "now"}})
    records = [_record(t_ref, 3, "a"), _record(t_ref, 1, "b")]

    def diagnoses(recent_hours: int, context_hours: int, items=records) -> list[str]:
        prompt, _ = add_analysis_history(
            user_prompt,
            items,
            t_ref,
            recent_hours=recent_hours,
            context_hours=context_hours,
        )
        return [h["diagnosis"] for h in _history(prompt)]

    # The recent window is capped at the context window.
    assert diagnoses(48, 2) == ["b"]
    # Without a recent window only the newest record in range is used.
    assert diagnoses(0, 24) == ["b"]
    assert diagnoses(6, 0) == []
    # Records newer than the current alert are ignored.
    assert diagnoses(6, 24, [_record(t_ref, -1, "future")]) == []


def test_summarize_analysis_keeps_key_readings_and_conclusion() -> None:
    t_ref = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    user_prompt = json.dumps(
        {
            "alert": {"source": "beszel", "title": "Connection to Atlas is down"},
            "node": {"id": "s1", "hardware": {"cores": 8}},
            "metrics": {
                "cpu_pct": {
                    "latest": 98.2,
                    "baseline": 20.0,
                    "peak_15m": 99.6,
                    "peak_60m": 99.6,
                },
                "mem_pct": {"latest": 91.0},
                "disk_pct": {"baseline": 70.0},
                "load_1m": {"latest": 7.5, "peak_15m": 8.0, "threads": 4},
                "net_sent_mib_s": {"latest": 3.0},
            },
            "containers": [{"name": "web"}, {"name": "db"}],
            "quality": {"gaps_detected": True},
        }
    )
    reply = (
        f"{DIAGNOSIS_PREFIX}可能原因：离线前 CPU 接近满载，可能是 web 容器异常。\n"
        "优先排查：\n1. 查看 web 容器日志。\n2. 核对内存。"
    )

    record = summarize_analysis(user_prompt, reply, t_ref)

    assert record.time == t_ref
    assert record.summary == {
        "title": "Connection to Atlas is down",
        # Only the key readings of CPU, memory, disk, load, and swap are kept.
        "metrics": {
            "cpu_pct": {"latest": 98.2, "peak_15m": 99.6},
            "mem_pct": {"latest": 91.0},
            "load_1m": {"latest": 7.5, "peak_15m": 8.0},
        },
        "top_containers": ["web", "db"],
        "diagnosis": "离线前 CPU 接近满载，可能是 web 容器异常。",
    }


def test_summarize_analysis_bounds_unstructured_replies() -> None:
    t_ref = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    user_prompt = json.dumps({"alert": {"title": "t"}, "metrics": {}, "containers": []})

    ascii_colon = summarize_analysis(user_prompt, "可能原因: 磁盘写满。", t_ref)
    assert ascii_colon.summary["diagnosis"] == "磁盘写满。"

    custom = summarize_analysis(user_prompt, "自" * 300, t_ref)
    assert custom.summary["diagnosis"] == "自" * 149 + "…"
