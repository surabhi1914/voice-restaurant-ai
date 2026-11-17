import httpx
import pytest
from openai import RateLimitError

from src import groq_handler


def simulate_rate_limit():
    request = httpx.Request(
        "POST", "https://api.groq.com/openai/v1/audio/transcriptions"
    )

    response = httpx.Response(
        429, request=request, json={"error": {"message": "Rate limit exceeded"}}
    )

    raise RateLimitError(
        "Rate limit exceeded",
        response=response,
        body=None,
    )


def test_groq_rate_limit_stops_required_operation(monkeypatch):
    warnings = []
    events = []

    class StreamlitStopped(Exception):
        pass

    monkeypatch.setattr(
        groq_handler.st,
        "warning",
        lambda message: warnings.append(message),
    )

    def fake_stop():
        raise StreamlitStopped()

    monkeypatch.setattr(groq_handler.st, "stop", fake_stop)

    with pytest.raises(StreamlitStopped):
        groq_handler.call_groq(
            operation=simulate_rate_limit,
            feature="Speech transcription",
            log_event=lambda event, **kwargs: events.append(event),
        )

    assert len(warnings) == 1
    assert "usage limit" in warnings[0].lower()
    assert "groq_rate_limited" in events


def test_groq_rate_limit_allows_optional_speech_failure(monkeypatch):
    warnings = []

    monkeypatch.setattr(
        groq_handler.st,
        "warning",
        lambda message: warnings.append(message),
    )

    result = groq_handler.call_groq(
        operation=simulate_rate_limit,
        feature="Speech generation",
        log_event=lambda event, **kwargs: None,
        optional=True,
    )

    assert result is None
    assert len(warnings) == 1
