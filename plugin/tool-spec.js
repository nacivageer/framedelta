/**
 * tool-spec.js — `framedelta` 的契约（不含 execute）。
 *
 * 形状提醒（两个方向不一样，别抄错）：
 *  - `parameters` 是「属性名 → 值 schema」的映射，**不是**完整 JSON Schema；
 *    `{ type:'object', properties:{...} }` 会被宿主拒成
 *    `unsupported JSON schema: parameters.type must be a value schema object`。
 *    必填写成属性里的 `required: true`。
 *  - `output.schema` 反而是完整 JSON Schema。
 *
 * 宿主支持的 schema 子集只有 string / number / integer / boolean / null /
 * array(items) / object(properties) / json / oneOf —— 没有 minimum、pattern、
 * maxItems。所以取值范围只能留在 description 里，由 execute 兜底校验。
 */

/** 工具名。注册日志、报错、测试都从这里取，别各处再写一遍字符串。 */
export const TOOL_NAME = 'framedelta';

/**
 * 这个工具最长能被宿主挂多久（毫秒）。
 *
 * 15 秒 30fps（452 帧）实测约 30 秒；首调用还要加解释器 + numpy 的启动开销。
 * 上限留足，免得长素材被宿主的默认超时砍成 `tool call timed out`。
 */
export const TOOL_TIMEOUT_MS = 900_000;

export function framedeltaToolSpec() {
  return {
    name: TOOL_NAME,
    description: [
      'Measure what changes in a SHORT clip and get back one labelled contact sheet plus a',
      'timeline: where the picture jumps, is replaced as a whole, drifts cumulatively, or fades —',
      'and how far the camera / objects moved between the picked frames.',
      '',
      'Use it when the question is "how many changes, and when" — a GIF, an mp4, or a directory of',
      'numbered frames, up to ~30 s. It never judges meaning; you read the sheet and say what it was.',
      '',
      'WHAT YOU GET (one call, one image):',
      '- one sheet: a grid of sampled frames labelled `frame  time  Δarea%`, with the picks boxed.',
      '  Δarea is changed pixels **vs the previous frame** — a pick that is far from the previous',
      '  pick but sits in a quiet moment can still read 0%, so do not read 0% as "nothing changed".',
      '- a text list: frame count / duration / fps, then `jump` (measured spike), `swap` (whole-frame',
      '  replacement), `drift` (cumulative change that settles), `fade` (to/from a blank screen),',
      '  each with time and evidence (`area`, `hsv`, `ncc`), then the picked frames and their delta.',
      '- the artifact directory: digest.json, annotated.png (motion arrows), kymograph.png, flow.csv.',
      '',
      'RULES OF THUMB:',
      '- The image is already in the result. Do NOT read the sheet file again with read_image.',
      '- Identical arguments are cached — calling twice costs nothing but prints the same answer.',
      '- **A `jump` is a measured spike, not a declared cut.** Fast motion can spike too; a burst',
      '  of ≥3 jumps with a near-whole-frame change is usually something passing in front of the',
      '  lens (hat, hair, hand) or a fast wipe — the digest marks those `occlusion_like`. Check the',
      '  cells before calling any jump a cut. A missing jump does NOT prove nothing changed (dark,',
      '  same-template page swaps are near invisible to these thresholds).',
      '- Long clip: pass `maxFrames` (e.g. 90) or `fps` (e.g. 10) first. Cost grows with frames:',
      '  about a minute for 450 frames at 30 fps; about 2 s for a handful.',
      '- Not for live screens (use see_now) and not for subtitles / speech (use the video_* tools).',
    ].join('\n'),
    parameters: {
      input: {
        type: 'string',
        required: true,
        description: 'Path to a GIF, an mp4, or a directory of numbered frames. Required.',
      },
      key: {
        type: 'integer',
        description: 'How many frames / sheet cells to pick, 1–16. Omit = auto by duration '
          + '(≤8 s → 4, +1 per extra 4 s, cap 8). The sheet can come out with fewer cells when '
          + 'consecutive picks would be the same picture.',
      },
      fps: {
        type: 'number',
        description: 'Resample at this fps before analysis (default: source rate). 10 is a good '
          + 'thinning for 30 fps material and cuts the run time roughly by 3.',
      },
      maxFrames: {
        type: 'integer',
        description: 'Cap how many frames are analysed (e.g. 90). The cheapest knob for long clips.',
      },
      out: {
        type: 'string',
        description: 'Directory for the six artifacts. Omit = a stable cache dir under '
          + '%TEMP%\\framedelta\\<name>-<hash>, reused when the same call repeats.',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          text: { type: 'string', required: true },
          images: { type: 'array', items: { type: 'json' } },
          meta: { type: 'json' },
        },
      },
      render(args, value) {
        // 图在前、文在后：模型先拿到能读的东西，再读数字。
        return [
          ...(value.images ?? []).map((attachment) => ({ type: 'image', attachment })),
          { type: 'text', text: value.text },
        ];
      },
    },
    timeoutMs: TOOL_TIMEOUT_MS,
    isConcurrencySafe() {
      // Python 侧是 CPU 密集的；并发跑会把同一批核抢成两倍时长，收益为负。
      return false;
    },
  };
}
