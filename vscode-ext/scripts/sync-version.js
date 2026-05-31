#!/usr/bin/env node
// Sync the extension version from the single source of truth:
// ../src/djobs/__init__.py (__version__). Run before compile/package.
const fs = require('fs');
const path = require('path');

const initPath = path.join(__dirname, '..', '..', 'src', 'djobs', '__init__.py');
const pkgPath = path.join(__dirname, '..', 'package.json');

const initSrc = fs.readFileSync(initPath, 'utf8');
const match = initSrc.match(/__version__\s*=\s*["']([^"']+)["']/);
if (!match) {
  console.error(`Could not find __version__ in ${initPath}`);
  process.exit(1);
}
const version = match[1];

const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
if (pkg.version !== version) {
  pkg.version = version;
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n', 'utf8');
  console.log(`Synced extension version -> ${version}`);
} else {
  console.log(`Extension version already ${version}`);
}
