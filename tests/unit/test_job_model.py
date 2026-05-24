"""Tests for Job model creation."""

from djobs.core.models import Job
from djobs.core.states import JobStatus


class TestJobCreation:
    def test_defaults(self) -> None:
        job = Job(type="demo.echo")
        assert job.type == "demo.echo"
        assert job.status == JobStatus.PENDING
        assert job.attempt == 0
        assert job.max_attempts == 1
        assert job.payload == {}
        assert job.id  # non-empty
        assert job.created_at is not None

    def test_custom_payload(self) -> None:
        job = Job(type="ai.summarize", payload={"text": "hello"})
        assert job.payload == {"text": "hello"}

    def test_unique_ids(self) -> None:
        a = Job(type="test")
        b = Job(type="test")
        assert a.id != b.id
