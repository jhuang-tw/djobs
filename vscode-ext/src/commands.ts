import * as childProcess from 'child_process';
import * as path from 'path';
import { DjobsClient } from './djobsClient';

/**
 * Run the public djobs entrypoint with the same runtime selected for the MCP server.
 *
 * The historical extension launcher called ``python -m djobs.cli`` for configured
 * interpreters and project virtual environments. That bypassed the memory-first
 * entrypoint where setup, memory, gain, and the actionable doctor are defined.
 */
export function runDjobsCommand(
  client: DjobsClient,
  args: string[],
  timeout = 30000,
): Promise<string> {
  const launch = client.mcpServerLaunch();
  const basename = path.basename(launch.command);
  let command = launch.command;
  let prefix: string[];

  if (/^djobs-mcp(?:\.(?:exe|cmd|bat))?$/i.test(basename)) {
    command = path.join(
      path.dirname(launch.command),
      basename.replace(/^djobs-mcp/i, 'djobs'),
    );
    prefix = [];
  } else {
    prefix = ['-c', 'from djobs.entrypoint import main; main()'];
  }

  return new Promise((resolve, reject) => {
    childProcess.execFile(
      command,
      [...prefix, ...args],
      {
        cwd: launch.cwd,
        env: { ...process.env, ...launch.env },
        timeout,
        windowsHide: true,
      },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(stderr.trim() || stdout.trim() || error.message));
          return;
        }
        resolve(stdout);
      },
    );
  });
}
