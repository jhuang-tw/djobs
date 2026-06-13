import * as vscode from 'vscode';
import { DjobsClient } from './djobsClient';
import { DjobsStatus, DjobsTask, JobStatus } from './types';

// ── public types ────────────────────────────────────────────────────

export type DashItem =
  | WorkflowGroup | ActionGroup | CompletedSummary | CompletedGroup
  | TaskItem | EvidenceItem | CardItem | HintItem;

// ── provider ────────────────────────────────────────────────────────

export class DjobsTasksProvider implements vscode.TreeDataProvider<DashItem> {
  private readonly emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.emitter.event;
  private snapshot: DjobsStatus | undefined;
  private lastError: string | undefined;

  constructor(private readonly client: DjobsClient) {}

  async refresh(): Promise<void> {
    try {
      this.snapshot = await this.client.status();
      this.lastError = undefined;
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
      this.snapshot = undefined;
    }
    this.emitter.fire();
  }

  getTreeItem(el: DashItem): vscode.TreeItem { return el; }

  getChildren(element?: DashItem): DashItem[] {
    // ── evidence leaf ──
    if (element instanceof TaskItem && element.task.evidence) {
      return [new EvidenceItem(element.task.evidence)];
    }

    // ── completed type → tasks ──
    if (element instanceof CompletedGroup) {
      return element.tasks.map((t, i) => new TaskItem(t, element.commonPrefix, i));
    }

    // ── completed summary → type groups ──
    if (element instanceof CompletedSummary) {
      return [...groupByType(element.tasks).entries()]
        .map(([type, group]) => new CompletedGroup(type, group));
    }

    // ── action group → tasks (failed first) ──
    if (element instanceof ActionGroup) {
      const sorted = [...element.tasks].sort((a, b) => {
        const aFailed = a.status === 'failed' || a.status === 'dead_lettered' ? 0 : 1;
        const bFailed = b.status === 'failed' || b.status === 'dead_lettered' ? 0 : 1;
        return aFailed - bFailed;
      });
      return sorted.map((t, i) => new TaskItem(t, element.commonPrefix, i));
    }

    // ── workflow group → action groups + completed ──
    if (element instanceof WorkflowGroup) {
      return buildWorkflowChildren(element.tasks);
    }

    // ── root level ──
    if (this.lastError) {
      return [card('warning', 'djobs unavailable', this.lastError)];
    }
    if (!this.snapshot) {
      return [card('sync', 'Loading…')];
    }

    const tasks = this.snapshot.tasks;
    if (tasks.length === 0) {
      const options = this.client.getViewOptions();
      const active = options.showCompleted ? '' : 'active ';
      if (options.scope === 'currentWorkspace') {
        return [
          card('inbox', `No ${active}tasks for this workspace`),
          hint('djobs records work when an MCP-enabled agent calls enqueue_task or resume_session.'),
          hint(options.showCompleted
            ? 'Completed tasks are shown because showCompleted is on.'
            : 'Completed tasks are hidden, not deleted. Turn on djobs.showCompleted to inspect them.'),
          hint('Use the globe toolbar button to show all workspaces.'),
        ];
      }
      return [
        card('inbox', `No ${active}tasks in the queue`),
        hint(options.showCompleted
          ? 'Use Durable Coder agent on a multi-file task.'
          : 'Completed tasks are hidden by default.'),
      ];
    }

    return buildRootLevel(tasks, this.client.getViewOptions().scope);
  }

  getIncompleteCount(): number {
    return this.snapshot?.tasks.filter((t) => !isTerminal(t.status)).length ?? 0;
  }

  getVisibleWorkflowCorrelationIds(): string[] {
    return [...groupByCorrelation(this.snapshot?.tasks ?? []).keys()];
  }
}

// ── root builder ────────────────────────────────────────────────────

// Lookup of every task in the current snapshot, rebuilt each render so the
// tooltip can resolve depends_on ids to readable labels and block state.
const taskById = new Map<string, DjobsTask>();

function buildRootLevel(tasks: DjobsTask[], scope: 'currentWorkspace' | 'allWorkspaces'): DashItem[] {
  const items: DashItem[] = [];

  taskById.clear();
  for (const t of tasks) {
    taskById.set(t.id, t);
  }

  // Group by workflow (correlation_id)
  const workflows = groupByCorrelation(tasks);

  if (workflows.size === 1) {
    // Single workflow: flatten, no extra nesting
    const [, wfTasks] = [...workflows.entries()][0];
    items.push(...buildWorkflowChildren(wfTasks));
  } else {
    // Multiple workflows
    for (const [cid, wfTasks] of workflows) {
      items.push(new WorkflowGroup(cid, wfTasks));
    }
  }

  // Quick stats
  const scopeLabel = scope === 'currentWorkspace' ? 'current workspace' : 'all workspaces';
  items.push(hint(
    `Total: ${tasks.length} task(s) across ${workflows.size} workflow(s) in ${scopeLabel}`,
  ));

  return items;
}

function buildWorkflowChildren(tasks: DjobsTask[]): DashItem[] {
  const items: DashItem[] = [];

  const resumable = tasks.filter((t) =>
    t.status === 'pending' || t.status === 'running'
    || t.status === 'retry_scheduled',
  );
  if (resumable.length > 0) {
    for (const [type, group] of groupByType(resumable)) {
      items.push(new ActionGroup(type, group));
    }
  }

  const failed = tasks.filter((t) => t.status === 'failed');
  const deadLettered = tasks.filter((t) => t.status === 'dead_lettered');
  const stuck = failed.length + deadLettered.length;
  if (stuck > 0) {
    const parts: string[] = [];
    if (failed.length) { parts.push(`${failed.length} failed`); }
    if (deadLettered.length) { parts.push(`${deadLettered.length} dead-lettered`); }
    const firstError = (failed[0] ?? deadLettered[0])?.last_error;
    const errorHint = firstError ? ` — ${firstError}` : '';
    items.push(card('error', `${stuck} task(s) need attention`,
      parts.join(', ') + errorHint));
  }

  // Surface abandoned-looking work even when a single workflow is flattened
  // (no WorkflowGroup header to carry the badge). Points to the archive action.
  const stale = staleCount(tasks);
  if (stale > 0) {
    const oldest = Math.max(
      ...tasks.filter(isStale).map((t) => ageInDays(t.created_at) ?? 0),
    );
    items.push(card(
      'warning',
      `${stale} stale task(s) — older than ${STALE_AFTER_DAYS} days`,
      `Oldest is ${oldest}d old. If this workflow was abandoned, archive it `
        + `(right-click the workflow or run "djobs archive-workflow") instead of resuming.`,
    ));
  }

  const succeeded = tasks.filter((t) => t.status === 'succeeded');
  if (succeeded.length > 0) {
    items.push(new CompletedSummary(succeeded));
  }

  if (resumable.length === 0 && stuck === 0 && succeeded.length > 0) {
    items.unshift(card('check', 'All clear — nothing to do'));
  }

  return items;
}

// ── tree items ──────────────────────────────────────────────────────

export class WorkflowGroup extends vscode.TreeItem {
  constructor(readonly correlationId: string, readonly tasks: DjobsTask[]) {
    const label = friendlyWorkflow(correlationId);
    const resumable = tasks.filter((t) => !isTerminal(t.status)).length;
    const done = tasks.filter((t) => t.status === 'succeeded').length;
    const stale = staleCount(tasks);
    super(label, vscode.TreeItemCollapsibleState.Expanded);
    this.iconPath = workflowIcon(tasks);
    this.description = `${done}/${tasks.length} done`
      + (resumable > 0 ? ` · ${resumable} resumable` : '')
      + (stale > 0 ? ` · ⚠ ${stale} stale` : '');
    this.tooltip = [
      `Workflow: ${correlationId}`,
      `Progress: ${done}/${tasks.length}`,
      resumable > 0 ? `Resumable: ${resumable}` : 'All clear',
      stale > 0
        ? `⚠ ${stale} task(s) stale (>${STALE_AFTER_DAYS}d) — right-click to archive if abandoned`
        : undefined,
    ].filter((l) => l !== undefined).join('\n');
    this.contextValue = 'workflowGroup';
  }
}

export class ActionGroup extends vscode.TreeItem {
  readonly commonPrefix: string;
  constructor(readonly actionType: string, readonly tasks: DjobsTask[]) {
    const action = friendlyAction(actionType);
    const n = tasks.length === 1 ? '1 task' : `${tasks.length} tasks`;
    super(`${action} → ${n}`, vscode.TreeItemCollapsibleState.Collapsed);
    this.commonPrefix = findCommonPrefix(tasks);
    this.iconPath = new vscode.ThemeIcon('debug-restart');
    this.description = '▶ resume all, or expand to pick start';
    this.contextValue = 'actionGroup';
  }
}

export class CompletedSummary extends vscode.TreeItem {
  constructor(readonly tasks: DjobsTask[]) {
    const n = tasks.length;
    super(`${n} task${n === 1 ? '' : 's'} completed`,
      vscode.TreeItemCollapsibleState.Collapsed);
    this.iconPath = new vscode.ThemeIcon('pass');
    this.description = uniqueTypes(tasks);
    this.contextValue = 'completedSummary';
  }
}

export class CompletedGroup extends vscode.TreeItem {
  readonly commonPrefix: string;
  constructor(readonly actionType: string, readonly tasks: DjobsTask[]) {
    const action = friendlyAction(actionType);
    const n = tasks.length === 1 ? '1 task' : `${tasks.length} tasks`;
    super(`${action} → ${n} done`, vscode.TreeItemCollapsibleState.Collapsed);
    this.commonPrefix = findCommonPrefix(tasks);
    this.iconPath = new vscode.ThemeIcon('pass');
    this.description = tasks[0]?.updated_at
      ? relativeTime(tasks[0].updated_at) : '';
    this.contextValue = 'completedGroup';
  }
}

export class TaskItem extends vscode.TreeItem {
  constructor(
    readonly task: DjobsTask,
    commonPrefix: string,
    readonly index: number,
  ) {
    const hasEvidence = !!task.evidence;
    super(
      `${index + 1}. ${shortLabel(task, commonPrefix)}`,
      hasEvidence
        ? vscode.TreeItemCollapsibleState.Collapsed
        : vscode.TreeItemCollapsibleState.None,
    );
    this.description = taskDescription(task);
    this.tooltip = taskTooltip(task);
    this.iconPath = isStale(task) ? new vscode.ThemeIcon('warning') : statusIcon(task.status);
    this.contextValue = 'task';
  }
}

class EvidenceItem extends vscode.TreeItem {
  constructor(evidence: string) {
    super(evidence, vscode.TreeItemCollapsibleState.None);
    this.iconPath = new vscode.ThemeIcon('note');
    this.contextValue = 'evidence';
  }
}

class CardItem extends vscode.TreeItem {
  constructor(icon: string, label: string, detail?: string, command?: vscode.Command) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.iconPath = new vscode.ThemeIcon(icon);
    this.tooltip = detail ?? label;
    if (detail) { this.description = detail.split('\n')[0]; }
    if (command) { this.command = command; }
    this.contextValue = 'card';
  }
}

class HintItem extends vscode.TreeItem {
  constructor(text: string) {
    super(text, vscode.TreeItemCollapsibleState.None);
    this.contextValue = 'hint';
  }
}

function card(icon: string, label: string, detail?: string, command?: vscode.Command): CardItem {
  return new CardItem(icon, label, detail, command);
}
function hint(text: string): HintItem { return new HintItem(text); }

// ── labels ──────────────────────────────────────────────────────────

function taskDescription(task: DjobsTask): string {
  if (task.evidence) {
    return `✓ ${truncate(task.evidence, 40)}`;
  }
  if (isBlocked(task)) {
    return `⛔ blocked by ${dependencyLabel(blockingDeps(task)[0])}`;
  }
  if (isStale(task)) {
    const age = ageInDays(task.created_at);
    return `⚠ stale · ${age}d old — archive if abandoned`;
  }
  return task.created_at ? relativeTime(task.created_at) : '';
}

// A task is blocked when it is not yet terminal and at least one dependency
// has not succeeded.
function isBlocked(task: DjobsTask): boolean {
  return !isTerminal(task.status) && blockingDeps(task).length > 0;
}

function blockingDeps(task: DjobsTask): string[] {
  const deps = task.depends_on;
  if (!deps || deps.length === 0) {
    return [];
  }
  return deps.filter((id) => {
    const dep = taskById.get(id);
    return !dep || dep.status !== 'succeeded';
  });
}

function taskTooltip(task: DjobsTask): string {
  const payload = parsePayload(task.payload_json);
  const file = typeof payload.file === 'string' ? payload.file : undefined;
  const summary = firstString(payload, ['summary', 'title', 'name', 'description']);
  const why = firstString(payload, ['why', 'reason', 'rationale']);
  const condition = firstString(payload, ['condition', 'when', 'requires']);
  const action = friendlyAction(task.type);
  const blockedBy = blockedByLine(task);
  const staleLine = isStale(task)
    ? `⚠ Stale: incomplete for ${ageInDays(task.created_at)} days — archive the workflow if it was abandoned`
    : undefined;
  return [
    summary ? `What: ${summary}` : `What: ${action}${file ? ' on ' + file : ''}`,
    why ? `Why: ${why}` : undefined,
    condition ? `Condition: ${condition}` : undefined,
    staleLine,
    blockedBy,
    `Status: ${task.status}`,
    `Type: ${task.type}`,
    `Task ID: ${task.id}`,
    task.correlation_id ? `Workflow: ${task.correlation_id}` : undefined,
    `Attempt: ${task.attempt ?? 0}/${task.max_attempts ?? 0}`,
    task.evidence ? `Evidence: ${task.evidence}` : undefined,
    task.last_error ? `Error: ${task.last_error}` : undefined,
  ].filter((l) => l !== undefined).join('\n');
}

// Build a 'Blocked by' / 'Depends on' tooltip line from depends_on ids,
// resolving each id to a readable label and flagging unfinished blockers.
function blockedByLine(task: DjobsTask): string | undefined {
  const deps = task.depends_on;
  if (!deps || deps.length === 0) {
    return undefined;
  }
  const pending = blockingDeps(task);
  if (pending.length > 0) {
    return `Blocked by: ${pending.map(dependencyLabel).join(', ')}`;
  }
  return `Depends on: ${deps.map(dependencyLabel).join(', ')} (all done)`;
}

function dependencyLabel(id: string): string {
  const dep = taskById.get(id);
  if (!dep) {
    return id.slice(0, 8);
  }
  const payload = parsePayload(dep.payload_json);
  const summary = firstString(payload, ['summary', 'title', 'name', 'description']);
  return summary ? truncate(summary, 40) : friendlyAction(dep.type);
}

function shortLabel(task: DjobsTask, commonPrefix: string): string {
  const payload = parsePayload(task.payload_json);
  const file = typeof payload.file === 'string'
    ? normalize(payload.file) : undefined;
  if (file) {
    const short = commonPrefix && file.startsWith(commonPrefix)
      ? file.slice(commonPrefix.length) : file;
    return short || file;
  }
  // No file path — fall back to a human-readable payload field before
  // resorting to the opaque task id.
  const summary = firstString(payload, ['summary', 'title', 'name', 'description']);
  if (summary) {
    return truncate(summary, 60);
  }
  // Still nothing — humanize the task type rather than show a bare id.
  if (task.type) {
    return `${friendlyAction(task.type)} (${task.id.slice(0, 8)})`;
  }
  return `task ${task.id.slice(0, 8)}`;
}

function firstString(
  payload: Record<string, unknown>,
  keys: string[],
): string | undefined {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

function friendlyWorkflow(cid: string): string {
  if (!cid) { return '(no workflow)'; }
  const parts = normalize(cid).split('/').filter(Boolean);
  if (parts.length >= 2) { return parts.slice(-2).join('/'); }
  return parts[parts.length - 1] || cid;
}

function friendlyAction(taskType: string): string {
  const map: Record<string, string> = {
    'add-docstrings': 'Add docstrings',
    'add-docstring': 'Add docstrings',
    'add-type-hints': 'Add type hints',
    'refactor': 'Refactor',
    'migrate': 'Migrate',
    'test': 'Add tests',
    'fix': 'Fix',
    'review': 'Review',
    'milestone': 'Milestone',
    'roadmap': 'Roadmap',
    'docs': 'Docs',
  };
  return map[taskType] ?? humanizeType(taskType);
}

function humanizeType(taskType: string): string {
  if (!taskType) { return taskType; }
  const words = taskType.replace(/[-_]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

// ── helpers ─────────────────────────────────────────────────────────

function normalize(s: string): string { return s.replace(/\\/g, '/'); }

function isTerminal(status: JobStatus): boolean {
  return status === 'succeeded' || status === 'failed'
    || status === 'dead_lettered';
}

function workflowIcon(tasks: DjobsTask[]): vscode.ThemeIcon {
  if (tasks.some((t) => t.status === 'running')) {
    return new vscode.ThemeIcon('sync~spin');
  }
  if (tasks.some((t) => t.status === 'failed')) {
    return new vscode.ThemeIcon('error');
  }
  const allDone = tasks.every((t) => t.status === 'succeeded');
  if (allDone) { return new vscode.ThemeIcon('pass'); }
  return new vscode.ThemeIcon('folder');
}

function relativeTime(iso: string): string {
  try {
    const ms = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(ms / 60_000);
    if (mins < 1) { return 'just now'; }
    if (mins < 60) { return `${mins}m ago`; }
    const hours = Math.floor(mins / 60);
    if (hours < 24) { return `${hours}h ago`; }
    return `${Math.floor(hours / 24)}d ago`;
  } catch { return ''; }
}

// Keep in sync with djobs.mcp_server._STALE_AFTER_DAYS so the sidebar and the
// agent's resume_session hints agree on what "stale" means.
const STALE_AFTER_DAYS = 7;

function ageInDays(iso: string | undefined): number | undefined {
  if (!iso) { return undefined; }
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) { return undefined; }
  return Math.floor((Date.now() - t) / 86_400_000);
}

// A task is stale when it has sat incomplete past the threshold — a signal that
// the workflow may have been abandoned and is a candidate for archiving rather
// than resuming.
function isStale(task: DjobsTask): boolean {
  if (isTerminal(task.status)) { return false; }
  const age = ageInDays(task.created_at);
  return age !== undefined && age >= STALE_AFTER_DAYS;
}

function staleCount(tasks: DjobsTask[]): number {
  return tasks.filter(isStale).length;
}

function statusIcon(status: JobStatus): vscode.ThemeIcon {
  switch (status) {
    case 'running': return new vscode.ThemeIcon('sync~spin');
    case 'pending': return new vscode.ThemeIcon('clock');
    case 'retry_scheduled': return new vscode.ThemeIcon('history');
    case 'failed': return new vscode.ThemeIcon('error');
    case 'dead_lettered': return new vscode.ThemeIcon('archive');
    case 'succeeded': return new vscode.ThemeIcon('pass');
    default: return new vscode.ThemeIcon('circle-outline');
  }
}

function parsePayload(json: string | undefined): Record<string, unknown> {
  if (!json) { return {}; }
  try {
    const p = JSON.parse(json);
    return typeof p === 'object' && p !== null
      ? p as Record<string, unknown> : {};
  } catch { return {}; }
}

function uniqueTypes(tasks: DjobsTask[]): string {
  return [...new Set(tasks.map((t) => t.type))].join(', ');
}

function findCommonPrefix(tasks: DjobsTask[]): string {
  const paths = tasks
    .map((t) => {
      const p = parsePayload(t.payload_json);
      return typeof p.file === 'string' ? normalize(p.file) : undefined;
    })
    .filter((f): f is string => f !== undefined);
  if (paths.length < 2) { return ''; }
  const parts0 = paths[0].split('/');
  let common = 0;
  for (let i = 0; i < parts0.length - 1; i++) {
    if (paths.every((p) => p.split('/')[i] === parts0[i])) {
      common = i + 1;
    } else { break; }
  }
  return common > 0 ? parts0.slice(0, common).join('/') + '/' : '';
}

function groupByType(tasks: DjobsTask[]): Map<string, DjobsTask[]> {
  const map = new Map<string, DjobsTask[]>();
  for (const t of tasks) {
    const list = map.get(t.type);
    if (list) { list.push(t); } else { map.set(t.type, [t]); }
  }
  return map;
}

function groupByCorrelation(tasks: DjobsTask[]): Map<string, DjobsTask[]> {
  const map = new Map<string, DjobsTask[]>();
  for (const t of tasks) {
    const key = t.correlation_id ?? '(none)';
    const list = map.get(key);
    if (list) { list.push(t); } else { map.set(key, [t]); }
  }
  return map;
}
