from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import os
import sys
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from xiaomi_health_sync.config import load_settings
from xiaomi_health_sync.mcp_health_data import METRIC_SOURCES, NormalizedHealthReader


HEALTH_METRICS = tuple(metric for metric in METRIC_SOURCES if metric != "sleep")
HEALTH_LABELS = {
    "steps": "步数",
    "calories": "活动热量",
    "heart_rate": "心率",
    "spo2": "血氧",
    "stress": "压力",
    "valid_stand": "站立",
    "intensity": "活动强度",
    "training_load": "训练负荷",
    "pai": "PAI",
    "resting_heart_rate": "静息心率",
    "single_temperature": "体温",
    "temperature_trend": "体温趋势",
    "temperature_characteristic": "体温特征",
    "glucose": "血糖",
    "diet": "饮食",
    "weight": "体重",
    "menstruation": "经期",
}


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _duration_minutes(value: Any) -> int | None:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    # Xiaomi currently stores minute counts in fields ending with _seconds.
    return round(amount / 60) if amount > 24 * 60 else amount


def _format_duration(minutes: int | None) -> str:
    if minutes is None:
        return "暂无"
    hours, remainder = divmod(max(0, minutes), 60)
    if hours and remainder:
        return f"{hours}小时{remainder}分"
    if hours:
        return f"{hours}小时"
    return f"{remainder}分"


def _parse_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(tz)


def _format_clock(value: Any, tz: ZoneInfo) -> str:
    parsed = _parse_datetime(value, tz)
    return parsed.strftime("%H:%M") if parsed else "暂无"


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _number_text(value: float) -> str:
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.1f}".rstrip("0").rstrip(".")


def _field_values(rows: list[dict[str, Any]], *names: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        containers = [row, _as_mapping(row.get("metrics")), _as_mapping(row.get("value"))]
        for container in containers:
            found = None
            for name in names:
                found = _as_number(container.get(name))
                if found is not None:
                    break
            if found is not None:
                values.append(found)
                break
    return values


def select_main_sleep(
    records: list[dict[str, Any]],
    sleep_day: str,
) -> dict[str, Any] | None:
    candidates: list[tuple[bool, str, dict[str, Any]]] = []
    for record in records:
        raw = _as_mapping(record.get("raw"))
        metrics = _as_mapping(raw.get("metrics"))
        if raw.get("sleep_day") != sleep_day or raw.get("sleep_source") != "watch":
            continue
        # Complete sessions outrank partial segments; latest end wins within a group.
        incomplete = metrics.get("is_incomplete") is not False
        candidates.append((incomplete, str(record.get("end_at") or ""), record))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=False)
    complete = [item for item in candidates if not item[0]]
    return (complete or candidates)[-1][2] if not complete else complete[-1][2]


def _sleep_lines(sleep: dict[str, Any] | None, tz: ZoneInfo) -> tuple[list[str], int | None]:
    if sleep is None:
        return ["暂无已同步的主睡眠记录。"], None

    raw = _as_mapping(sleep.get("raw"))
    metrics = _as_mapping(raw.get("metrics"))
    total = _duration_minutes(metrics.get("total_sleep_seconds"))
    if total is None:
        start = _parse_datetime(sleep.get("start_at"), tz)
        end = _parse_datetime(sleep.get("end_at"), tz)
        if start and end:
            total = max(0, round((end - start).total_seconds() / 60))

    lines = [
        f"入睡 {_format_clock(sleep.get('start_at'), tz)} · "
        f"醒来 {_format_clock(sleep.get('end_at'), tz)} · "
        f"总睡眠 {_format_duration(total)}",
        " · ".join(
            [
                f"深睡 {_format_duration(_duration_minutes(metrics.get('deep_sleep_seconds')))}",
                f"浅睡 {_format_duration(_duration_minutes(metrics.get('light_sleep_seconds')))}",
                f"REM {_format_duration(_duration_minutes(metrics.get('rem_sleep_seconds')))}",
                f"清醒 {_format_duration(_duration_minutes(metrics.get('awake_seconds')))}",
            ]
        ),
    ]
    if sleep.get("avg_hr") is not None:
        lines.append(f"平均心率 {_number_text(float(sleep['avg_hr']))} bpm")
    if sleep.get("avg_spo2") is not None:
        lines.append(f"平均血氧 {_number_text(float(sleep['avg_spo2']))}%")

    analysis: list[str] = []
    if total is not None:
        if total < 360:
            analysis.append("总时长偏短，今天优先安排补足休息")
        elif total < 420:
            analysis.append("总时长接近 6–7 小时，今天留意午后精力")
        else:
            analysis.append("总时长达到 7 小时以上，基础恢复时间较充足")
        deep = _duration_minutes(metrics.get("deep_sleep_seconds"))
        if deep is not None and total > 0 and deep / total < 0.15:
            analysis.append("深睡占比相对偏低")
    if sleep.get("avg_spo2") is not None and float(sleep["avg_spo2"]) < 95:
        analysis.append("平均血氧低于常见参考范围，建议结合连续数据观察")
    if analysis:
        lines.append("分析：" + "；".join(analysis) + "。")
    return lines, total


def summarize_metric(metric: str, rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    label = HEALTH_LABELS.get(metric, metric)
    if metric == "steps":
        values = _field_values(rows, "steps")
        return f"{label} {_number_text(sum(values))}" if values else None
    if metric == "calories":
        values = _field_values(rows, "calories", "calorie")
        return f"{label} {_number_text(sum(values))} kcal" if values else None
    if metric == "heart_rate":
        values = _field_values(rows, "bpm", "heart_rate")
        if not values:
            return None
        low, high = min(values), max(values)
        span = f"{_number_text(low)}–{_number_text(high)} bpm" if low != high else f"{_number_text(low)} bpm"
        return f"{label} {span}"
    if metric == "spo2":
        values = _field_values(rows, "percent", "spo2")
        if not values:
            return None
        return f"{label} 平均 {_number_text(sum(values) / len(values))}%（最低 {_number_text(min(values))}%）"
    if metric == "stress":
        values = _field_values(rows, "stress")
        return f"{label} 平均 {_number_text(sum(values) / len(values))}（最高 {_number_text(max(values))}）" if values else None
    if metric == "valid_stand":
        return f"{label} {len(rows)} 个时段"
    if metric == "resting_heart_rate":
        values = _field_values(rows, "bpm", "heart_rate")
        return f"{label} {_number_text(values[-1])} bpm" if values else None
    if metric == "diet":
        calories = _field_values(rows, "total_calorie_kcal", "calorie_kcal")
        protein = _field_values(rows, "total_protein_g", "protein_g")
        parts = [f"{label} {_number_text(sum(calories))} kcal"] if calories else [f"{label} {len(rows)} 餐"]
        if protein:
            parts.append(f"蛋白质 {_number_text(sum(protein))} g")
        return " · ".join(parts)
    if metric == "glucose":
        values = _field_values(rows, "glucose_mg_dl", "glucose")
        return f"{label} {_number_text(min(values))}–{_number_text(max(values))} mg/dL" if values else None
    if metric == "weight":
        values = _field_values(rows, "weight_kg", "weight")
        return f"{label} {_number_text(values[-1])} kg" if values else None

    return f"{label} {len(rows)} 条记录"


def safe_error_text(exc: BaseException) -> str:
    text = str(exc)
    secrets = (
        ((os.getenv("SLACK_WEBHOOK_URL") or "").strip(), "<redacted webhook>"),
        ((os.getenv("SLACK_USER_TOKEN") or "").strip(), "<redacted Slack token>"),
    )
    for secret, replacement in secrets:
        if secret:
            text = text.replace(secret, replacement)
    return text


def render_trigger(report_date: date) -> str:
    report_id = (os.getenv("WAKE_REPORT_ID") or "").strip()
    report_kind = (os.getenv("WAKE_REPORT_KIND") or "morning").strip()
    if report_id:
        title = {
            "morning": "早安我的少年",
            "nap": "午睡简报",
            "sleep_update": "睡眠更新",
            "new_sleep": "新睡眠",
        }.get(report_kind, "睡眠简报")
        return (
            "Xiaomi Health morning report ready\n"
            f"report_id={report_id}\n"
            f"report_kind={report_kind}\n"
            f"title={title}\n"
            "请调用 Xiaomi Health MCP 的 get_morning_context(report_id)，"
            "核验 data_coverage 后生成完整报告；不要猜测缺失数据。"
        )
    health_date = report_date - timedelta(days=1)
    return (
        f"Xiaomi Health morning report ready: "
        f"sleep_day={report_date.isoformat()} health_date={health_date.isoformat()}. "
        "Please read the Xiaomi Health MCP data and send the full morning report "
        "to me in ChatGPT."
    )


def render_report(
    *,
    report_date: date,
    sleep_day: str,
    sleep: dict[str, Any] | None,
    metrics: dict[str, list[dict[str, Any]]],
    weather: str | None = None,
) -> str:
    tz = ZoneInfo(os.getenv("WAKE_TIMEZONE", "Asia/Shanghai"))
    sleep_lines, _ = _sleep_lines(sleep, tz)
    lines = [
        f"🌅 {report_date.isoformat()} 晨报",
        "",
        "🌤 今日",
        weather or "天气：Mi Fitness 当前天气/定位数据尚未接入，本次不猜测位置。",
        "",
        "🌙 昨晚睡眠",
        *sleep_lines,
        "",
        f"📊 昨日健康（{(report_date - timedelta(days=1)).isoformat()}）",
    ]
    health_lines = [
        summary
        for metric, rows in metrics.items()
        if (summary := summarize_metric(metric, rows)) is not None
    ]
    lines.extend(health_lines or ["暂无已同步的健康数据。"])
    lines.append("")
    lines.append(f"数据日期：睡眠 {sleep_day} · 健康 {(report_date - timedelta(days=1)).isoformat()}")
    return "\n".join(lines)


def _day_window(day: date, tz: ZoneInfo) -> tuple[str, str]:
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    utc = timezone.utc
    return (
        start.astimezone(utc).isoformat().replace("+00:00", "Z"),
        end.astimezone(utc).isoformat().replace("+00:00", "Z"),
    )


def build_report(
    reader: NormalizedHealthReader,
    report_date: date,
    *,
    tz: ZoneInfo,
) -> str:
    sleep_start = datetime.combine(report_date, time.min, tzinfo=tz) - timedelta(hours=12)
    sleep_end = datetime.combine(report_date, time.min, tzinfo=tz) + timedelta(hours=14)
    sleep_rows = reader.query_all(
        "sleep",
        start_at=sleep_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        end_at=sleep_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        page_size=500,
    )["records"]
    sleep = select_main_sleep(sleep_rows, report_date.isoformat())

    health_start, health_end = _day_window(report_date - timedelta(days=1), tz)
    metrics = {
        metric: reader.query_all(
            metric,
            start_at=health_start,
            end_at=health_end,
            page_size=1000,
        )["records"]
        for metric in HEALTH_METRICS
    }
    return render_report(
        report_date=report_date,
        sleep_day=report_date.isoformat(),
        sleep=sleep,
        metrics=metrics,
    )


def post_slack_report(
    webhook_url: str,
    text: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> None:
    with httpx.Client(timeout=20.0, transport=transport) as client:
        response = client.post(webhook_url, json={"text": text})
        response.raise_for_status()


def send_slack_report(
    text: str,
    *,
    user_token: str,
    channel_id: str,
    webhook_url: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    if user_token:
        if not channel_id:
            raise ValueError("SLACK_CHANNEL_ID is required when SLACK_USER_TOKEN is configured")
        with httpx.Client(timeout=20.0, transport=transport) as client:
            response = client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {user_token}"},
                json={"channel": channel_id, "text": text},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("ok") is not True:
            error = payload.get("error") or "unknown_error"
            raise ValueError(f"Slack API error: {error}")
        return

    if not webhook_url:
        raise ValueError("SLACK_WEBHOOK_URL is required when SLACK_USER_TOKEN is not configured")
    post_slack_report(webhook_url, text, transport=transport)


def _parse_report_date(value: str | None, tz: ZoneInfo) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(tz).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send a Xiaomi Health morning-report trigger to Slack.")
    parser.add_argument("--date", help="report date in YYYY-MM-DD; defaults to the local date")
    parser.add_argument("--dry-run", action="store_true", help="print the trigger without sending it")
    args = parser.parse_args(argv)

    try:
        load_settings()
        tz = ZoneInfo(os.getenv("WAKE_TIMEZONE", "Asia/Shanghai"))
        report_date = _parse_report_date(args.date, tz)
        user_token = (os.getenv("SLACK_USER_TOKEN") or "").strip()
        channel_id = (os.getenv("SLACK_CHANNEL_ID") or "").strip()
        webhook_url = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()

        text = render_trigger(report_date)
        if args.dry_run:
            print(text)
        else:
            send_slack_report(
                text,
                user_token=user_token,
                channel_id=channel_id,
                webhook_url=webhook_url,
            )
            print(f"morning report trigger sent for {report_date.isoformat()}")
    except (ValueError, httpx.HTTPError, OSError) as exc:
        print(f"morning report trigger failed: {safe_error_text(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
