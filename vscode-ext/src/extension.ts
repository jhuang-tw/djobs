import * as https from 'https';
import * as vscode from 'vscode';
import { DjobsClient } from './djobsClient';
import { DjobsTasksProvider, TaskItem, ActionGroup, WorkflowGroup } from './tasksProvider';

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    return;
  }

  const client = new DjobsClient(workspaceRoot);
  const provider = new DjobsTasksProvider(client);

  // Register the djobs MCP server natively (VS Code 1.101+) so a VS Code user
  // never hand-edits .vscode/mcp.json. Defer to an existing mcp.json djobs entry
  // to avoid a duplicate server; re-provide whenever the runtime, queue
  // location, or interpreter changes. The install-mcp CLI path remains for
  // non-VS-Code agents and is the fallback when this API is unavailable.
  const nativeMcp = typeof vscode.lm.registerMcpServerDefinitionProvider === 'function';
  const mcpDidChange = new vscode.EventEmitter<void>();
  if (nativeMcp) {
    context.subscriptions.push(
      mcpDidChange,
      vscode.lm.registerMcpServerDefinitionProvider('djobsServerProvider', {
        onDidChangeMcpServerDefinitions: mcpDidChange.event,
        provideMcpServerDefinitions: async () => {
          // Defer to a HEALTHY committed mcp.json djobs entry so we never run a
          // duplicate server (committed/shared configs win). If that entry is
          // dead (e.g. it points at a deleted .venv), provide natively so djobs
          // still works; the setup flow separately offers to repair the file.
          if (client.hasMcpJsonDjobsServer() && !client.detectDeadMcpInterpreter()) {
            return [];
          }
          // Nothing to offer until the runtime is launchable; the setup flow
          // fires the change emitter once it installs djobs so we re-provide.
          if (!(await client.isPackageInstalled())) {
            return [];
          }
          const launch = client.mcpServerLaunch();
          const version = await client.installedVersion();
          const server = new vscode.McpStdioServerDefinition(
            'djobs', launch.command, launch.args, launch.env, version,
          );
          server.cwd = vscode.Uri.file(launch.cwd);
          return [server];
        },
      }),
    );
  }

  // Status bar badge
  const statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left, 50,
  );
  statusBarItem.command = 'workbench.view.extension.djobs';
  statusBarItem.tooltip = 'Open djobs sidebar';
  context.subscriptions.push(statusBarItem);

  const doctorChannel = vscode.window.createOutputChannel('djobs');
  context.subscriptions.push(doctorChannel);

  function updateStatusBar(): void {
    const paused = provider.isPaused();
    void vscode.commands.executeCommand('setContext', 'djobs.paused', paused);
    const count = provider.getIncompleteCount();
    if (paused) {
      statusBarItem.text = '$(circle-slash) djobs: paused';
      statusBarItem.tooltip = 'djobs is paused — agents will not resume/enqueue. Open the sidebar to resume.';
      statusBarItem.show();
    } else if (count > 0) {
      statusBarItem.text = `$(debug-restart) djobs: ${count} pending`;
      statusBarItem.tooltip = 'Open djobs sidebar';
      statusBarItem.show();
    } else {
      statusBarItem.hide();
    }
  }

  // Wrap refresh to also update the status bar, and guard against overlapping
  // runs: each refresh spawns a Python child process, and the auto-refresh
  // timer + config watcher can otherwise fire concurrently (slow on Windows).
  const originalRefresh = provider.refresh.bind(provider);
  let refreshing = false;
  provider.refresh = async () => {
    if (refreshing) {
      return;
    }
    refreshing = true;
    try {
      await originalRefresh();
      updateStatusBar();
    } finally {
      refreshing = false;
    }
  };

  // Set initial scope context for conditional icon
  const initialScope = vscode.workspace.getConfiguration('djobs').get<string>('scope', 'currentWorkspace');
  vscode.commands.executeCommand('setContext', 'djobs.scope', initialScope);
  vscode.commands.executeCommand('setContext', 'djobs.promptActionsEnabled', promptActionsEnabled());

  const treeView = vscode.window.createTreeView('djobsTasks', {
    treeDataProvider: provider,
    showCollapseAll: true,
  });

  const refresh = vscode.commands.registerCommand('djobs.refresh', async () => {
    await provider.refresh();
  });

  const copyTaskId = vscode.commands.registerCommand('djobs.copyTaskId', async (item?: TaskItem) => {
    if (!item) {
      return;
    }
    await vscode.env.clipboard.writeText(item.task.id);
    vscode.window.showInformationMessage(`Copied djobs task ID: ${item.task.id.slice(0, 8)}`);
  });

  const inspectTask = vscode.commands.registerCommand('djobs.inspectTask', async (item?: TaskItem) => {
    if (!item) {
      return;
    }
    const document = await vscode.workspace.openTextDocument({
      content: JSON.stringify(item.task, null, 2),
      language: 'json',
    });
    await vscode.window.showTextDocument(document, { preview: true });
  });

  const viewTaskHistory = vscode.commands.registerCommand('djobs.viewTaskHistory', async (item?: TaskItem) => {
    if (!item) {
      return;
    }
    const content = await client.taskHistory(item.task);
    const document = await vscode.workspace.openTextDocument({ content, language: 'json' });
    await vscode.window.showTextDocument(document, { preview: true });
  });

  const skipTask = vscode.commands.registerCommand('djobs.skipTask', async (item?: TaskItem) => {
    if (!item) { return; }
    const file = taskDisplayName(item.task);
    const choice = await vscode.window.showWarningMessage(
      `Skip task: ${file}? This marks it done without editing.`,
      'Skip', 'Cancel',
    );
    if (choice !== 'Skip') { return; }
    await client.skipTask(item.task, `Skipped from VS Code sidebar: ${item.task.id}`);
    vscode.window.showInformationMessage(`Skipped: ${file}`);
    await provider.refresh();
  });

  const archiveTask = vscode.commands.registerCommand('djobs.archiveTask', async (item?: TaskItem) => {
    if (!item) { return; }
    const label = taskDisplayName(item.task);
    const choice = await vscode.window.showWarningMessage(
      `Archive task: ${label}? It will be hidden from active views; audit history is kept.`,
      'Archive', 'Cancel',
    );
    if (choice !== 'Archive') { return; }
    await client.archiveTask(item.task, `Archived from VS Code sidebar: ${item.task.id}`);
    vscode.window.showInformationMessage(`Archived: ${label}`);
    await provider.refresh();
  });

  const deleteTask = vscode.commands.registerCommand('djobs.deleteTask', async (item?: TaskItem) => {
    if (!item) { return; }
    const label = taskDisplayName(item.task);
    const choice = await vscode.window.showWarningMessage(
      `Delete task: ${label}? This permanently removes the task and its audit events. Archive it instead if you want history.`,
      { modal: true },
      'Delete',
    );
    if (choice !== 'Delete') { return; }
    await client.deleteTask(item.task);
    vscode.window.showInformationMessage(`Deleted: ${label}`);
    await provider.refresh();
  });

  const archiveWorkflow = vscode.commands.registerCommand('djobs.archiveWorkflow', async (item?: WorkflowGroup | ActionGroup) => {
    let correlationId: string | undefined;
    let label = 'current workspace';

    if (item instanceof WorkflowGroup) {
      correlationId = item.correlationId;
      label = `"${item.label}"`;
    } else if (item instanceof ActionGroup && item.tasks.length > 0) {
      correlationId = item.tasks[0].correlation_id ?? workspaceRoot;
      label = `workflow "${workflowLabel(correlationId)}"`;
    } else {
      const workflows = provider.getVisibleWorkflowCorrelationIds();
      if (workflows.length === 1) {
        correlationId = workflows[0];
        label = `workflow "${workflowLabel(correlationId)}"`;
      } else if (workflows.length > 1) {
        const picked = await vscode.window.showQuickPick(
          workflows.map((id) => ({ label: workflowLabel(id), description: id, id })),
          { placeHolder: 'Archive which djobs workflow?' },
        );
        if (!picked) {
          return;
        }
        correlationId = picked.id;
        label = `workflow "${picked.label}"`;
      } else {
        correlationId = workspaceRoot;
      }
    }

    const choice = await vscode.window.showWarningMessage(
      `Archive all non-terminal tasks in ${label}? Completed tasks and audit history are kept.`,
      { modal: true },
      'Archive',
    );
    if (choice !== 'Archive') {
      return;
    }
    const count = await client.archiveByCorrelation(correlationId, 'Archived from VS Code sidebar');
    vscode.window.showInformationMessage(`Archived ${count} task(s).`);
    await provider.refresh();
  });

  const promptFinishWorkflow = vscode.commands.registerCommand(
    'djobs.promptFinishWorkflow',
    async (item?: WorkflowGroup | ActionGroup | TaskItem) => {
      if (!promptActionsEnabled()) {
        const enable = 'Enable prompt actions';
        const selected = await vscode.window.showInformationMessage(
          'djobs prompt actions are disabled. Enable them for this workspace?',
          enable,
          'Cancel',
        );
        if (selected !== enable) {
          return;
        }
        await setPromptActionsEnabled(true);
      }
      const correlationId = workflowCorrelationId(item, workspaceRoot);
      const startTaskId = item instanceof TaskItem ? item.task.id : undefined;
      await openChatQuery(buildFinishWorkflowQuery(correlationId, startTaskId));
    },
  );

  const enablePromptActions = vscode.commands.registerCommand('djobs.enablePromptActions', async () => {
    await setPromptActionsEnabled(true);
    vscode.window.showInformationMessage(
      'djobs prompt actions enabled for this workspace. Use the play actions in the sidebar to prompt an agent manually.',
    );
  });

  const disablePromptActions = vscode.commands.registerCommand('djobs.disablePromptActions', async () => {
    await setPromptActionsEnabled(false);
    vscode.window.showInformationMessage('djobs prompt actions disabled for this workspace.');
  });

  const toggleScope = vscode.commands.registerCommand('djobs.toggleScope', async () => {
    const config = vscode.workspace.getConfiguration('djobs');
    const current = config.get<string>('scope', 'currentWorkspace');
    const next = current === 'allWorkspaces' ? 'currentWorkspace' : 'allWorkspaces';
    await config.update('scope', next, vscode.ConfigurationTarget.Workspace);
    await vscode.commands.executeCommand('setContext', 'djobs.scope', next);
    const label = next === 'allWorkspaces' ? 'All workspaces' : 'Current workspace';
    vscode.window.showInformationMessage(`djobs scope: ${label}`);
    await provider.refresh();
  });

  const toggleQueueLocation = vscode.commands.registerCommand('djobs.toggleQueueLocation', async () => {
    const config = vscode.workspace.getConfiguration('djobs');
    const current = config.get<string>('queueLocation', 'workspace');
    const next = current === 'global' ? 'workspace' : 'global';
    await config.update('queueLocation', next, vscode.ConfigurationTarget.Global);

    if (nativeMcp) {
      // The native server's DJOBS_DB follows the setting; re-provide so VS Code
      // restarts the agent against the newly selected queue. No JSON to write.
      mcpDidChange.fire();
      vscode.window.showInformationMessage(
        next === 'global'
          ? 'djobs now uses the shared global queue (~/.djobs/global.db). Your agent follows automatically.'
          : 'djobs now uses the per-workspace queue (djobs_mcp.db).',
      );
    } else if (next === 'global') {
      const wireUp = 'Wire up agent';
      const selected = await vscode.window.showInformationMessage(
        'djobs now reads the shared global queue (~/.djobs/global.db). '
          + 'Run "djobs install-mcp --global" in each project so the agent writes there too.',
        wireUp,
      );
      if (selected === wireUp) {
        try {
          await client.wireGlobalMcp();
          vscode.window.showInformationMessage(
            'djobs agent wired to the global queue. Reload the window if the MCP server was already running.',
          );
        } catch (error) {
          const detail = error instanceof Error ? error.message : String(error);
          vscode.window.showErrorMessage(
            `djobs: could not wire the global queue. ${detail} Try "djobs: Diagnose Setup".`,
          );
        }
      }
    } else {
      vscode.window.showInformationMessage('djobs now uses the per-workspace queue (djobs_mcp.db).');
    }
    await provider.refresh();
  });

  const diagnose = vscode.commands.registerCommand('djobs.diagnose', async () => {
    doctorChannel.clear();
    doctorChannel.show(true);
    doctorChannel.appendLine('Running djobs setup diagnostics…\n');
    try {
      const report = await client.doctor();
      let allOk = true;
      for (const check of report.checks) {
        const info = check.level === 'info';
        if (!check.ok && !info) {
          allOk = false;
        }
        const mark = check.ok ? 'OK  ' : info ? 'INFO' : 'FAIL';
        doctorChannel.appendLine(`  [${mark}] ${check.name}: ${check.detail}`);
      }
      doctorChannel.appendLine('');
      doctorChannel.appendLine(
        allOk ? 'All checks passed.' : 'Some checks failed — see the FAIL lines above.',
      );
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      doctorChannel.appendLine(`Could not run diagnostics: ${detail}`);
      doctorChannel.appendLine('');
      doctorChannel.appendLine('djobs may not be installed.');
      const setUp = 'Set up djobs';
      const choice = await vscode.window.showErrorMessage(
        'djobs may not be installed. Set it up now?',
        setUp,
      );
      if (choice === setUp) {
        await vscode.commands.executeCommand('djobs.setup');
      }
    }
  });

  const setup = vscode.commands.registerCommand('djobs.setup', async () => {
    await runDjobsSetup(client, provider, nativeMcp, mcpDidChange);
  });

  const pauseDjobs = vscode.commands.registerCommand('djobs.pause', async () => {
    try {
      await client.pause();
      vscode.window.showInformationMessage(
        'djobs paused. Agents will not resume or enqueue tasks until you resume djobs. No tasks were deleted.',
      );
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      await handlePauseCommandFailure(client, detail, true);
    }
    await provider.refresh();
  });

  const unpauseDjobs = vscode.commands.registerCommand('djobs.unpause', async () => {
    try {
      await client.unpause();
      vscode.window.showInformationMessage('djobs resumed. Agents can resume and enqueue tasks again.');
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      await handlePauseCommandFailure(client, detail, false);
    }
    await provider.refresh();
  });

  const configWatcher = vscode.workspace.onDidChangeConfiguration(async (event) => {
    if (event.affectsConfiguration('djobs')) {
      const newScope = vscode.workspace.getConfiguration('djobs').get<string>('scope', 'currentWorkspace');
      await vscode.commands.executeCommand('setContext', 'djobs.scope', newScope);
      await vscode.commands.executeCommand('setContext', 'djobs.promptActionsEnabled', promptActionsEnabled());
      if (nativeMcp && (
        event.affectsConfiguration('djobs.pythonPath')
        || event.affectsConfiguration('djobs.queueLocation')
        || event.affectsConfiguration('djobs.globalDbPath')
        || event.affectsConfiguration('djobs.dbPath')
      )) {
        // These determine the native server's launch command/env; re-provide.
        mcpDidChange.fire();
      }
      await provider.refresh();
    }
  });

  const intervalSeconds = vscode.workspace
    .getConfiguration('djobs')
    .get<number>('autoRefreshInterval', 5);
  const timer = setInterval(() => provider.refresh(), Math.max(1, intervalSeconds) * 1000);

  context.subscriptions.push(
    treeView,
    refresh,
    copyTaskId,
    inspectTask,
    viewTaskHistory,
    skipTask,
    archiveTask,
    deleteTask,
    archiveWorkflow,
    promptFinishWorkflow,
    enablePromptActions,
    disablePromptActions,
    toggleScope,
    toggleQueueLocation,
    diagnose,
    setup,
    pauseDjobs,
    unpauseDjobs,
    configWatcher,
    { dispose: () => clearInterval(timer) },
  );

  await provider.refresh();
  void maybeOfferMarketplaceExtensionUpdate(context);
  // Update the Python package FIRST, against whichever interpreter the sidebar
  // runs. A stale djobs (predating --global / doctor) would otherwise make the
  // wiring commands below fail. Only wire when the CLI is current.
  const cliReady = await maybeOfferUpdate(context, client, mcpDidChange);
  if (cliReady) {
    await maybeOfferSetup(context, client, provider, nativeMcp, mcpDidChange);
  }
}

export function deactivate(): void {}

/**
 * On activation, if djobs isn't usable yet, offer a single one-click setup. This
 * covers three failure modes so the user never hits a dead end:
 *   - djobs not installed at all,
 *   - the shared global queue isn't wired for this project, or
 *   - .vscode/mcp.json points the agent at an interpreter that no longer exists
 *     (e.g. a project `.venv` was deleted) — the cause of VS Code's native
 *     "command ... needed to run djobs was not found" error.
 * Skips silently once the user opts out per workspace; the `djobs.setup` command
 * remains available to repair things manually afterwards.
 */
async function maybeOfferSetup(
  context: vscode.ExtensionContext,
  client: DjobsClient,
  provider: DjobsTasksProvider,
  nativeMcp: boolean,
  mcpDidChange?: vscode.EventEmitter<void>,
): Promise<void> {
  const installed = await client.isPackageInstalled();
  // With native registration the agent is wired automatically (no mcp.json), so
  // the only reasons to prompt are a missing runtime or a dead, pre-existing
  // mcp.json entry that VS Code would otherwise error on.
  const needsWiring = !nativeMcp && client.isGlobalQueue() && !client.isGlobalMcpWired();
  const deadCommand = client.detectDeadMcpInterpreter();
  if (installed && !needsWiring && !deadCommand) {
    return;
  }

  const dismissKey = 'djobs.setup.dismissed';
  if (context.workspaceState.get<boolean>(dismissKey)) {
    return;
  }

  const setUp = !installed ? 'Set up djobs' : 'Re-wire djobs';
  const dontAsk = "Don't ask again";
  let selected: string | undefined;
  if (!installed) {
    selected = await vscode.window.showInformationMessage(
      'djobs isn\'t installed yet. Set it up (isolated global install) so the '
        + 'sidebar and your AI agent get crash-proof task memory?',
      setUp,
      dontAsk,
    );
  } else if (deadCommand) {
    selected = await vscode.window.showWarningMessage(
      `djobs' agent is wired to a missing interpreter (${deadCommand}). `
        + 'Re-wire it to the working djobs install?',
      setUp,
      dontAsk,
    );
  } else {
    selected = await vscode.window.showInformationMessage(
      'djobs uses a shared global queue. Wire this project\'s agent to it so '
        + 'tasks it creates show up everywhere?',
      setUp,
      dontAsk,
    );
  }

  if (selected === dontAsk) {
    await context.workspaceState.update(dismissKey, true);
    return;
  }
  if (selected !== setUp) {
    return;
  }

  await runDjobsSetup(client, provider, nativeMcp, mcpDidChange);
}

/**
 * Install djobs when missing and (re-)wire the agent's MCP launch command when
 * it points at the shared global queue but isn't connected yet, or at an
 * interpreter that no longer exists. Shared by the activation notice and the
 * manually invokable "Set up / Repair djobs" command so neither path dead-ends.
 * On failure, offers the diagnostics output as a follow-up action.
 */
async function runDjobsSetup(
  client: DjobsClient,
  provider: DjobsTasksProvider,
  nativeMcp: boolean,
  mcpDidChange?: vscode.EventEmitter<void>,
): Promise<void> {
  try {
    const installed = await client.isPackageInstalled();
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'Setting up djobs…' },
      async (progress) => {
        if (!installed) {
          progress.report({ message: 'Installing djobs…' });
          await client.installPackage();
        }
        // Re-evaluate after a possible install. With native registration the
        // agent is wired automatically, so only repair a dead, pre-existing
        // mcp.json entry (never create a new JSON file). Otherwise fall back to
        // wiring/repairing mcp.json via the CLI.
        if (nativeMcp) {
          if (client.detectDeadMcpInterpreter()) {
            progress.report({ message: 'Re-wiring the agent…' });
            await client.reWireMcp();
          }
        } else if (client.isGlobalQueue() && !client.isGlobalMcpWired()) {
          progress.report({ message: 'Wiring the agent…' });
          await client.wireGlobalMcp();
        } else if (client.detectDeadMcpInterpreter()) {
          progress.report({ message: 'Re-wiring the agent…' });
          await client.reWireMcp();
        }
      },
    );
    // Let VS Code pick up a freshly installed runtime / repaired wiring.
    mcpDidChange?.fire();
    vscode.window.showInformationMessage(
      nativeMcp
        ? 'djobs is set up. MCP tools are available, and the sidebar will show tasks when an agent records them.'
        : 'djobs is set up. Reload the window if the MCP server was already running; the sidebar will show tasks when an agent records them.',
    );
    await provider.refresh();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);

    // No Python runtime at all: the extension cannot silently install one
    // (that is a system change), so guide the user honestly instead of
    // pretending pipx/pip is available.
    if (detail === 'NO_PYTHON_RUNTIME') {
      const getUv = 'Get uv (no Python needed)';
      const getPython = 'Get Python';
      const choice = await vscode.window.showErrorMessage(
        'djobs needs a runtime to install its engine, but none was found on PATH. '
          + 'Install uv (a single binary that needs no pre-existing Python) or '
          + 'Python, then run "djobs: Set up / Repair djobs" again.',
        getUv,
        getPython,
      );
      if (choice === getUv) {
        await vscode.env.openExternal(
          vscode.Uri.parse('https://docs.astral.sh/uv/getting-started/installation/'),
        );
      } else if (choice === getPython) {
        await vscode.env.openExternal(
          vscode.Uri.parse('https://www.python.org/downloads/'),
        );
      }
      return;
    }

    if (detail.startsWith('PYTHON_TOO_OLD')) {
      const getUv = 'Get uv (recommended)';
      const getPython = 'Get Python 3.11+';
      const choice = await vscode.window.showErrorMessage(
        'djobs requires Python 3.11 or newer. The setup flow tried the available '
          + 'installers, but none could provide a compatible runtime. Install uv '
          + '(recommended; it can provision Python automatically) or Python 3.11+, '
          + 'then run "djobs: Set up / Repair djobs" again.',
        getUv,
        getPython,
      );
      if (choice === getUv) {
        await vscode.env.openExternal(
          vscode.Uri.parse('https://docs.astral.sh/uv/getting-started/installation/'),
        );
      } else if (choice === getPython) {
        await vscode.env.openExternal(
          vscode.Uri.parse('https://www.python.org/downloads/'),
        );
      }
      return;
    }

    const friendly = detail === 'INSTALL_VERIFY_FAILED'
      ? 'djobs installed but could not be launched afterward'
      : `djobs setup failed: ${detail}`;
    const diagnose = 'Diagnose Setup';
    const choice = await vscode.window.showErrorMessage(
      `${friendly}. Open diagnostics for details.`,
      diagnose,
    );
    if (choice === diagnose) {
      await vscode.commands.executeCommand('djobs.diagnose');
    }
  }
}

async function handlePauseCommandFailure(
  client: DjobsClient,
  detail: string,
  pause: boolean,
): Promise<void> {
  if (/invalid choice: '?(pause|unpause)'?|unrecognized arguments: (pause|unpause)/i.test(detail)) {
    const update = 'Update djobs';
    const selected = await vscode.window.showErrorMessage(
      'The djobs Python package is too old for the Pause/Resume command. Update it now?',
      update,
      'Cancel',
    );
    if (selected === update) {
      try {
        await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: 'Updating djobs…' },
          async () => client.updatePackage(),
        );
        if (pause) {
          await client.pause();
          vscode.window.showInformationMessage(
            'djobs updated and paused. Agents will not resume or enqueue tasks until you resume djobs.',
          );
        } else {
          await client.unpause();
          vscode.window.showInformationMessage('djobs updated and resumed.');
        }
      } catch (error) {
        const updateDetail = error instanceof Error ? error.message : String(error);
        vscode.window.showErrorMessage(`djobs update failed: ${updateDetail}`);
      }
    }
    return;
  }

  vscode.window.showErrorMessage(
    pause ? `djobs: could not pause. ${detail}` : `djobs: could not resume. ${detail}`,
  );
}

function workflowLabel(correlationId: string): string {
  const parts = correlationId.replace(/\\/g, '/').split('/').filter(Boolean);
  return parts.at(-1) ?? correlationId;
}

function taskDisplayName(task: { id: string; payload_json?: string; type?: string }): string {
  try {
    const payload = task.payload_json ? JSON.parse(task.payload_json) as Record<string, unknown> : {};
    for (const key of ['file', 'summary', 'title', 'name']) {
      const value = payload[key];
      if (typeof value === 'string' && value.trim()) {
        return value.trim();
      }
    }
  } catch {
    // Fall through to a stable short label.
  }
  return task.type ? `${task.type} ${task.id.slice(0, 8)}` : task.id.slice(0, 8);
}

function promptActionsEnabled(): boolean {
  return vscode.workspace
    .getConfiguration('djobs')
    .get<boolean>('promptActions.enabled', false);
}

async function setPromptActionsEnabled(enabled: boolean): Promise<void> {
  await vscode.workspace
    .getConfiguration('djobs')
    .update('promptActions.enabled', enabled, vscode.ConfigurationTarget.Workspace);
  await vscode.commands.executeCommand('setContext', 'djobs.promptActionsEnabled', enabled);
}

function workflowCorrelationId(
  item: WorkflowGroup | ActionGroup | TaskItem | undefined,
  workspaceRoot: string,
): string {
  if (item instanceof WorkflowGroup) {
    return item.correlationId;
  }
  if (item instanceof ActionGroup && item.tasks.length > 0) {
    return item.tasks[0].correlation_id ?? workspaceRoot;
  }
  if (item instanceof TaskItem) {
    return item.task.correlation_id ?? workspaceRoot;
  }
  return workspaceRoot;
}

function buildFinishWorkflowQuery(correlationId: string, startTaskId?: string): string {
  return [
    'Use djobs to finish the required unfinished work for this workflow.',
    '',
    `Workspace correlation_id: ${correlationId}`,
    startTaskId ? `Start with task ID: ${startTaskId}` : undefined,
    '',
    'Call djobs resume_session with that correlation_id. Continue runnable incomplete tasks until there is no required unfinished work left. Before redoing a task, inspect the current file state; if it is already done, call complete_task with evidence instead of editing again. For every task you finish, call complete_task with concise evidence. If a task is obsolete, ask before deleting it; archive abandoned workflow tasks instead of leaving them pending.',
  ].filter((line) => line !== undefined).join('\n');
}

async function openChatQuery(query: string): Promise<void> {
  try {
    await vscode.commands.executeCommand('workbench.action.chat.open', { query });
  } catch {
    await vscode.env.clipboard.writeText(query);
    vscode.window.showWarningMessage(
      'djobs could not open Chat automatically. The query was copied to the clipboard.',
    );
  }
}

/**
 * Compare two dotted version strings. Returns true when `a` is strictly older
 * than `b`. Non-numeric/suffix parts are ignored; missing parts count as 0.
 */
export function isOlderVersion(a: string, b: string): boolean {
  const parse = (v: string): number[] =>
    v.split('.').map((p) => parseInt(p, 10)).map((n) => (Number.isNaN(n) ? 0 : n));
  const av = parse(a);
  const bv = parse(b);
  const len = Math.max(av.length, bv.length);
  for (let i = 0; i < len; i++) {
    const ai = av[i] ?? 0;
    const bi = bv[i] ?? 0;
    if (ai !== bi) {
      return ai < bi;
    }
  }
  return false;
}

const MARKETPLACE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000;
const MARKETPLACE_QUERY_PATH = '/_apis/public/gallery/extensionquery?api-version=7.2-preview.1';
const MARKETPLACE_ITEM_FLAGS = 914;

interface MarketplaceVersionQuery {
  results?: Array<{
    extensions?: Array<{
      versions?: Array<{ version?: string }>;
    }>;
  }>;
}

/**
 * VS Code owns extension auto-update, but that can lag or be disabled. Check the
 * Marketplace at most once per day and point the user at the normal extension
 * update surfaces when a newer djobs extension exists. This never opens Chat,
 * copies text, or asks an agent to do anything.
 */
async function maybeOfferMarketplaceExtensionUpdate(
  context: vscode.ExtensionContext,
): Promise<void> {
  try {
    const pkg = context.extension.packageJSON as {
      name?: string;
      publisher?: string;
      version?: string;
    };
    const currentVersion = pkg.version;
    const extensionId = pkg.publisher && pkg.name ? `${pkg.publisher}.${pkg.name}` : undefined;
    if (!currentVersion || !extensionId) {
      return;
    }

    const now = Date.now();
    const lastCheckKey = 'djobs.marketplaceUpdate.lastChecked';
    const lastChecked = context.globalState.get<number>(lastCheckKey, 0);
    if (now - lastChecked < MARKETPLACE_CHECK_INTERVAL_MS) {
      return;
    }
    await context.globalState.update(lastCheckKey, now);

    const latestVersion = await latestMarketplaceVersion(extensionId);
    if (!latestVersion || !isOlderVersion(currentVersion, latestVersion)) {
      return;
    }

    const dismissKey = `djobs.marketplaceUpdate.dismissed.${latestVersion}`;
    if (context.globalState.get<boolean>(dismissKey)) {
      return;
    }

    const openMarketplace = 'Open Marketplace';
    const openExtensions = 'Open Extensions';
    const dontAsk = "Don't ask for this version";
    const selected = await vscode.window.showInformationMessage(
      `djobs extension v${latestVersion} is available on the VS Code Marketplace. `
        + `You have v${currentVersion}; VS Code auto-update may not have applied yet.`,
      openMarketplace,
      openExtensions,
      dontAsk,
    );
    if (selected === openMarketplace) {
      await vscode.env.openExternal(
        vscode.Uri.parse(`https://marketplace.visualstudio.com/items?itemName=${extensionId}`),
      );
    } else if (selected === openExtensions) {
      try {
        await vscode.commands.executeCommand('workbench.extensions.search', `@id:${extensionId}`);
      } catch {
        await vscode.env.openExternal(
          vscode.Uri.parse(`https://marketplace.visualstudio.com/items?itemName=${extensionId}`),
        );
      }
    } else if (selected === dontAsk) {
      await context.globalState.update(dismissKey, true);
    }
  } catch {
    // Update detection is best-effort; setup/sidebar must never fail because the
    // Marketplace is unavailable or a corporate proxy blocks the request.
  }
}

async function latestMarketplaceVersion(extensionId: string): Promise<string | undefined> {
  const body = JSON.stringify({
    filters: [{ criteria: [{ filterType: 7, value: extensionId }] }],
    flags: MARKETPLACE_ITEM_FLAGS,
  });
  const raw = await marketplacePost(body);
  if (!raw) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(raw) as MarketplaceVersionQuery;
    return parsed.results?.[0]?.extensions?.[0]?.versions?.[0]?.version;
  } catch {
    return undefined;
  }
}

function marketplacePost(body: string): Promise<string | undefined> {
  return new Promise((resolve) => {
    let finished = false;
    const finish = (value: string | undefined): void => {
      if (!finished) {
        finished = true;
        resolve(value);
      }
    };

    const request = https.request(
      {
        hostname: 'marketplace.visualstudio.com',
        path: MARKETPLACE_QUERY_PATH,
        method: 'POST',
        timeout: 5000,
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
          'User-Agent': 'djobs-vscode-extension',
        },
      },
      (response) => {
        if (!response.statusCode || response.statusCode < 200 || response.statusCode >= 300) {
          response.resume();
          finish(undefined);
          return;
        }
        response.setEncoding('utf8');
        let raw = '';
        response.on('data', (chunk: string | Buffer) => {
          raw += chunk.toString();
          if (raw.length > 1_000_000) {
            request.destroy();
            finish(undefined);
          }
        });
        response.on('end', () => finish(raw));
      },
    );
    request.on('timeout', () => {
      request.destroy();
      finish(undefined);
    });
    request.on('error', () => finish(undefined));
    request.write(body);
    request.end();
  });
}

/**
 * The VS Code extension auto-updates via the Marketplace, but the djobs Python
 * package (which provides the CLI/MCP server the extension drives) does not. If
 * the installed package is older than this extension, offer a one-click upgrade
 * so new commands the extension relies on actually exist. Opt-out is remembered
 * per installed version so a new release can prompt again.
 */
async function maybeOfferUpdate(
  context: vscode.ExtensionContext,
  client: DjobsClient,
  mcpDidChange?: vscode.EventEmitter<void>,
): Promise<boolean> {
  const extVersion = context.extension.packageJSON.version as string | undefined;
  const installedVer = await client.installedVersion();
  if (!installedVer) {
    // Nothing installed at all — let the setup flow handle it.
    if (!(await client.isPackageInstalled())) {
      return true;
    }
    // Package IS installed but too old to expose __version__ (pre-0.6).
    // Fall through treating it as "0.0.0" so the version comparison always
    // triggers the upgrade prompt — preventing new CLI flags from failing.
  }
  const installed = installedVer ?? '0.0.0';
  // Already current (or newer) — safe to run the wiring commands.
  if (!extVersion || !isOlderVersion(installed, extVersion)) {
    return true;
  }

  const update = 'Update djobs';
  const dontAsk = "Don't ask again";
  const dismissKey = `djobs.updateDismissed.${installed}`;
  if (context.globalState.get<boolean>(dismissKey)) {
    // User opted out for this version: don't nag, and don't run new-CLI commands
    // that this stale package would reject.
    return false;
  }

  const versionLabel = installedVer ? `v${installedVer}` : 'unknown version (likely outdated)';
  const selected = await vscode.window.showWarningMessage(
    `The djobs package (${versionLabel}) is older than the djobs extension `
      + `(v${extVersion}). Update it so the sidebar and agent work correctly?`,
    update,
    dontAsk,
  );

  if (selected === dontAsk) {
    await context.globalState.update(dismissKey, true);
    return false;
  }
  if (selected !== update) {
    return false;
  }

  try {
    const now = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'Updating djobs…' },
      async () => {
        await client.updatePackage();
        return client.installedVersion();
      },
    );
    vscode.window.showInformationMessage(
      now ? `djobs updated to v${now}. Reload the window if the MCP server was already running.`
        : 'djobs updated.',
    );
    // Re-provide so the native MCP server restarts on the new version.
    mcpDidChange?.fire();
    // Updated successfully — the CLI is now current, so wiring is safe.
    return !!now && !isOlderVersion(now, extVersion);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    const diagnose = 'Diagnose Setup';
    const choice = await vscode.window.showErrorMessage(
      `djobs update failed: ${detail}. Update manually with "pip install -U djobs".`,
      diagnose,
    );
    if (choice === diagnose) {
      await vscode.commands.executeCommand('djobs.diagnose');
    }
    return false;
  }
}
