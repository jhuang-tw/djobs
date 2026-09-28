import * as vscode from 'vscode';
import { djobsCommandLaunch, runDjobsCommand } from './commands';
import { DjobsClient } from './djobsClient';

type Exposure = 'resume' | 'evidence' | 'audit' | 'candidates';
type NodeKind = 'workspace' | 'folder' | 'memory' | 'notice';
type Json = Record<string, unknown>;
const SCHEME = 'djobs-memory-view';
const FOLDERS: Record<string, string> = {
  episodes: 'Episodes', facts: 'Facts', experiences: 'Verified Experiences',
  lessons: 'Lessons', skills: 'Skills and Candidates', imports: 'Imported Sessions',
};
const MAX_RESPONSE = 256 * 1024;
const EXPIRED = 'This memory view has expired. Refresh Memory Explorer and reopen the item.';

function object(value: unknown): Json {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Invalid memory response');
  }
  return value as Json;
}

export function parseMemoryResponse(text: string): Json {
  if (text.length > MAX_RESPONSE) { throw new Error('Memory response exceeds its bound'); }
  const result = object(JSON.parse(text));
  if (result.ok !== true) { throw new Error('Memory is unavailable; coding can continue'); }
  return result;
}

export function contextAddress(value: unknown, expectedRoot?: string): string {
  if (typeof value !== 'string' || value.length > 1600) {
    throw new Error('Invalid memory address');
  }
  const match = /^djobs:\/\/repo\/([^/]+)\/(?:(episodes|facts|experiences|lessons|skills|imports)\/(?:((?:mem|imp)_[0-9a-f]{32}))?)?$/.exec(value);
  if (!match || !decodeURIComponent(match[1]).startsWith('family:')) {
    throw new Error('Invalid memory address');
  }
  const root = `djobs://repo/${match[1]}/`;
  if (expectedRoot && root !== expectedRoot) { throw new Error('Memory repository changed'); }
  return root;
}

function label(value: unknown, fallback: string): string {
  return (typeof value === 'string' ? value : fallback)
    .replace(/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g, ' ').slice(0, 160);
}

export class MemoryNode extends vscode.TreeItem {
  constructor(
    public readonly kind: NodeKind,
    public readonly workspace: vscode.WorkspaceFolder,
    public readonly generation: number,
    title: string,
    public readonly address?: string,
    public readonly rootAddress?: string,
    public readonly record?: Json,
  ) {
    super(title, kind === 'workspace' || kind === 'folder'
      ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
    this.contextValue = kind === 'memory' ? 'djobsMemoryItem' : 'djobsMemoryGroup';
    if (kind === 'memory') {
      this.description = label(record?.status, 'data');
      this.tooltip = 'Stored content is untrusted data. Open to inspect native sources and lifecycle.';
      this.command = { command: 'djobs.memoryOpen', title: 'Inspect Memory', arguments: [this] };
    }
  }
}

/** Native read-through UI. The backend owns all eligibility and lifecycle decisions. */
export class MemoryExplorer implements vscode.TreeDataProvider<MemoryNode>,
  vscode.TextDocumentContentProvider, vscode.Disposable {
  private generation = 0;
  private issued = new WeakSet<MemoryNode>();
  private readonly changed = new vscode.EventEmitter<MemoryNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private readonly documentChanged = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.documentChanged.event;
  private readonly documents = new Map<string, string>();
  private serial = 0;
  private query = '';
  private exposure: Exposure = 'resume';
  private disposed = false;

  constructor(private readonly run: typeof runDjobsCommand = runDjobsCommand) {}

  dispose(): void {
    this.refresh();
    this.disposed = true;
    this.changed.dispose();
    this.documentChanged.dispose();
  }

  refresh(): void {
    this.generation += 1;
    this.issued = new WeakSet();
    const old = [...this.documents.keys()];
    this.documents.clear();
    old.forEach(uri => this.documentChanged.fire(vscode.Uri.parse(uri)));
    this.changed.fire(undefined);
  }

  releaseDocument(uri: vscode.Uri): void { this.documents.delete(uri.toString()); }

  provideTextDocumentContent(uri: vscode.Uri): string {
    return vscode.workspace.isTrusted ? this.documents.get(uri.toString()) ?? EXPIRED : EXPIRED;
  }

  getTreeItem(node: MemoryNode): vscode.TreeItem { return node; }

  private current(node: MemoryNode): boolean {
    return !this.disposed && vscode.workspace.isTrusted && this.issued.has(node)
      && node.generation === this.generation
      && Boolean(vscode.workspace.workspaceFolders?.some(
        folder => folder.uri.toString() === node.workspace.uri.toString(),
      ));
  }

  private node(kind: NodeKind, workspace: vscode.WorkspaceFolder, title: string,
    address?: string, root?: string, record?: Json): MemoryNode {
    const node = new MemoryNode(kind, workspace, this.generation, title, address, root, record);
    this.issued.add(node);
    return node;
  }

  private async read(node: MemoryNode, args: string[]): Promise<Json | undefined> {
    if (!this.current(node)) { return undefined; }
    const raw = await this.run(new DjobsClient(node.workspace.uri.fsPath), args, 5000);
    if (!this.current(node)) { return undefined; }
    return parseMemoryResponse(raw);
  }

  async getChildren(parent?: MemoryNode): Promise<MemoryNode[]> {
    if (this.disposed || !vscode.workspace.isTrusted) { return []; }
    if (!parent) {
      return (vscode.workspace.workspaceFolders ?? []).map(folder =>
        this.node('workspace', folder, `${folder.name} · ${this.exposure}`));
    }
    if (!this.current(parent) || !['workspace', 'folder'].includes(parent.kind)) { return []; }
    try {
      const args = ['memory', 'tree', this.query, '--depth', '0', '--exposure', this.exposure];
      if (parent.address) { args.push('--uri', parent.address); }
      const result = await this.read(parent, args);
      if (!result) { return []; }
      if (result.memory_store_status === 'not_initialized') {
        return [this.node('notice', parent.workspace, 'No memory store has been initialized')];
      }
      const root = contextAddress(result.root_uri, parent.rootAddress);
      if (result.root_uri !== root) { throw new Error('Expected repository root'); }
      if (parent.kind === 'workspace') {
        if (!Array.isArray(result.folders) || result.folders.length > 6) {
          throw new Error('Missing bounded context folders');
        }
        return result.folders.map(value => {
          const folder = object(value);
          const name = String(folder.name);
          if (!Object.prototype.hasOwnProperty.call(FOLDERS, name) || folder.uri !== `${root}${name}/`) {
            throw new Error('Invalid context folder');
          }
          return this.node('folder', parent.workspace, FOLDERS[name], String(folder.uri), root);
        });
      }
      if (!Array.isArray(result.memories) || result.memories.length > 20) {
        throw new Error('Missing bounded memories');
      }
      const nodes = result.memories.map(value => {
        const item = object(value);
        contextAddress(item.uri, root);
        if (typeof item.id !== 'string' || !/^(mem|imp)_[0-9a-f]{32}$/.test(item.id)
          || item.uri !== `${parent.address}${item.id}` || item.stored_content_is_data !== true) {
          throw new Error('Invalid context item');
        }
        return this.node('memory', parent.workspace, label(item.title ?? item.abstract, item.id),
          String(item.uri), root, item);
      });
      if (result.ambiguous === true) {
        nodes.push(this.node('notice', parent.workspace, 'Unresolved conflicts. Inspect audit exposure; no fact was chosen.'));
      }
      if (result.truncated === true) {
        nodes.push(this.node('notice', parent.workspace, 'More items omitted. Narrow the search.'));
      } else if (!nodes.length) {
        nodes.push(this.node('notice', parent.workspace, 'No visible items for this exposure'));
      }
      return nodes;
    } catch {
      return this.current(parent)
        ? [this.node('notice', parent.workspace, 'Memory unavailable. Refresh or inspect diagnostics.')]
        : [];
    }
  }

  private async display(node: MemoryNode, result: Json, title: string): Promise<void> {
    if (!this.current(node)) { return; }
    const text = JSON.stringify({ ...result, notice: 'UNTRUSTED DATA · no execution authority' }, null, 2);
    if (text.length > MAX_RESPONSE) { throw new Error('Memory display bound'); }
    while (this.documents.size >= 20) {
      const first = this.documents.keys().next().value as string;
      this.documents.delete(first);
      this.documentChanged.fire(vscode.Uri.parse(first));
    }
    const uri = vscode.Uri.parse(`${SCHEME}:/view-${++this.serial}/${title}.json`);
    this.documents.set(uri.toString(), text);
    const document = await vscode.workspace.openTextDocument(uri);
    if (!this.current(node)) {
      this.documents.delete(uri.toString());
      this.documentChanged.fire(uri);
      return;
    }
    await vscode.window.showTextDocument(document, { preview: true });
  }

  async open(node: MemoryNode, depth = 1): Promise<void> {
    if (!this.current(node) || node.kind !== 'memory' || ![0, 1, 2].includes(depth)) { return; }
    const result = await this.read(node, ['memory', 'show', node.address!, '--depth', String(depth)]);
    if (!result) { return; }
    if (Array.isArray(result.memories) && result.memories.length) {
      const item = object(result.memories[0]);
      if (item.id !== node.record?.id || item.uri !== node.address) {
        throw new Error('Memory identity changed');
      }
    }
    await this.display(node, result, `depth-${depth}`);
  }

  async selectDepth(node: MemoryNode): Promise<void> {
    if (!this.current(node)) { return; }
    const choice = await vscode.window.showQuickPick(
      ['0 · Abstract', '1 · Overview and lifecycle', '2 · Sources, relations and evidence'],
      { title: 'Memory content depth (does not change authority)' },
    );
    if (choice && this.current(node)) { await this.open(node, Number(choice[0])); }
  }

  async selectExposure(): Promise<void> {
    if (!vscode.workspace.isTrusted) { return; }
    const choice = await vscode.window.showQuickPick(
      ['resume', 'evidence', 'audit', 'candidates'],
      { title: 'Memory exposure · audit includes history; candidates are not accepted' },
    );
    if (choice && vscode.workspace.isTrusted) { this.exposure = choice as Exposure; this.refresh(); }
  }

  async search(): Promise<void> {
    if (!vscode.workspace.isTrusted) { return; }
    const query = await vscode.window.showInputBox({
      title: 'Search memory abstracts', value: this.query,
      validateInput: value => value.length > 500 ? 'Use at most 500 characters' : undefined,
    });
    if (query !== undefined && query.length <= 500 && vscode.workspace.isTrusted) {
      this.query = query; this.refresh();
    }
  }

  async review(node: MemoryNode): Promise<void> {
    if (!this.current(node) || node.kind !== 'memory') { return; }
    const imported = String(node.record?.id).startsWith('imp_');
    const args = [imported ? 'session' : 'memory', 'review', String(node.record?.id)];
    const preview = await this.read(node, args);
    if (!preview) { return; }
    await this.display(node, preview, 'review-preview');
    if (!this.current(node) || preview.requires_human_review !== true) { return; }
    const choice = await vscode.window.showWarningMessage(
      'Open interactive review? The backend will show fresh evidence. You must type its exact review hash; this button does not accept memory.',
      { modal: true }, 'Open Interactive Review',
    );
    if (choice !== 'Open Interactive Review' || !this.current(node)) { return; }
    const launch = djobsCommandLaunch(new DjobsClient(node.workspace.uri.fsPath), [...args, '--apply']);
    const task = new vscode.Task(
      { type: 'djobs-memory-review', memoryId: node.record?.id }, node.workspace,
      'Review memory (human confirmation required)', 'djobs',
      new vscode.ProcessExecution(launch.command, launch.args, { cwd: launch.cwd, env: launch.env }), [],
    );
    task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, focus: true };
    await vscode.tasks.executeTask(task);
    this.refresh();
  }

  async forget(node: MemoryNode): Promise<void> {
    if (!this.current(node) || node.kind !== 'memory') { return; }
    const choice = await vscode.window.showWarningMessage(
      `Forget ${node.record?.id}? Dependent memory may become unavailable. Explicit tasks are preserved.`,
      { modal: true }, 'Forget This Memory',
    );
    if (choice !== 'Forget This Memory' || !this.current(node)) { return; }
    const result = await this.read(node, ['memory', 'forget', String(node.record?.id)]);
    if (result) { this.refresh(); }
  }

  async trace(node: MemoryNode): Promise<void> {
    if (!this.current(node)) { return; }
    const query = await vscode.window.showInputBox({ title: 'Explain native memory retrieval',
      validateInput: value => value.length > 500 ? 'Use at most 500 characters' : undefined });
    if (!query?.trim() || query.length > 500 || !this.current(node)) { return; }
    const result = await this.read(node, ['memory', 'trace', query]);
    if (result) { await this.display(node, result, 'retrieval-trace'); }
  }
}

export function registerMemoryExplorer(context: vscode.ExtensionContext): MemoryExplorer {
  const explorer = new MemoryExplorer();
  const protect = (action: () => Promise<void>) => action().catch(() =>
    vscode.window.showWarningMessage('djobs memory is unavailable. Coding can continue; no automatic repair was run.'));
  context.subscriptions.push(explorer,
    vscode.window.createTreeView('djobsMemoryExplorer', { treeDataProvider: explorer }),
    vscode.workspace.registerTextDocumentContentProvider(SCHEME, explorer),
    vscode.workspace.onDidCloseTextDocument(doc => explorer.releaseDocument(doc.uri)),
    vscode.workspace.onDidChangeWorkspaceFolders(() => explorer.refresh()),
    vscode.workspace.onDidChangeConfiguration(event => {
      if (event.affectsConfiguration('djobs')) { explorer.refresh(); }
    }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => explorer.refresh()),
    vscode.commands.registerCommand('djobs.memoryRefresh', () => explorer.refresh()),
    vscode.commands.registerCommand('djobs.memoryExposure', () => protect(() => explorer.selectExposure())),
    vscode.commands.registerCommand('djobs.memorySearch', () => protect(() => explorer.search())),
    vscode.commands.registerCommand('djobs.memoryOpen', (node: MemoryNode) => protect(() => explorer.open(node))),
    vscode.commands.registerCommand('djobs.memoryDepth', (node: MemoryNode) => protect(() => explorer.selectDepth(node))),
    vscode.commands.registerCommand('djobs.memoryReview', (node: MemoryNode) => protect(() => explorer.review(node))),
    vscode.commands.registerCommand('djobs.memoryForget', (node: MemoryNode) => protect(() => explorer.forget(node))),
    vscode.commands.registerCommand('djobs.memoryTrace', (node: MemoryNode) => protect(() => explorer.trace(node))),
  );
  return explorer;
}
