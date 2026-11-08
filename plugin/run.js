/**
 * run.js — 把一次 `framedelta` 调用变成「跑 Python → 读 digest.json → 文本 + 一张图」。
 *
 * 分工：这个文件**不认识 cordis**（不碰 ctx），只收 `{ args, ctx }` 里的东西当参数，
 * 好让 `index.js` 保持"只注册"，也方便脱离宿主单测。
 *
 * 一条不变的约定（`docs/决策记录.md` 的「接口」节）：Python 与 Node 之间只交换
 * 「目录 + JSON」。这里不 import 脚本的任何逻辑，只 execFile。
 */

import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { findPython } from './python.js';

/** 包里的脚本副本。`tools/sync_plugin.py` 保证它和仓库根目录那份逐字节一致。 */
export const SCRIPT_PATH = join(dirname(fileURLToPath(import.meta.url)), 'py', 'gif_digest.py');

const DIGEST = 'digest.json';
const SHEET = 'sheet.png';

/** execFile 的上限：工具本身给宿主 900s，这里留 30s 收尾。 */
const PY_TIMEOUT_MS = 870_000;

/* ---- 路径 ---------------------------------------------------------------- */

/** 会话工作目录（拿不到就退回宿主进程 cwd）。相对路径按它解析。 */
export async function sessionCwd(ctx, exec) {
  try {
    const svc = typeof ctx?.get === 'function' ? ctx.get('workingDirectory') : ctx?.workingDirectory;
    if (svc?.ensure && exec?.agent) return await svc.ensure(exec.agent);
  } catch { /* 老宿主没有这个服务：按进程 cwd 走 */ }
  return process.cwd();
}

/**
 * 找出真实存在的输入路径。
 *
 * 依次试：原样（绝对路径）→ 会话工作目录 → 宿主进程 cwd。都不在就报**解析后**的路径，
 * 让调用方一眼看出是相对路径解析错了，而不是"文件不存在"。
 */
export function resolveInput(input, bases) {
  const raw = String(input ?? '').trim();
  if (!raw) throw new Error('input is required (a GIF, an mp4, or a directory of frames)');
  const candidates = isAbsolute(raw) ? [raw] : [...bases.map((b) => resolve(b, raw)), raw];
  for (const candidate of candidates) {
    try {
      if (existsSync(candidate)) return resolve(candidate);
    } catch { /* 不可读就当没找到 */ }
  }
  throw new Error(`input not found: ${candidates[0]}${raw !== candidates[0] ? ` (from "${raw}")` : ''}`);
}

/* ---- 缓存目录 ------------------------------------------------------------ */

/**
 * 目录输入的内容指纹。
 *
 * 目录自己的 mtime 不随子文件「内容被覆盖」变化，只数条目数也不够：帧序列换了同名
 * 文件时，路径 / size / mtime / 条目数可以全都不变，缓存会一直命中旧产物。
 * 所以对每个直接子文件取 `名字|大小|mtime`，排序后一起 hash。
 */
function dirSignature(path) {
  const rows = [];
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    if (!entry.isFile()) {
      rows.push(`${entry.name}|dir`);
      continue;
    }
    try {
      const st = statSync(join(path, entry.name));
      rows.push(`${entry.name}|${st.size}|${Math.round(st.mtimeMs)}`);
    } catch {
      rows.push(`${entry.name}|?`);
    }
  }
  rows.sort();
  return createHash('sha1').update(rows.join('\n')).digest('hex').slice(0, 16);
}

function inputFingerprint(path) {
  const st = statSync(path);
  if (st.isDirectory()) {
    let sig = '';
    try {
      sig = dirSignature(path);
    } catch { /* 目录读不了就让目录自身的 mtime 兜底 */ }
    return `${path}|dir|${st.size}|${Math.round(st.mtimeMs)}|${sig}`;
  }
  return `${path}|${st.size}|${Math.round(st.mtimeMs)}`;
}

/** 输入（文件或目录下任一文件）的最新 mtime；产物比它新才算命中。 */
function newestMtime(path) {
  const st = statSync(path);
  if (!st.isDirectory()) return st.mtimeMs;
  let newest = st.mtimeMs;
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    try {
      newest = Math.max(newest, statSync(join(path, entry.name)).mtimeMs);
    } catch { /* 读不到的条目跳过 */ }
  }
  return newest;
}

/**
 * 显式给 `out` 时不能只看 digest.json 在不在：路径是固定的，源或参数变了它也在。
 * 产物比输入旧就重跑——与 README 的「动一下素材」承诺一致。
 */
function digestIsFresh(digestPath, input) {
  try {
    return statSync(digestPath).mtimeMs >= newestMtime(input);
  } catch {
    return false;
  }
}

/**
 * 同一次调用（同一文件、同一 mtime、同一组参数）落到同一个目录，第二次直接复用产物。
 * 缓存键里带 mtime 与 size：素材一改就换目录，不会读到上一次的数字。
 */
export function outDirFor({ input, key, fps, maxFrames, env = process.env }) {
  const hash = createHash('sha1')
    .update(JSON.stringify({
      p: inputFingerprint(input),
      key: key ?? null,
      fps: fps ?? null,
      maxFrames: maxFrames ?? null,
    }))
    .digest('hex')
    .slice(0, 10);
  const stem = basename(input).replace(/\.[^.]+$/, '') || 'frames';
  const slug = stem.replace(/[^\w\u4e00-\u9fa5-]+/g, '_').slice(0, 40) || 'frames';
  return join(env.DSH_FRAMEDELTA_OUT || tmpdir(), 'framedelta', `${slug}-${hash}`);
}

/* ---- 跑 Python ----------------------------------------------------------- */

function tail(text, lines = 12) {
  const rows = String(text ?? '').trimEnd().split(/\r?\n/);
  return rows.slice(-lines).join('\n');
}

/** @returns {Promise<{ms: number, python: string}>} */
export async function runPython({ script = SCRIPT_PATH, input, outDir, key, fps, maxFrames, config, env = process.env }) {
  const found = await findPython({ config, env });
  if (!found.cmd) {
    throw new Error(
      `no Python with ${found.missing.join(' + ')}. Tried: ${found.tried.join(', ') || '(nothing)'}. `
      + 'Point DSH_FRAMEDELTA_PYTHON at an interpreter that has numpy and Pillow, '
      + 'or install them for the DSH bundled Python.',
    );
  }

  const args = [script, input, '--out', outDir, '--quiet'];
  if (key != null) args.push('--key', String(key));
  if (fps != null) args.push('--fps', String(fps));
  if (maxFrames != null) args.push('--max-frames', String(maxFrames));

  const started = Date.now();
  await new Promise((ok, fail) => {
    execFile(found.cmd, args, { timeout: PY_TIMEOUT_MS, windowsHide: true, maxBuffer: 8 * 1024 * 1024 },
      (error, _stdout, stderr) => {
        if (!error) return ok();
        if (error.killed) {
          return fail(new Error(`framedelta.py killed after ${Math.round(PY_TIMEOUT_MS / 1000)}s`));
        }
        const code = typeof error.code === 'number' ? error.code : '?';
        const hint = /ffmpeg/i.test(stderr) ? ' (is ffmpeg on PATH? mp4 input needs it)' : '';
        return fail(new Error(`framedelta.py failed (exit ${code})${hint}: ${tail(stderr) || String(error.message)}`));
      });
  });

  return { ms: Date.now() - started, python: found.cmd };
}

/* ---- 文本 ----------------------------------------------------------------- */

const fmtSec = (ms) => `${(Number(ms) / 1000).toFixed(3)}s`;
const fmtPct = (frac) => `${(Number(frac) * 100).toFixed(frac >= 0.1 ? 0 : 1)}%`;
const fmtNum = (value, digits = 1) => (Number.isFinite(value) ? Number(value).toFixed(digits) : '?');

/** 事件各行最多打几条；超出的用一行 `+N more` 收尾，剩下的让调用方去读 digest.json。 */
const EVENT_CAP = { jumps: 8, swaps: 5, drifts: 5, fades: 5 };

function eventLines(digest) {
  const lines = [];
  const push = (kind, rows, format) => {
    if (!Array.isArray(rows) || rows.length === 0) return;
    for (const row of rows.slice(0, EVENT_CAP[kind] ?? 5)) lines.push(format(row));
    if (rows.length > (EVENT_CAP[kind] ?? 5)) {
      lines.push(`${kind}: +${rows.length - (EVENT_CAP[kind] ?? 5)} more (digest.json)`);
    }
  };
  push('jumps', digest.jumps, (j) =>
    `jump  #${j.frame}  ${fmtSec(j.t_ms)}  area ${fmtPct(j.changed_frac)}  hsv ${fmtNum(j.hsv_score)}  ncc ${fmtNum(j.ncc, 2)}`
    + (j.occlusion_like ? `  occlusion-like (burst ${j.burst})` : ''));
  push('swaps', digest.swaps, (s) =>
    `swap  #${s.frame}  ${fmtSec(s.t_ms)}  area ${fmtPct(s.changed_frac)}  ncc ${fmtNum(s.ncc, 2)}  neighbours quiet`);
  push('drifts', digest.drifts, (d) =>
    `drift #${d.start_frame}→#${d.end_frame}  ${fmtSec(d.start_ms)}–${fmtSec(d.end_ms)}  area ${fmtPct(d.changed_frac)}  hsv ${fmtNum(d.hsv_mean)}`);
  push('fades', digest.fades, (f) => {
    const start = f.start_ms ?? f.t_ms;
    const span = f.end_ms != null ? `${fmtSec(start)}–${fmtSec(f.end_ms)}` : fmtSec(start);
    const mid = f.mid_ms != null ? `  mid ${fmtSec(f.mid_ms)}` : '';
    const mode = f.mode ? `  mode ${f.mode}` : '';
    return `fade  #${f.start_frame ?? f.frame}  ${span}  area ${fmtPct(f.changed_frac)}${mid}${mode}`;
  });
  return lines;
}

function keyFrameLines(digest) {
  const lines = [];
  for (const k of digest.key_frames ?? []) {
    const d = k.delta;
    const parts = [`#${k.frame}`, fmtSec(k.t_ms), k.reason ?? 'pick'];
    if (d) {
      parts.push(`area ${fmtPct(d.changed_frac)}`);
      if (Math.abs(d.dx_px) >= 0.5 || Math.abs(d.dy_px) >= 0.5) parts.push(`dx ${fmtNum(d.dx_px)} dy ${fmtNum(d.dy_px)}`);
      if (d.scale && Math.abs(d.scale - 1) >= 0.01) parts.push(`scale ${fmtNum(d.scale, 3)}`);
      if (d.local_mag_px >= 1) parts.push(`local ${fmtNum(d.local_mag_px)}px`);
    }
    lines.push(`  ${parts.join('  ')}`);
  }
  return lines;
}

/**
 * digest.json → 给模型看的那段话。
 *
 * 只报「哪一刻、多大、几次」。**不写形容词**：`docs/决策记录.md` 里"摘要是否变成
 * caption"的判据就是这条 —— 语义留给读图的人。
 */
export function formatDigest(digest, { outDir, reused, ms, name } = {}) {
  const meta = digest.meta ?? {};
  const counts = digest.counts ?? {};
  const budget = digest.budget ?? {};
  const cells = (digest.key_frames ?? []).length;
  const period = meta.duration_ms ? fmtSec(meta.duration_ms) : '?s';
  const headline = [
    'framedelta',
    name ?? basename(digest.source ?? outDir ?? ''),
    `${meta.frames ?? '?'}f`,
    period,
    meta.fps ? `${fmtNum(meta.fps)}fps` : null,
    `picks ${cells}/${budget.k ?? cells}`,
    budget.mode === 'auto' ? 'auto' : 'fixed',
    reused ? 'cached' : `${((ms ?? 0) / 1000).toFixed(1)}s`,
  ].filter(Boolean).join(' · ');

  const countLine = [
    `jump ${counts.jumps ?? 0}`,
    `swap ${counts.swaps ?? 0}`,
    `drift ${counts.drifts ?? 0}`,
    `fade ${counts.fades ?? 0}`,
    `shots ${(digest.shots ?? []).length}`,
    `pairs ${counts.pairs ?? '?'}`,
  ].join(' · ');

  const frames = keyFrameLines(digest);
  const lines = [headline, countLine];
  const occl = (digest.jumps ?? []).filter((j) => j.occlusion_like).length;
  if (occl) {
    lines.push(`note: ${occl} jump(s) are occlusion-like — a burst of ≥3 whole-frame spikes,`
      + ' usually something passing in front of the lens (hat/hair/hand) or a fast wipe.'
      + ' Do NOT count them as cuts; check the cells.');
  }
  const events = eventLines(digest);
  lines.push(...(events.length ? events : [`no jump / swap / drift / fade above threshold`]));
  if (frames.length) lines.push('picks (frame numbers match the labels on the sheet):', ...frames);
  lines.push(`out ${outDir}`);
  return lines.join('\n');
}

/* ---- 一次完整调用 --------------------------------------------------------- */

/**
 * @returns {Promise<{text: string, sheet: Buffer, outDir: string, digest: object, meta: object, reused: boolean}>}
 */
export async function runFramedelta({ args = {}, ctx, exec, config = {}, env = process.env, script = SCRIPT_PATH }) {
  const bases = [await sessionCwd(ctx, exec), process.cwd()];
  const input = resolveInput(args.input, bases);

  const key = normaliseCount(args.key, 'key', 1, 16);
  const fps = normalisePositive(args.fps, 'fps');
  const maxFrames = normaliseCount(args.maxFrames, 'maxFrames', 1, 100_000);

  const outDir = args.out ? resolve(bases[0], String(args.out)) : outDirFor({ input, key, fps, maxFrames, env });
  const digestPath = join(outDir, DIGEST);
  const reusable = existsSync(digestPath) && digestIsFresh(digestPath, input);

  let run = { ms: 0 };
  if (!reusable) run = await runPython({ script, input, outDir, key, fps, maxFrames, config, env });

  let digest;
  try {
    digest = JSON.parse(readFileSync(digestPath, 'utf8'));
  } catch (error) {
    throw new Error(`framedelta.py finished but ${digestPath} is unreadable: ${error?.message ?? error}`);
  }

  const sheetPath = join(outDir, SHEET);
  let sheet = null;
  try {
    sheet = readFileSync(sheetPath);
  } catch {
    // 图没了不该让整条调用失败：文本本身仍然有用，调用方会看到"没有图"的说明。
  }

  const text = formatDigest(digest, { outDir, reused: reusable, ms: run.ms, name: basename(input) });
  return {
    text,
    sheet,
    outDir,
    digest,
    reused: reusable,
    meta: {
      dir: outDir,
      input,
      reused: reusable,
      ms: run.ms,
      frames: digest.meta?.frames ?? null,
      durationMs: digest.meta?.duration_ms ?? null,
      counts: digest.counts ?? {},
      cells: (digest.key_frames ?? []).length,
      budget: digest.budget ?? {},
    },
  };
}

/* ---- 参数兜底校验 --------------------------------------------------------- */

function normaliseCount(value, label, min, max) {
  if (value == null || value === '') return null;
  const n = Number(value);
  if (!Number.isInteger(n) || n < min || n > max) {
    throw new Error(`${label} must be an integer in ${min}..${max} (got ${JSON.stringify(value)})`);
  }
  return n;
}

function normalisePositive(value, label) {
  if (value == null || value === '') return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) throw new Error(`${label} must be a positive number (got ${JSON.stringify(value)})`);
  return n;
}
