"""One-shot branch migration for automatic hook compatibility.

This file is executed and removed by the temporary PR job. It exists only to
apply exact, reviewable transformations without manually replacing a large
source file through the contents API.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


AUTO = "src/djobs/auto_hook.py"

replace_once(
    AUTO,
    '_INCOMPLETE_STATUSES = ("pending", "running", "retry_scheduled")',
    dedent(
        '''
        _RECOVERABLE_STATUSES = (
            "pending",
            "running",
            "retry_scheduled",
            "failed",
            "dead_lettered",
        )
        '''
    ).strip(),
)

replace_once(
    AUTO,
    dedent(
        '''
        def _shell_kind(tool_name: str) -> str | None:
            lowered = tool_name.strip().lower()
            if lowered in {"bash", "shell"}:
                return "bash"
            if lowered in {"powershell", "pwsh"}:
                return "powershell"
            return None
        '''
    ).strip(),
    dedent(
        '''
        def _shell_kind(
            payload: dict[str, Any],
            *,
            platform_name: str | None = None,
        ) -> str | None:
            """Resolve the original terminal shell across Copilot and VS Code payloads."""

            lowered = _tool_name(payload).strip().lower()
            if lowered in {"powershell", "pwsh"}:
                return "powershell"
            if lowered in {"bash", "shell"}:
                # PascalCase/VS Code-compatible payloads use the Claude name
                # ``Bash`` for both bash and PowerShell. The extension-host OS is
                # the only deterministic discriminator in that normalized form.
                if "hook_event_name" in payload and (platform_name or os.name) == "nt":
                    return "powershell"
                return "bash"
            if lowered in {
                "runterminalcommand",
                "run_in_terminal",
                "runinterminal",
                "terminal",
            }:
                return "powershell" if (platform_name or os.name) == "nt" else "bash"
            return None
        '''
    ).strip(),
)

replace_once(AUTO, "shell = _shell_kind(_tool_name(payload))", "shell = _shell_kind(payload)")

replace_once(
    AUTO,
    '    return {"permissionDecision": "allow", "modifiedArgs": args}',
    dedent(
        '''
            # Copilot CLI/cloud consumes the top-level fields. VS Code consumes
            # hookSpecificOutput.updatedInput. Returning both makes the same hook
            # file deterministic across hosts; unknown fields are ignored.
            return {
                "permissionDecision": "allow",
                "modifiedArgs": args,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": args,
                },
            }
        '''
    ).rstrip(),
)

replace_once(
    AUTO,
    dedent(
        '''
                    queue.complete(
                        task_id,
                        evidence=(f"automatic command checkpoint: exit 0 in {elapsed:.2f}s"),
                    )
        '''
    ).rstrip(),
    dedent(
        '''
                    queue.complete(
                        task_id,
                        evidence=(f"automatic command checkpoint: exit 0 in {elapsed:.2f}s"),
                    )
                    # Successful command checkpoints remain auditable but should
                    # not flood the active task/sidebar view.
                    queue.archive(task_id, "Automatic command completed")
        '''
    ).rstrip(),
)

replace_once(AUTO, "statuses=_INCOMPLETE_STATUSES,", "statuses=_RECOVERABLE_STATUSES,")

replace_once(
    AUTO,
    '    return {"additionalContext": "\\n".join(lines)}',
    dedent(
        '''
            context = "\\n".join(lines)
            return {
                "additionalContext": context,
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                },
            }
        '''
    ).rstrip(),
)

replace_once(
    AUTO,
    dedent(
        '''
        def _hook_config(mode: str) -> dict[str, Any]:
            bash_guard = "if command -v djobs >/dev/null 2>&1; then djobs hook {event} || true; fi"
            powershell_guard = (
                "if (Get-Command djobs -ErrorAction SilentlyContinue) {{ "
                "djobs hook {event}; "
                "if ($LASTEXITCODE -ne 0) {{ exit 0 }} "
                "}}"
            )

            def command_hook(event: str, *, matcher: str | None = None) -> dict[str, Any]:
                item: dict[str, Any] = {
                    "type": "command",
                    "bash": bash_guard.format(event=event),
                    "powershell": powershell_guard.format(event=event),
                    "timeoutSec": 10,
                    "env": {"DJOBS_HOOK_MODE": mode},
                }
                if matcher is not None:
                    item["matcher"] = matcher
                return item

            return {
                "version": 1,
                "hooks": {
                    "SessionStart": [command_hook("session-start")],
                    "PreToolUse": [command_hook("pre", matcher="Bash")],
                },
            }
        '''
    ).strip(),
    dedent(
        '''
        def _hook_config(mode: str, db_path: str | None = None) -> dict[str, Any]:
            bash_guard = "if command -v djobs >/dev/null 2>&1; then djobs hook {event} || true; fi"
            powershell_guard = (
                "if (Get-Command djobs -ErrorAction SilentlyContinue) {{ "
                "djobs hook {event}; "
                "if ($LASTEXITCODE -ne 0) {{ exit 0 }} "
                "}}"
            )

            def command_hook(event: str, *, matcher: str | None = None) -> dict[str, Any]:
                environment = {"DJOBS_HOOK_MODE": mode}
                if db_path is not None:
                    environment["DJOBS_DB"] = db_path
                item: dict[str, Any] = {
                    "type": "command",
                    "bash": bash_guard.format(event=event),
                    "powershell": powershell_guard.format(event=event),
                    "timeoutSec": 10,
                    "env": environment,
                }
                if matcher is not None:
                    item["matcher"] = matcher
                return item

            # Lower-camel event names are native to Copilot CLI/cloud. VS Code
            # converts this format to PascalCase and maps the OS-specific command
            # properties automatically.
            return {
                "version": 1,
                "hooks": {
                    "sessionStart": [command_hook("session-start")],
                    "preToolUse": [
                        command_hook(
                            "pre",
                            matcher=(
                                "bash|powershell|runTerminalCommand|run_in_terminal"
                            ),
                        )
                    ],
                },
            }
        '''
    ).strip(),
)

replace_once(
    AUTO,
    dedent(
        '''
        def install_hooks(
            root: Path | None = None,
            *,
            mode: str = "smart",
            force: bool = False,
        ) -> Path:
        '''
    ).strip(),
    dedent(
        '''
        def install_hooks(
            root: Path | None = None,
            *,
            mode: str = "smart",
            force: bool = False,
            db_path: Path | str | None = None,
        ) -> Path:
        '''
    ).strip(),
)

replace_once(
    AUTO,
    dedent(
        '''
            resolved_mode = _normalise_mode(mode)
            content = json.dumps(_hook_config(resolved_mode), indent=2) + "\\n"
        '''
    ).rstrip(),
    dedent(
        '''
            resolved_mode = _normalise_mode(mode)
            resolved_db = (
                str(Path(db_path).expanduser().resolve()) if db_path is not None else None
            )
            content = json.dumps(_hook_config(resolved_mode, resolved_db), indent=2) + "\\n"
        '''
    ).rstrip(),
)

replace_once(
    AUTO,
    '    install_parser.add_argument("--force", action="store_true")',
    dedent(
        '''
            db_group = install_parser.add_mutually_exclusive_group()
            db_group.add_argument(
                "--db",
                default=None,
                help="Use this SQLite database for both automatic hooks and MCP.",
            )
            db_group.add_argument(
                "--global",
                dest="use_global",
                action="store_true",
                help="Use the shared queue at ~/.djobs/global.db.",
            )
            install_parser.add_argument("--force", action="store_true")
        '''
    ).rstrip(),
)

replace_once(
    AUTO,
    "        install_hooks(Path(args.root), mode=args.mode, force=args.force)",
    dedent(
        '''
                hook_db = Path.home() / ".djobs" / "global.db" if args.use_global else args.db
                install_hooks(
                    Path(args.root),
                    mode=args.mode,
                    force=args.force,
                    db_path=hook_db,
                )
        '''
    ).rstrip(),
)

replace_once(
    "src/djobs/entrypoint.py",
    '    install_hooks(Path.cwd(), mode="smart", force=args.force)',
    dedent(
        '''
            hook_db = cli._global_db() if args.use_global else getattr(args, "db", None)
            install_hooks(
                Path.cwd(),
                mode="smart",
                force=args.force,
                db_path=hook_db,
            )
        '''
    ).rstrip(),
)

Path("tests/unit/test_auto_hook.py").write_text(
    dedent(
        '''
        """Tests for deterministic command rewriting and automatic checkpoints."""

        from __future__ import annotations

        import json
        from pathlib import Path

        import pytest

        from djobs import auto_hook
        from djobs.queue.service import QueueService
        from djobs.storage.sqlite import SQLiteJobRepository


        def _payload(command: str, cwd: Path, *, tool_name: str = "Bash") -> dict[str, object]:
            return {
                "hook_event_name": "PreToolUse",
                "session_id": "session-1",
                "timestamp": "2026-07-22T00:00:00Z",
                "cwd": str(cwd),
                "tool_name": tool_name,
                "tool_input": {"command": command, "timeout": 120},
            }


        def _decoded_rewrite(result: dict[str, object]) -> dict[str, object]:
            modified = result["modifiedArgs"]
            assert isinstance(modified, dict)
            command = modified["command"]
            assert isinstance(command, str)
            encoded = command.removeprefix("djobs hook run --payload ")
            return auto_hook._decode_envelope(encoded)


        def test_smart_mode_rewrites_meaningful_command_for_both_output_schemas(
            tmp_path: Path,
        ) -> None:
            result = auto_hook.rewrite_pre_tool_payload(
                _payload("pytest -q", tmp_path),
                mode="smart",
            )

            assert result["permissionDecision"] == "allow"
            modified = result["modifiedArgs"]
            assert modified["command"].startswith("djobs hook run --payload ")
            assert modified["timeout"] == 120

            specific = result["hookSpecificOutput"]
            assert specific["hookEventName"] == "PreToolUse"
            assert specific["permissionDecision"] == "allow"
            assert specific["updatedInput"] == modified


        def test_smart_mode_skips_read_only_command(tmp_path: Path) -> None:
            assert auto_hook.rewrite_pre_tool_payload(
                _payload("git status", tmp_path),
                mode="smart",
            ) == {}


        def test_all_mode_rewrites_read_only_command(tmp_path: Path) -> None:
            result = auto_hook.rewrite_pre_tool_payload(
                _payload("git status", tmp_path),
                mode="all",
            )
            assert result["modifiedArgs"]["command"].startswith("djobs hook run --payload ")


        def test_state_only_command_is_never_rewritten(tmp_path: Path) -> None:
            assert auto_hook.rewrite_pre_tool_payload(
                _payload("cd src", tmp_path),
                mode="all",
            ) == {}


        def test_camel_case_payload_is_supported(tmp_path: Path) -> None:
            payload = {
                "sessionId": "session-2",
                "timestamp": 123,
                "cwd": str(tmp_path),
                "toolName": "bash",
                "toolArgs": json.dumps({"command": "npm run build"}),
            }
            result = auto_hook.rewrite_pre_tool_payload(payload, mode="smart")
            assert _decoded_rewrite(result)["shell"] == "bash"


        def test_native_powershell_payload_preserves_powershell(tmp_path: Path) -> None:
            payload = {
                "sessionId": "session-ps",
                "timestamp": 123,
                "cwd": str(tmp_path),
                "toolName": "powershell",
                "toolArgs": {"command": "npm run build"},
            }
            result = auto_hook.rewrite_pre_tool_payload(payload, mode="smart")
            assert _decoded_rewrite(result)["shell"] == "powershell"


        def test_vscode_terminal_uses_extension_host_platform(tmp_path: Path) -> None:
            payload = _payload("npm run build", tmp_path, tool_name="runTerminalCommand")
            assert auto_hook._shell_kind(payload, platform_name="posix") == "bash"
            assert auto_hook._shell_kind(payload, platform_name="nt") == "powershell"


        def test_wrapped_success_archives_checkpoint_without_sidebar_noise(
            tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            monkeypatch.setattr(auto_hook, "_execute_command", lambda *_args: 0)
            payload = {
                "command": "pytest -q",
                "shell": "bash",
                "cwd": str(tmp_path),
                "session_id": "session-3",
            }

            assert auto_hook.run_wrapped_payload(payload) == 0

            repo = SQLiteJobRepository.from_path(tmp_path / "djobs_mcp.db")
            jobs = repo.list_jobs_by_correlation_ids([str(tmp_path)])
            assert len(jobs) == 1
            assert jobs[0].type == "auto-command"
            assert jobs[0].status.value == "archived"
            assert jobs[0].payload["summary"] == "pytest -q"
            events = repo.list_events(jobs[0].id)
            assert any(event.event_type == "job_succeeded" for event in events)
            assert any(event.event_type == "job_archived" for event in events)


        def test_wrapped_failure_records_failed_checkpoint(
            tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            monkeypatch.setattr(auto_hook, "_execute_command", lambda *_args: 7)
            payload = {
                "command": "npm test",
                "shell": "bash",
                "cwd": str(tmp_path),
                "session_id": "session-4",
            }

            assert auto_hook.run_wrapped_payload(payload) == 7

            repo = SQLiteJobRepository.from_path(tmp_path / "djobs_mcp.db")
            jobs = repo.list_jobs_by_correlation_ids([str(tmp_path)])
            assert jobs[0].status.value == "failed"
            assert jobs[0].last_error == "automatic command checkpoint: exit 7"


        def test_session_start_injects_unfinished_and_failed_checkpoints(tmp_path: Path) -> None:
            repo = SQLiteJobRepository.from_path(tmp_path / "djobs_mcp.db")
            queue = QueueService(repo)
            queue.submit(
                "auto-command",
                {"summary": "npm run build"},
                correlation_id=str(tmp_path),
            )
            failed = queue.submit(
                "auto-command",
                {"summary": "npm test"},
                correlation_id=str(tmp_path),
            )
            queue.fail(failed.id, "exit 1")

            result = auto_hook.session_start_context({"cwd": str(tmp_path)})

            assert "additionalContext" in result
            assert "2 unfinished checkpoint" in result["additionalContext"]
            assert "npm run build" in result["additionalContext"]
            assert "npm test" in result["additionalContext"]
            assert result["hookSpecificOutput"]["additionalContext"] == result["additionalContext"]


        def test_install_hooks_is_idempotent_and_uses_native_event_names(tmp_path: Path) -> None:
            target = auto_hook.install_hooks(tmp_path, mode="smart")
            first = target.read_text(encoding="utf-8")
            target_again = auto_hook.install_hooks(tmp_path, mode="smart")

            assert target_again == target
            assert target.read_text(encoding="utf-8") == first
            config = json.loads(first)
            assert "sessionStart" in config["hooks"]
            assert "preToolUse" in config["hooks"]
            matcher = config["hooks"]["preToolUse"][0]["matcher"]
            assert "bash" in matcher
            assert "powershell" in matcher
            assert "djobs hook pre" in first


        def test_install_hooks_propagates_shared_database(tmp_path: Path) -> None:
            shared = tmp_path / "shared" / "global.db"
            target = auto_hook.install_hooks(tmp_path, db_path=shared)
            config = json.loads(target.read_text(encoding="utf-8"))

            for event in ("sessionStart", "preToolUse"):
                environment = config["hooks"][event][0]["env"]
                assert environment["DJOBS_DB"] == str(shared.resolve())


        def test_hook_doctor_reports_missing_and_installed(tmp_path: Path) -> None:
            ok, _ = auto_hook.hook_doctor(tmp_path)
            assert not ok

            auto_hook.install_hooks(tmp_path)
            ok, detail = auto_hook.hook_doctor(tmp_path)
            assert ok
            assert "installed at" in detail
        '''
    ).lstrip(),
    encoding="utf-8",
)

README = "README.md"
replace_once(
    README,
    "It ships MCP tools, agent instructions, and a VS Code sidebar; the runtime installs and manages itself.",
    "It ships deterministic lifecycle hooks, context-efficient MCP tools, agent instructions, and a VS Code sidebar; setup is one command.",
)
replace_once(
    README,
    dedent(
        '''
        djobs gives your agent three tools that solve this:

        | Tool | What it does |
        |------|-------------|
        | `enqueue_task` | Save each file as a durable task — survives any crash |
        | `complete_task` | Mark a file done after the agent edits it |
        | `resume_session` | On next chat, find all unfinished files instantly |
        '''
    ).strip(),
    dedent(
        '''
        djobs combines deterministic hooks for the common path with MCP tools for
        structured, multi-step work:

        | Layer | What it does |
        |-------|-------------|
        | Automatic `preToolUse` hook | Rewrites meaningful shell commands before execution and records a durable checkpoint without relying on the model |
        | Automatic `sessionStart` hook | Injects unfinished and failed checkpoints into the next session |
        | MCP workflow tools | Track semantic multi-file tasks with `enqueue_batch`, `complete_batch`, `resume_delta`, evidence, dependencies, and multi-agent claims |
        '''
    ).strip(),
)
replace_once(
    README,
    dedent(
        '''
          Agent calls enqueue_task for each file  ← checkpoint saved
          Agent edits file 1 → complete_task      ✅
          Agent edits file 2 → complete_task      ✅
          ...
          Agent edits file 12 → complete_task     ✅
          💥 VS Code crashes

        You reopen VS Code, start a new chat: "hi"

          Agent calls resume_session              ← finds 8 incomplete tasks
          Agent edits file 13 → complete_task     ✅
        '''
    ).rstrip(),
    dedent(
        '''
          djobs hook checkpoints meaningful commands automatically
          Agent edits file 1 → structured MCP task completes ✅
          Agent edits file 2 → structured MCP task completes ✅
          ...
          Agent reaches file 12
          💥 VS Code crashes

        You reopen VS Code, start a new chat: "hi"

          sessionStart injects the remaining work automatically
          Agent continues with file 13                        ✅
        '''
    ).rstrip(),
)
replace_once(
    README,
    dedent(
        '''
        > **Maturity — early but tested.** 388 passing tests, CI across Python 3.11–3.13, SQLite and optional
        > PostgreSQL backends. Marked Alpha while the public API stabilizes; the core enqueue → complete →
        > resume flow is stable and used daily.
        '''
    ).rstrip(),
    dedent(
        '''
        > **Maturity — early but tested.** CI covers Python 3.11–3.14, SQLite and optional PostgreSQL
        > backends. Marked Alpha while the public API stabilizes; the core checkpoint → resume flow is
        > stable and used daily.
        '''
    ).rstrip(),
)
replace_once(
    README,
    "That one step installs the runtime, wires the MCP server, installs the agent\ninstructions, and adds the task sidebar. No terminal, no manual config.",
    "That one step installs the runtime, wires the MCP server, installs deterministic\nlifecycle hooks and agent instructions, and adds the task sidebar. No manual config.",
)
replace_once(
    README,
    dedent(
        '''
        After setup, you keep talking normally — “continue”, “fix this”, “run tests”,
        “retry”, “the previous run failed”, or “release” are enough. The extension does
        not generate, copy, or open Chat prompts. It registers MCP tools, installs the
        agent guidance, and shows the durable task state; the agent decides when to call
        `resume_session`, enqueue multi-step work, and finish each unit with evidence.
        '''
    ).rstrip(),
    dedent(
        '''
        After setup, you keep talking normally — “continue”, “fix this”, “run tests”,
        “retry”, “the previous run failed”, or “release” are enough. The extension does
        not generate, copy, or open Chat prompts. Meaningful terminal commands are
        checkpointed before execution, failed checkpoints are restored at the next
        session, and MCP remains available for semantic multi-step workflows.
        '''
    ).rstrip(),
)
replace_once(
    README,
    dedent(
        '''
        It writes `.vscode/mcp.json`, installs the agent guidance block in
        `.github/copilot-instructions.md`, runs `djobs doctor`, and prints next steps.
        It auto-detects the right interpreter, so the wiring works even in a JavaScript,
        Go, or Rust repo with no Python environment.
        '''
    ).rstrip(),
    dedent(
        '''
        It writes `.vscode/mcp.json`, installs `.github/hooks/djobs.json`, installs the
        agent guidance block in `.github/copilot-instructions.md`, runs `djobs doctor`,
        and prints next steps. It auto-detects the right interpreter, so the wiring works
        even in a JavaScript, Go, or Rust repo with no Python environment.
        '''
    ).rstrip(),
)
replace_once(
    README,
    dedent(
        '''
        > **djobs is not only an MCP tool.** It also installs agent instructions so
        > coding agents proactively call `resume_session`, `enqueue_task`,
        > `complete_task`, and `fail_task` during long or risky work — you don't have to
        > remember to tell them.
        '''
    ).rstrip(),
    dedent(
        '''
        > **djobs does not rely on the model remembering.** Deterministic hooks handle
        > command checkpointing and session recovery. Agent instructions and MCP tools
        > add richer file-level planning, evidence, dependencies, and multi-agent state.
        '''
    ).rstrip(),
)
replace_once(
    README,
    "djobs install-instructions  # write only the agent guidance block\ndjobs doctor                # diagnose an existing setup",
    "djobs install-instructions  # write only the agent guidance block\ndjobs hook install          # write only .github/hooks/djobs.json\ndjobs doctor                # diagnose an existing setup",
)
replace_once(README, "# [OK  ] djobs package: v0.7.3 ...", "# [OK  ] djobs package: v0.10.0 ...")
replace_once(
    README,
    "# [OK  ] agent guidance block: present in .github/copilot-instructions.md",
    "# [OK  ] agent guidance block: present in .github/copilot-instructions.md\n# [OK  ] automatic command hooks: installed at .github/hooks/djobs.json",
)
replace_once(
    README,
    dedent(
        '''
        ## Making Your Agent Use djobs Automatically

        After installing, your agent has the MCP tools available — but it won't use them unless you tell it to. Add the following to your agent instructions (e.g. `.github/copilot-instructions.md` or any `.agent.md`):

        ```
        At the start of every session, call resume_session to find unfinished work.
        For multi-file tasks, enqueue each file as a durable task and call complete_task after each edit.
        ```

        **What this gives you:**
        - On every new chat, unfinished work is automatically surfaced
        - For multi-file tasks (>3 files), each file is tracked as a durable task
        - After editing each file, progress is recorded
        - If a session crashes, the next chat auto-resumes from where it stopped — no questions asked

        You can also use the `djobs install-instructions` CLI command to add the guidance block automatically.
        '''
    ).rstrip(),
    dedent(
        '''
        ## Automatic Command Rewriting

        `djobs init` installs a deterministic `preToolUse` hook. Before a meaningful
        Bash or PowerShell command runs, the hook substitutes a `djobs hook run`
        wrapper through the host's supported tool-argument mutation API. The original
        command output and exit code are preserved.

        The default **smart** mode checkpoints tests, builds, linters, type checks, and
        other substantial compound commands. It skips read-only commands such as
        `git status` and shell-state-only commands such as `cd` or `export`.

        ```bash
        djobs hook install --mode smart   # recommended
        djobs hook install --mode all     # checkpoint almost every terminal command
        djobs hook install --mode off     # install hooks but disable rewriting
        djobs hook install --global       # share ~/.djobs/global.db with MCP
        djobs hook doctor                 # validate the hook file
        ```

        Hook processing is fail-open: if djobs cannot inspect or checkpoint a command,
        it returns control to the host instead of blocking the user's work. `djobs pause`
        disables both automatic rewriting and session-start recovery without deleting data.

        Successful automatic command checkpoints are archived after their audit evidence
        is recorded, keeping the active sidebar clean. Failed or interrupted checkpoints
        remain visible and are injected into the next session automatically.
        '''
    ).rstrip(),
)
replace_once(
    README,
    "pytest -q              # 379 tests (18 skipped without Postgres)\nruff check src/ tests/ # lint",
    "pytest -q              # tests\nruff check src/ tests/ # lint",
)
replace_once(
    README,
    "- **Native MCP setup** — registers the djobs MCP server without manual config in VS Code",
    "- **Native MCP + hook setup** — registers the MCP server and deterministic lifecycle hooks without manual config",
)
replace_once(
    README,
    "- [x] Agent guidance installer — nudges agents to resume/enqueue before editing",
    "- [x] Deterministic lifecycle hooks — rewrite meaningful commands and resume failed/interrupted checkpoints\n- [x] Agent guidance installer — adds semantic multi-file workflow guidance",
)

print("Applied automatic hook compatibility and documentation updates.")
