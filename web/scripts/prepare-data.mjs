import { cp, mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { resolve, dirname, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const history = resolve(web, '..', process.env.GRID_HISTORY_DIR || 'history');
const outputName = process.argv.includes('--build') ? '.generated-build' : '.generated';
const generated = resolve(web, outputName);
const index = JSON.parse(await readFile(resolve(history, 'index.json'), 'utf8'));
if (index.schema_version !== 1 || !index.snapshots.length) throw new Error('Run python -m pipeline.weekly_snapshot first.');
let count = 0;
async function check(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Unexpected symlink: ${path}`);
    if (entry.isDirectory()) await check(path);
    else {
      if (!entry.isFile() || (await stat(path)).size > 25 * 1024 * 1024) throw new Error(`Asset exceeds Cloudflare's 25 MiB limit: ${path}`);
      count++;
    }
  }
}
await check(history);
if (count > 19000) throw new Error('History approaches the free static-host file limit.');
for (const item of index.snapshots) {
  if (!/^snapshots\/[\w-]+\.json$/.test(item.url)) throw new Error('Invalid snapshot path');
  const snapshot = JSON.parse(await readFile(resolve(history, item.url), 'utf8'));
  if (!/^geometry\/[a-f0-9]+\.json\.gz$/.test(snapshot.geometry)) throw new Error('Invalid geometry path');
  await stat(resolve(history, snapshot.geometry));
}
// Only this generated directory is disposable, never the history itself.
if (relative(web, generated) !== outputName || !generated.startsWith(web + sep)) throw new Error('Invalid staging path');
await rm(generated, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
await mkdir(generated, { recursive: true });
await cp(history, resolve(generated, 'data'), { recursive: true, filter: source => !source.endsWith('.tmp') });
await writeFile(resolve(generated, '.nojekyll'), '');
await writeFile(resolve(generated, '_headers'), '/data/index.json\n  Cache-Control: public, max-age=0, must-revalidate\n/data/geometry/*\n  Cache-Control: public, max-age=31536000, immutable\n/data/snapshots/*\n  Cache-Control: public, max-age=31536000, immutable\n/data/sources/*\n  Cache-Control: public, max-age=31536000, immutable\n');
console.log(`Prepared ${index.snapshots.length} weekly snapshot(s), ${count} assets; all below 25 MiB.`);
