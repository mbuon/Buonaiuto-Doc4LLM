from ingestion.scheduler import IngestionScheduler, IngestionTrigger


def test_scheduler_deduplicates_triggers_by_event_key() -> None:
    scheduler = IngestionScheduler()
    trigger = IngestionTrigger(
        source_type="pypi_rss",
        library_id="fastapi",
        version="0.115.0",
        event_key="pypi:fastapi:0.115.0",
    )

    first = scheduler.enqueue(trigger)
    second = scheduler.enqueue(trigger)

    assert first is True
    assert second is False
    assert len(scheduler.pending()) == 1


def test_scheduler_generates_trace_id_when_missing() -> None:
    scheduler = IngestionScheduler()

    scheduler.enqueue(
        IngestionTrigger(
            source_type="pypi_rss",
            library_id="fastapi",
            version="0.115.0",
            event_key="pypi:fastapi:0.115.0",
        )
    )

    pending = scheduler.pending()

    assert len(pending) == 1
    assert isinstance(pending[0].trace_id, str)
    assert pending[0].trace_id


def test_scheduler_preserves_explicit_trace_id() -> None:
    scheduler = IngestionScheduler()

    scheduler.enqueue(
        IngestionTrigger(
            source_type="github_release",
            library_id="fastapi",
            version="0.115.1",
            event_key="github:fastapi:0.115.1",
            trace_id="trace-ingestion-123",
        )
    )

    assert scheduler.pending()[0].trace_id == "trace-ingestion-123"
