"""Multi-step workflow execution engine with status tracking."""

from enum import Enum

class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

class WorkflowStep:
    def __init__(self, name: str, action: str):
        self.name = name
        self.action = action
        self.status = StepStatus.PENDING
        self.error: str | None = None

    def start(self) -> None:
        self.status = StepStatus.RUNNING

    def complete(self) -> None:
        self.status = StepStatus.COMPLETED

    def fail(self, error: str) -> None:
        self.status = StepStatus.FAILED
        self.error = error

    def skip(self) -> None:
        self.status = StepStatus.SKIPPED

class Workflow:
    def __init__(self, name: str):
        self.name = name
        self.steps: list[WorkflowStep] = []

    def add_step(self, name: str, action: str) -> WorkflowStep:
        step = WorkflowStep(name, action)
        self.steps.append(step)
        return step

    def progress(self) -> float:
        if not self.steps:
            return 0.0
        done = sum(1 for s in self.steps if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED))
        return done / len(self.steps) * 100

    def is_complete(self) -> bool:
        return all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in self.steps)

    def failed_steps(self) -> list[WorkflowStep]:
        return [s for s in self.steps if s.status == StepStatus.FAILED]
