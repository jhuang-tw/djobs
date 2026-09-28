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
export function djobsCommandLaunch(client: DjobsClient, args: string[]): {
  command: string; args: string[]; cwd: string; env: Record<string, string>;
} {
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
  } else if (/^djobs(?:\.(?:exe|cmd|bat))?$/i.test(basename)) {
    prefix = [];
  } else {
    prefix = ['-m', 'djobs.public_cli'];
  }

  return { command, args: [...prefix, ...args], cwd: launch.cwd, env: launch.env };
}

export function runDjobsCommand(
  client: DjobsClient, args: string[], timeout = 30000,
): Promise<string> {
  const launch = djobsCommandLaunch(client, args);

  return new Promise((resolve, reject) => {
    childProcess.execFile(
      launch.command,
      launch.args,
      {
        cwd: launch.cwd,
        env: { ...process.env, ...launch.env },
        timeout,
        windowsHide: true,
        maxBuffer: 256 * 1024,
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
