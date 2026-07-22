from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.12.0"

EXTENSION_TS = r"""import * as vscode from 'vscode';
import { DjobsClient } from './djobsClient';

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    return;
  }

  const client = new DjobsClient(workspaceRoot);
  const output = vscode.window.createOutputChannel('djobs');
  context.subscriptions.push(output);

  const nativeMcp = typeof vscode.lm.registerMcpServerDefinitionProvider === 'function';
  const mcpDidChange = new vscode.EventEmitter<void>();
  context.subscriptions.push(mcpDidChange);

  if (nativeMcp) {
    context.subscriptions.push(
      vscode.lm.registerMcpServerDefinitionProvider('djobsServerProvider', {
        onDidChangeMcpServerDefinitions: mcpDidChange.event,
        provideMcpServerDefinitions: async () => {
          if (client.hasMcpJsonDjobsServer() && !client.detectDeadMcpInterpreter()) {
            return [];
          }
          if (!(await client.isPackageInstalled())) {
            return [];
          }
          const launch = client.mcpServerLaunch();
          const version = await client.installedVersion();
          const server = new vscode.McpStdioServerDefinition(
            'djobs',
            launch.command,
            launch.args,
            launch.env,
            version,
          );
          server.cwd = vscode.Uri.file(launch.cwd);
          return [server];
        },
      }),
    );
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('djobs.setup', async () => {
      await runDjobsSetup(context, client, nativeMcp, mcpDidChange);
    }),
    vscode.commands.registerCommand('djobs.diagnose', async () => {
      await runDiagnostics(client, output, nativeMcp);
    }),
    vscode.commands.registerCommand('djobs.pause', async () => {
      await runPauseCommand(client, true);
    }),
    vscode.commands.registerCommand('djobs.unpause', async () => {
      await runPauseCommand(client, false);
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (
        event.affectsConfiguration('djobs.pythonPath')
        || event.affectsConfiguration('djobs.queueLocation')
        || event.affectsConfiguration('djobs.globalDbPath')
        || event.affectsConfiguration('djobs.dbPath')
      ) {
        client.resetLauncher();
        mcpDidChange.fire();
      }
    }),
  );
}

export function deactivate(): void {}

async function runDiagnostics(
  client: DjobsClient,
  output: vscode.OutputChannel,
  nativeMcp: boolean,
): Promise<void> {
  output.clear();
  output.show(true);
  output.appendLine('Running djobs diagnostics...\n');

  try {
    const report = await client.doctor();
    let allOk = true;
    for (const check of report.checks) {
      if (nativeMcp && check.name === 'mcp.json wiring') {
        output.appendLine('  [INFO] VS Code native MCP: registered by the extension; mcp.json is optional');
        continue;
      }
      const info = check.level === 'info';
      if (!check.ok && !info) {
        allOk = false;
      }
      const mark = check.ok ? 'OK  ' : info ? 'INFO' : 'FAIL';
      output.appendLine(`  [${mark}] ${check.name}: ${check.detail}`);
    }

    const hooksReady = await client.hooksInstalled();
    output.appendLine(
      hooksReady
        ? '  [OK  ] automatic coding hooks: installed'
        : '  [FAIL] automatic coding hooks: missing; run "djobs: Set up / Repair djobs"',
    );
    allOk = allOk && hooksReady;

    output.appendLine('');
    output.appendLine(allOk ? 'All checks passed.' : 'Some checks need attention.');
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    output.appendLine(`Could not run diagnostics: ${detail}`);
    const choice = await vscode.window.showErrorMessage(
      'djobs diagnostics could not run. Set up or repair djobs now?',
      'Set up djobs',
    );
    if (choice === 'Set up djobs') {
      await vscode.commands.executeCommand('djobs.setup');
    }
  }
}

async function runDjobsSetup(
  context: vscode.ExtensionContext,
  client: DjobsClient,
  nativeMcp: boolean,
  mcpDidChange: vscode.EventEmitter<void>,
): Promise<void> {
  try {
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'Setting up djobs...' },
      async (progress) => {
        const installed = await client.isPackageInstalled();
        if (!installed) {
          progress.report({ message: 'Installing the coding checkpoint engine...' });
          await client.installPackage();
        } else {
          const installedVersion = await client.installedVersion();
          const extensionVersion = String(context.extension.packageJSON.version ?? '');
          if (installedVersion && extensionVersion && installedVersion !== extensionVersion) {
            progress.report({ message: 'Updating the coding checkpoint engine...' });
            await client.updatePackage();
          }
        }

        progress.report({ message: 'Installing smart coding hooks...' });
        await client.installHooks();

        if (nativeMcp) {
          if (client.detectDeadMcpInterpreter()) {
            progress.report({ message: 'Repairing an old MCP launch path...' });
            await client.reWireMcp();
          }
        } else if (client.isGlobalQueue() && !client.isGlobalMcpWired()) {
          progress.report({ message: 'Wiring the MCP server...' });
          await client.wireGlobalMcp();
        } else if (client.detectDeadMcpInterpreter()) {
          progress.report({ message: 'Repairing the MCP launch path...' });
          await client.reWireMcp();
        }
      },
    );

    mcpDidChange.fire();
    vscode.window.showInformationMessage(
      'djobs is ready. Smart command checkpoints and session recovery are active; no sidebar is added.',
    );
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    if (detail === 'NO_PYTHON_RUNTIME') {
      const choice = await vscode.window.showErrorMessage(
        'djobs needs Python 3.11+ or uv. Install one, then run setup again.',
        'Get uv',
        'Get Python',
      );
      if (choice === 'Get uv') {
        await vscode.env.openExternal(
          vscode.Uri.parse('https://docs.astral.sh/uv/getting-started/installation/'),
        );
      } else if (choice === 'Get Python') {
        await vscode.env.openExternal(vscode.Uri.parse('https://www.python.org/downloads/'));
      }
      return;
    }

    if (detail.startsWith('PYTHON_TOO_OLD')) {
      vscode.window.showErrorMessage(
        'djobs requires Python 3.11 or newer. Install uv or a newer Python, then run setup again.',
      );
      return;
    }

    const choice = await vscode.window.showErrorMessage(
      `djobs setup failed: ${detail}`,
      'Open diagnostics',
    );
    if (choice === 'Open diagnostics') {
      await vscode.commands.executeCommand('djobs.diagnose');
    }
  }
}

async function runPauseCommand(client: DjobsClient, pause: boolean): Promise<void> {
  try {
    if (pause) {
      await client.pause();
      vscode.window.showInformationMessage(
        'djobs paused. Automatic checkpoint rewriting and recovery are disabled; no state was deleted.',
      );
    } else {
      await client.unpause();
      vscode.window.showInformationMessage('djobs resumed.');
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    if (/invalid choice: '?(pause|unpause)'?|unrecognized arguments: (pause|unpause)/i.test(detail)) {
      const selected = await vscode.window.showErrorMessage(
        'The installed djobs engine is too old for this command.',
        'Update djobs',
      );
      if (selected === 'Update djobs') {
        await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: 'Updating djobs...' },
          async () => client.updatePackage(),
        );
        await runPauseCommand(client, pause);
      }
      return;
    }
    vscode.window.showErrorMessage(`djobs command failed: ${detail}`);
  }
}
"""

EXTENSION_README = """# djobs — Coding Token Saver

![djobs coding checkpoints](https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png)

**Automatic coding checkpoints that reduce repeated context, re-reading, and command work.**

The extension is intentionally headless. It does not add an Activity Bar icon, task
sidebar, polling loop, or background dashboard. It installs or repairs the djobs
runtime, registers the MCP server, and installs deterministic coding hooks.

## Setup

1. Install the extension.
2. Run **djobs: Set up / Repair djobs** from the Command Palette.
3. Start a new compatible coding-agent session and work normally.

Smart hooks checkpoint meaningful tests, builds, linters, type checks, and compound
terminal commands before execution. Failed or interrupted work can be restored in
the next session without asking the model to reconstruct the entire conversation.

## Commands

- **djobs: Set up / Repair djobs** — install/update the engine, native MCP registration, and smart hooks.
- **djobs: Diagnose Setup** — verify runtime, MCP, queue, and hook health.
- **djobs: Pause djobs** — temporarily disable automatic rewriting and recovery.
- **djobs: Resume djobs** — re-enable automation.

There is no task-management UI. Detailed inspection remains available through the
CLI and MCP tools only when needed.

## See the estimated savings

```bash
djobs gain
djobs gain --graph
djobs gain --history
djobs gain --all --format json
```

The report separates automatic checkpoints from structured workflows and labels
its values as estimates rather than provider billing data.

## Compatibility

Automatic hooks, native MCP registration, setup, and diagnostics are implemented
and tested with GitHub Copilot in VS Code. Other MCP-compatible coding agents can
use the core tools; automatic behavior depends on each host's hook protocol.

## Privacy and control

Queue data stays local unless you configure a shared database. `djobs pause`
disables automation without deleting state. The extension performs no task polling
and adds no persistent VS Code view.
"""

PACKAGE = {
    "name": "djobs",
    "displayName": "djobs — Coding Token Saver",
    "description": "Automatic coding checkpoints that reduce repeated context and command work.",
    "version": VERSION,
    "publisher": "jhuang-tw",
    "license": "MIT",
    "icon": "media/icon-128.png",
    "repository": {"type": "git", "url": "https://github.com/jhuang-tw/djobs"},
    "engines": {"vscode": "^1.101.0"},
    "categories": ["Machine Learning", "Other"],
    "keywords": [
        "djobs", "coding agent", "coding token saver", "token savings",
        "agent checkpoint", "context recovery", "crash recovery",
        "session recovery", "command checkpoint", "MCP",
        "Model Context Protocol", "GitHub Copilot", "Claude Code",
        "Codex", "Cursor", "Cline", "Gemini",
    ],
    "activationEvents": ["onStartupFinished"],
    "main": "./out/extension.js",
    "contributes": {
        "mcpServerDefinitionProviders": [{"id": "djobsServerProvider", "label": "djobs"}],
        "commands": [
            {"command": "djobs.setup", "title": "Set up / Repair djobs", "category": "djobs"},
            {"command": "djobs.diagnose", "title": "Diagnose Setup", "category": "djobs"},
            {"command": "djobs.pause", "title": "Pause djobs", "category": "djobs"},
            {"command": "djobs.unpause", "title": "Resume djobs", "category": "djobs"},
        ],
        "configuration": {
            "title": "djobs",
            "properties": {
                "djobs.pythonPath": {
                    "type": "string",
                    "default": "",
                    "description": "Path to a Python executable with djobs installed. Leave empty to auto-detect .venv, djobs, or Python on PATH.",
                },
                "djobs.dbPath": {
                    "type": "string",
                    "default": "djobs_mcp.db",
                    "description": "Workspace queue path. Used only when djobs.queueLocation is workspace.",
                },
                "djobs.queueLocation": {
                    "type": "string",
                    "enum": ["workspace", "global"],
                    "default": "global",
                    "enumDescriptions": [
                        "Keep a separate queue in this workspace.",
                        "Share ~/.djobs/global.db while keeping recovery scoped to the workspace.",
                    ],
                    "description": "Where durable checkpoint state is stored.",
                },
                "djobs.globalDbPath": {
                    "type": "string",
                    "default": "",
                    "description": "Optional absolute path for the shared queue. Leave empty for ~/.djobs/global.db.",
                },
            },
        },
    },
    "scripts": {
        "sync-version": "node scripts/sync-version.js",
        "vscode:prepublish": "npm run sync-version && npm run compile",
        "compile": "npm run sync-version && tsc -p ./",
        "watch": "tsc -watch -p ./",
        "package": "vsce package",
    },
    "devDependencies": {
        "@types/node": "^20.0.0",
        "@types/vscode": "^1.101.0",
        "typescript": "^5.4.0",
        "@vscode/vsce": "^3.0.0",
    },
    "homepage": "https://jhuang-tw.github.io/djobs/",
    "bugs": {"url": "https://github.com/jhuang-tw/djobs/issues"},
    "galleryBanner": {"color": "#272A3A", "theme": "dark"},
    "preview": True,
    "pricing": "Free",
}


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {text.count(old)}")
    return text.replace(old, new, 1)


def update_client() -> None:
    path = ROOT / "vscode-ext/src/djobsClient.ts"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import { DjobsCommandOptions, DjobsDoctorReport, DjobsScope, DjobsStatus, DjobsTask } from './types';",
        "import { DjobsDoctorReport } from './types';",
        "client type import",
    )
    start = text.index("  getViewOptions():")
    end = text.index("  /** Pause djobs", start)
    text = text[:start] + text[end:]

    text = text.replace(
        "    const options = this.getOptions();\n    await this.run(['pause', '--db', options.dbPath]);",
        "    await this.run(['pause', '--db', this.resolvedDbPath()]);",
    )
    text = text.replace(
        "    const options = this.getOptions();\n    await this.run(['unpause', '--db', options.dbPath]);",
        "    await this.run(['unpause', '--db', this.resolvedDbPath()]);",
    )

    start = text.index("  async archiveCurrentWorkflow")
    end = text.index("  /** True when the user has selected", start)
    text = text[:start] + text[end:]

    install_hooks = """  /** Install deterministic smart-mode coding hooks for this workspace. */
  async installHooks(): Promise<void> {
    const args = ['hook', 'install', '--mode', 'smart', '--force'];
    if (this.isGlobalQueue()) {
      args.push('--global');
    } else {
      args.push('--db', this.resolvedDbPath());
    }
    await this.run(args);
  }

  /** Check whether automatic coding hooks are installed and valid. */
  async hooksInstalled(): Promise<boolean> {
    try {
      await this.run(['hook', 'doctor']);
      return true;
    } catch {
      return false;
    }
  }

"""
    marker = "  /** True when the user has selected the shared global queue. */"
    text = replace_once(text, marker, install_hooks + marker, "install hook insertion")

    old_resolved = """  /** Absolute path of the queue DB the sidebar reads (global or per-workspace). */
  resolvedDbPath(): string {
    return this.getOptions().dbPath;
  }
"""
    new_resolved = """  /** Absolute queue path used by hooks and the MCP server. */
  resolvedDbPath(): string {
    const config = vscode.workspace.getConfiguration('djobs');
    const queueLocation = config.get<string>('queueLocation') ?? 'global';
    if (queueLocation === 'global') {
      const configuredGlobal = config.get<string>('globalDbPath')?.trim();
      return configuredGlobal && configuredGlobal.length > 0
        ? configuredGlobal
        : path.join(os.homedir(), '.djobs', 'global.db');
    }
    const configuredDb = config.get<string>('dbPath')?.trim() || 'djobs_mcp.db';
    return path.isAbsolute(configuredDb)
      ? configuredDb
      : path.join(this.workspaceRoot, configuredDb);
  }
"""
    text = replace_once(text, old_resolved, new_resolved, "resolved db path")

    start = text.index("  private getOptions():")
    end = text.index("  private launcher?:", start)
    text = text[:start] + text[end:]

    text = text.replace("the absolute queue path the sidebar reads", "the absolute queue path used by hooks")
    text = text.replace("reads and the agent's writes", "hook and agent writes")
    text = text.replace("on every sidebar refresh", "on repeated command invocations")
    path.write_text(text, encoding="utf-8")


def update_versions() -> None:
    init_path = ROOT / "src/djobs/__init__.py"
    init_text = init_path.read_text(encoding="utf-8")
    init_text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{VERSION}"', init_text, count=1)
    init_path.write_text(init_text, encoding="utf-8")

    server_path = ROOT / "server.json"
    server = json.loads(server_path.read_text(encoding="utf-8"))
    server["version"] = VERSION
    for package in server.get("packages", []):
        package["version"] = VERSION
    server_path.write_text(json.dumps(server, indent=2) + "\n", encoding="utf-8")

    lock_path = ROOT / "vscode-ext/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["version"] = VERSION
    lock["packages"][""]["version"] = VERSION
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def update_docs_and_tests() -> None:
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    readme = replace_once(
        readme,
        "The extension installs or repairs the Python runtime, registers the MCP server,\ninstalls deterministic lifecycle hooks, and adds the task sidebar.",
        "The extension installs or repairs the Python runtime, registers the MCP server,\nand installs deterministic lifecycle hooks without adding a persistent sidebar or poller.",
        "root README setup",
    )
    readme = replace_once(
        readme,
        "| GitHub Copilot in VS Code | Automatic hooks, MCP registration, setup, and sidebar are implemented and tested. |",
        "| GitHub Copilot in VS Code | Automatic hooks, native MCP registration, setup, pause/resume, and diagnostics are implemented and tested. |",
        "root README compatibility",
    )
    readme_path.write_text(readme, encoding="utf-8")

    docs_path = ROOT / "docs/index.html"
    docs = docs_path.read_text(encoding="utf-8")
    docs = replace_once(
        docs,
        "Automatic hooks, setup, and the sidebar are implemented and tested with GitHub Copilot in VS Code.",
        "Automatic hooks, native MCP registration, setup, and diagnostics are implemented and tested with GitHub Copilot in VS Code.",
        "site compatibility",
    )
    docs = replace_once(
        docs,
        "Write approvals stay conservative, prompt actions are opt-in, and <code>djobs pause</code> disables automation without deleting state.",
        "Write approvals stay conservative, the extension performs no task polling, and <code>djobs pause</code> disables automation without deleting state.",
        "site control",
    )
    docs_path.write_text(docs, encoding="utf-8")

    changelog_path = ROOT / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    section = """## [0.12.0] - 2026-07-22

### Changed
- `[ext]` **Headless coding integration.** The VS Code extension is now a thin setup, native-MCP, hook, pause/resume, and diagnostics layer. It no longer creates an Activity Bar container, task tree, status badge, prompt-action UI, or task-management context menus.
- `[ext]` **Coding-first setup.** Setup now installs deterministic smart-mode hooks directly, so the extension's runtime work is focused on preventing repeated tests, builds, linters, type checks, and context reconstruction.

### Removed
- `[ext]` **Background task polling.** Removed the five-second Python status poller and overlapping-refresh guard because no persistent task view remains.
- `[ext]` **Sidebar implementation.** Removed the 600-line tree provider and the client/status types used only to render, inspect, archive, delete, or prompt from the sidebar.
- `[ext]` **Redundant update/network logic.** Removed custom Marketplace/PyPI update checks; VS Code handles extension updates, while the explicit setup command keeps the Python engine aligned.

"""
    changelog = replace_once(
        changelog,
        "## [Unreleased]\n\n",
        "## [Unreleased]\n\n" + section,
        "changelog insertion",
    )
    changelog_path.write_text(changelog, encoding="utf-8")

    guard_path = ROOT / "tests/unit/test_release_site_guards.py"
    guard = guard_path.read_text(encoding="utf-8")
    start = guard.index("def test_extension_prompt_actions_are_opt_in()")
    guard = guard[:start] + """def test_extension_is_headless_and_coding_focused() -> None:
    package = json.loads(_EXT_PACKAGE.read_text(encoding="utf-8"))
    contributes = package["contributes"]

    assert "viewsContainers" not in contributes
    assert "views" not in contributes
    assert "menus" not in contributes

    commands = {item["command"] for item in contributes["commands"]}
    assert commands == {"djobs.setup", "djobs.diagnose", "djobs.pause", "djobs.unpause"}

    properties = contributes["configuration"]["properties"]
    removed_ui_settings = {
        "djobs.scope",
        "djobs.showCompleted",
        "djobs.promptActions.enabled",
        "djobs.autoRefreshInterval",
    }
    assert removed_ui_settings.isdisjoint(properties)

    extension_text = (_REPO / "vscode-ext" / "src" / "extension.ts").read_text(encoding="utf-8")
    assert "createTreeView" not in extension_text
    assert "createStatusBarItem" not in extension_text
    assert "setInterval" not in extension_text
    assert "tasksProvider" not in extension_text
    assert not (_REPO / "vscode-ext" / "src" / "tasksProvider.ts").exists()
"""
    guard_path.write_text(guard, encoding="utf-8")

    cli_path = ROOT / "src/djobs/cli.py"
    cli = cli_path.read_text(encoding="utf-8")
    cli = replace_once(
        cli,
        '    info_checks = {"djobs-mcp on PATH"}',
        '    info_checks = {"djobs-mcp on PATH", "agent guidance block"}',
        "doctor advisory checks",
    )
    cli_path.write_text(cli, encoding="utf-8")


def main() -> None:
    write("vscode-ext/src/extension.ts", EXTENSION_TS)
    write("vscode-ext/README.md", EXTENSION_README)
    write("vscode-ext/package.json", json.dumps(PACKAGE, indent=2) + "\n")
    update_client()
    write(
        "vscode-ext/src/types.ts",
        """export interface DjobsDoctorCheck {
  name: string;
  ok: boolean;
  detail: string;
  /** Advisory checks do not make the overall diagnosis fail. */
  level?: 'check' | 'info';
}

export interface DjobsDoctorReport {
  version?: string | null;
  checks: DjobsDoctorCheck[];
}
""",
    )
    tasks = ROOT / "vscode-ext/src/tasksProvider.ts"
    if tasks.exists():
        tasks.unlink()
    update_versions()
    update_docs_and_tests()


if __name__ == "__main__":
    main()
