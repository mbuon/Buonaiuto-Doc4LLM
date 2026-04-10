from control.quotas import QuotaLimiter


def test_quota_limiter_enforces_daily_limit() -> None:
    limiter = QuotaLimiter()
    workspace = "ws-a"
    date_key = "2026-03-18"

    assert limiter.check_and_increment_daily(workspace, date_key, limit=2) is True
    assert limiter.check_and_increment_daily(workspace, date_key, limit=2) is True
    assert limiter.check_and_increment_daily(workspace, date_key, limit=2) is False


def test_quota_limiter_enforces_minute_rate_limit() -> None:
    limiter = QuotaLimiter()
    workspace = "ws-a"
    minute_key = "2026-03-18T14:52"

    assert limiter.check_and_increment_rate(workspace, minute_key, rpm_limit=1) is True
    assert limiter.check_and_increment_rate(workspace, minute_key, rpm_limit=1) is False
