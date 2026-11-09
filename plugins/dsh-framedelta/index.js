/**
 * index.js — `dsh-framedelta` 的宿主入口。**只做注册**。
 *
 * 契约要点（对着 @deepseek-ai/dsh-tools 的类型定义核对过）：
 *  - `apply` 返回 disposer，把所有 `ctx.tools.register()` 的返回值串起来；
 *  - `execute` 返回规范值，要能过 `output.schema`（**无损 JSON**：不能有 undefined）；
 *  - `output.render(args, value)` 把规范值渲染成 ContentBlock[]，图片块是
 *    `{ type: 'image', attachment: ImageAttachmentRef }`，ref 只能来自
 *    `ctx.attachments.saveImage()` —— 自己编一个字符串不会被当成图。
 *
 * 两条教训（都是这套宿主上真踩过的）：
 *  1. `inject` 是硬前缀：声明的服务没就绪，`apply` 根本不会被调用，插件永远停在
 *     "加载中"，日志里什么都没有。`attachments` 因此**运行期探测**，不写进 inject。
 *  2. `ctx?.attachments?.saveImage` 挡不住 cordis 的 get 陷阱（没用 inject 声明时它直接
 *     throw）。要用 `ctx.get(name)`，并用 try 包住，老宿主没有 `get()` 时再退回属性访问。
 */

import { basename, join } from 'node:path';
import { mkdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';

import { framedeltaToolSpec, TOOL_NAME } from './tool-spec.js';
import { normaliseDefinition } from './schema.js';
import { runFramedelta } from './run.js';
import { resetPythonCache } from './python.js';

export const name = 'dsh-framedelta';

/**
 * **只声明 `tools`。** 图片能力（`attachments`）留到运行期探测：某些宿主版本或轻量
 * profile 里没有这个服务，硬 inject 会让整个插件挂在那里不加载。
 */
export const inject = ['tools'];

/** apply 的看门狗：宿主启动阶段同步等 apply，永不 resolve 就是"装插件把应用卡死"。 */
const APPLY_TIMEOUT_MS = 20_000;

/** 取一个可选服务；取不到返回 null，绝不抛。 */
function serviceOf(ctx, key) {
  if (!ctx) return null;
  try {
    if (typeof ctx.get === 'function') {
      const viaGet = ctx.get(key);
      if (viaGet) return viaGet;
    }
  } catch { /* 没声明的服务：get 陷阱会 throw，往下退 */ }
  try {
    return ctx[key] ?? null;
  } catch {
    return null;
  }
}

/**
 * 解析宿主的 `defineTool`。正常安装时裸包名可解析；本地/链接安装解析不到时用直通实现 ——
 * 我们的 spec 已经给出完整的 `output.schema` + `render`，少一层类型收敛也要让工具在。
 */
async function loadDefineTool() {
  try {
    const mod = await import('@deepseek-ai/dsh-tools');
    if (mod?.defineTool) return mod.defineTool;
  } catch { /* 落到下面 */ }
  const override = process.env.DSH_TOOLS_PATH;
  if (override) {
    try {
      const { pathToFileURL } = await import('node:url');
      const mod = await import(pathToFileURL(override).href);
      if (mod?.defineTool) return mod.defineTool;
    } catch { /* 配置坏了不该让插件起不来 */ }
  }
  logWarn('@deepseek-ai/dsh-tools 没解析到，用直通 defineTool');
  return (definition) => definition;
}

function logWarn(message) {
  try {
    console.warn(`[dsh-framedelta] ${message}`);
  } catch { /* 记不了就算了 */ }
}

/**
 * 把这次激活的结果写成一个文件。
 *
 * 为什么要这个：`apply` 里的异常必须吞掉（否则"装个插件把应用搞死"），代价是
 * **工具没注册上也不会有任何可见反馈** —— 我第一次装完就是这样：插件行 active、
 * 工具列表里却没有 `framedelta`。写一行状态文件，至少能回答"它到底跑没跑、为什么没成"。
 */
function noteApply(payload) {
  try {
    const dir = join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'framedelta');
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'last-apply.json'),
      JSON.stringify({ at: new Date().toISOString(), pid: process.pid, ...payload }, null, 2),
      'utf8');
  } catch { /* 诊断不能反过来弄坏加载 */ }
}

/** 把 PNG 提交成宿主附件；没有 attachments 服务时返回 null（工具仍然可用，只是没图）。 */
async function attachSheet(ctx, bytes, fileName) {
  const attachments = serviceOf(ctx, 'attachments');
  const saveImage = attachments?.saveImage;
  if (typeof saveImage !== 'function') return null;
  // **必须 `.call(attachments, …)`**：saveImage 内部读 this.compression，裸调会丢 this。
  return saveImage.call(attachments, {
    data: bytes,
    mediaType: 'image/png',
    name: fileName,
  });
}

function buildTool(ctx, config) {
  const spec = framedeltaToolSpec();

  return {
    ...spec,
    async execute(args, exec) {
      const value = await runFramedelta({ args: args ?? {}, ctx, exec, config });

      let images = [];
      let text = value.text;
      if (value.sheet) {
        const stem = basename(value.meta?.input ?? 'input').replace(/\.[^.]+$/, '');
        const fileName = `framedelta-${stem}-${Date.now()}.png`;
        try {
          const ref = await attachSheet(ctx, value.sheet, fileName);
          if (ref) images = [ref];
          else text += '\n(sheet not attached: this DSH build has no attachments service)';
        } catch (error) {
          // 图交不上去不该把整条调用判死：文本与产物目录仍然有用。
          text += `\n(sheet not attached: ${error?.message ?? error})`;
        }
      } else {
        text += '\n(sheet.png missing from the output directory)';
      }

      // 必须是无损 JSON：值里出现 undefined，宿主会判 `value is not lossless JSON` 整条作废。
      return { text, images, meta: value.meta ?? {} };
    },
  };
}

export async function apply(ctx, config = {}) {
  try {
    return await withTimeout(registerAll(ctx, config), APPLY_TIMEOUT_MS);
  } catch (error) {
    // 最坏的情况是插件不能用，不是应用起不来。
    try {
      ctx?.logger?.error?.(`[dsh-framedelta] failed to load: ${error?.stack ?? error}`);
    } catch { /* 连日志都写不了就算了 */ }
    noteApply({ ok: false, error: String(error?.stack ?? error) });
    if (process.env.DSH_FRAMEDELTA_DEBUG) throw error;
    return () => {};
  }
}

async function registerAll(ctx, config) {
  // 模块在多轮 apply 之间是复用的：每次激活都重新探一遍 Python，
  // 免得"改了配置里的 python 路径，却要等一分钟缓存过期"。
  resetPythonCache();

  const defineTool = await loadDefineTool();
  const spec = buildTool(ctx, config);
  const defined = defineTool(spec);
  // 直通实现（裸包名没解析到）时自己翻译形状，否则 register 会因 output.schema 抛错。
  const tool = defined === spec ? normaliseDefinition(spec) : defined;

  const disposer = ctx.tools.register(tool);
  noteApply({ ok: true, tool: TOOL_NAME, defineTool: defineTool.name || 'anonymous' });
  try {
    ctx?.logger?.info?.(`[dsh-framedelta] registered tool ${TOOL_NAME}`);
  } catch { /* 日志不是必须的 */ }
  return () => {
    try {
      disposer?.();
    } catch { /* ignore */ }
  };
}

function withTimeout(promise, ms) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`registerAll timed out after ${ms}ms`)), ms);
    timer.unref?.();
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}
