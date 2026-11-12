/**
 * Launches the Vitest CLI from the canonical (correctly cased) project path.
 *
 * On Windows, terminals such as VS Code's often start in "c:\..." (lowercase
 * drive letter). Vitest then loads its runtime once via that path and again
 * via the real "C:\..." path, so test files see a second, uninitialised copy
 * and fail with "Vitest failed to find the current suite". Normalising the
 * working directory first avoids that. On other platforms this is a no-op.
 *
 * Usage: node scripts/vitest.mjs [vitest args...]
 */
import { spawnSync } from 'node:child_process'
import { realpathSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = realpathSync.native(join(dirname(fileURLToPath(import.meta.url)), '..'))
const vitestCli = join(projectRoot, 'node_modules', 'vitest', 'vitest.mjs')

const result = spawnSync(process.execPath, [vitestCli, ...process.argv.slice(2)], {
  cwd: projectRoot,
  stdio: 'inherit',
})

if (result.error) throw result.error
process.exit(result.status ?? 1)
