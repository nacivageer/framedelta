/**
 * python.js — 找一台机器上能跑 gif_digest.py 的 Python。
 *
 * 为什么单独一个模块：宿主自带的 Python（numpy + Pillow 都在里面）和用户在 PATH 上
 * 装的 Python 可能不是同一个，而"哪台机器把依赖装在哪"只有运行时知道。写死开发机路径
 * 会让插件在别人机器上永远报 `no python`。
 *
 * 顺序：显式配置 → 本插件专用环境变量 → DSH_PYTHON → DSH 运行时的 Python → PATH。
 * 判定标准不是"能执行"，而是"能 `import numpy, PIL`"——脚本只依赖这两个。
 */

import { execFile } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

/** 脚本真正需要的模块；探测时按这一串验。 */
const PROBE_MODULES = ['numpy', 'PIL'];
const PROBE_CODE = `import ${PROBE_MODULES.join(', ')}`;

/** `~/.dsh`（或 DSH_HOME）；别的插件也按这个规矩找家目录。 */
export function dshHome(env = process.env) {
  return env.DSH_HOME || join(env.USERPROFILE || env.HOME || homedir(), '.dsh');
}

/**
 * DSH 自己那套运行时里的 Python。
 *
 * 实测布局是 `<DSH_HOME>/dsh-runtimes/<runtime>/dependencies/python/python.exe`，
 * 但 `<runtime>` 这层名字会变（dsh-primary-runtime / 以后可能有别的），所以扫目录、
 * 不写死那一段。`load_workspace_dependencies` 报的也是这一处。
 */
export function bundledPythons(env = process.env) {
  const root = join(dshHome(env), 'dsh-runtimes');
  const exe = process.platform === 'win32' ? 'python.exe' : join('bin', 'python3');
  let names = [];
  try {
    names = readdirSync(root, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name);
  } catch {
    return [];
  }
  return names
    .map((name) => join(root, name, 'dependencies', 'python', exe))
    .filter((p) => existsSync(p));
}

/** @returns {string[]} 候选解释器，按优先级 */
export function pythonCandidates({ config = {}, env = process.env } = {}) {
  const out = [];
  const push = (value) => {
    if (typeof value === 'string' && value.trim() && !out.includes(value.trim())) out.push(value.trim());
  };
  push(config?.python);
  push(env.DSH_FRAMEDELTA_PYTHON);
  push(env.DSH_PYTHON);
  for (const candidate of bundledPythons(env)) push(candidate);
  if (process.platform === 'win32') {
    push('python');
    push('py');
  } else {
    push('python3');
    push('python');
  }
  return out;
}

/**
 * 成功永久缓存；失败只缓存 60 秒 —— 装完依赖不用重启宿主，最多等一分钟就自动恢复。
 * （同一台机器上"装完 numpy 插件还说没有"是最伤人的一类报告。）
 */
const FAIL_TTL_MS = 60_000;
let cached = null;          // { cmd, at }
let inFlight = null;        // 并发调用只探一轮

function probe(cmd, timeoutMs) {
  return new Promise((resolve) => {
    execFile(cmd, ['-c', PROBE_CODE], { timeout: timeoutMs, windowsHide: true }, (error) => {
      resolve(error ? null : cmd);
    });
  });
}

/**
 * @returns {Promise<{cmd: string|null, tried: string[], missing: string[]}>}
 *   `cmd` 为 null 时调用方把 tried / missing 拼进报错，别只说 "no python"。
 */
export async function findPython({ config = {}, env = process.env, timeoutMs = 8000 } = {}) {
  if (cached && (cached.cmd || Date.now() - cached.at < FAIL_TTL_MS)) {
    return { cmd: cached.cmd, tried: cached.tried, missing: cached.missing };
  }
  if (inFlight) return inFlight;

  inFlight = (async () => {
    const candidates = pythonCandidates({ config, env });
    const tried = [];
    for (const cmd of candidates) {
      const hit = await probe(cmd, timeoutMs);
      tried.push(cmd);
      if (hit) {
        const result = { cmd: hit, tried, missing: [] };
        cached = { cmd: hit, at: Date.now(), tried, missing: [] };
        return result;
      }
    }
    const result = { cmd: null, tried, missing: PROBE_MODULES.slice() };
    cached = { cmd: null, at: Date.now(), tried, missing: result.missing };
    return result;
  })();

  try {
    return await inFlight;
  } finally {
    inFlight = null;
  }
}

/** 清掉探测缓存（改配置后不必等 60 秒；测试也用它）。 */
export function resetPythonCache() {
  cached = null;
  inFlight = null;
}
