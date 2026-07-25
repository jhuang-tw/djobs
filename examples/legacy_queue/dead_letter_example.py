"""Example: inspecting and handling dead-lettered tasks.

After a job exhausts all max_attempts, it moves to 'dead_lettered' status.
This script shows how to list, inspect, and optionally resubmit them.
"""

from __future__ import annotations

from djobs import QueueService, SQLiteJobRepository

DB_PATH = "djobs_mcp.db"


def main() -> None:
    repo = SQLiteJobRepository.from_path(DB_PATH)
    queue = QueueService(repo)

    dead_jobs = queue.list_by_status("dead_lettered")
    if not dead_jobs:
        print("No dead-lettered tasks found.")
        return

    print(f"Found {len(dead_jobs)} dead-lettered task(s):\n")
    for job in dead_jobs:
        print(f"  ID:    {job.id}")
        print(f"  Type:  {job.type}")
        print(f"  Error: {job.last_error}")
        print(f"  Attempts: {job.attempt}/{job.max_attempts}")
        print()

    # To resubmit a dead-lettered task as a fresh job:
    #
    #   new_job = queue.submit(
    #       job_type=job.type,
    #       payload=job.payload,
    #       max_attempts=job.max_attempts,
    #       correlation_id=job.correlation_id,
    #   )
    #   print(f"Resubmitted as {new_job.id}")


if __name__ == "__main__":
    main()
