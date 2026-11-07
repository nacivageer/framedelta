/**
 * schema.js — 把「紧凑 spec」翻译成宿主能接受的完整 JSON Schema。
 *
 * 为什么需要它：宿主有两层形状。
 *  - `defineTool` 吃的是**紧凑值 schema**：`parameters` 是「属性名 → 值 schema」的
 *    映射，必填写成属性里的 `required: true`，`json` 是一种合法 type。
 *  - `ctx.tools.register` 真正校验的是**完整 JSON Schema**：`required` 只能在对象上
 *    写成字符串数组，`type` 只能是 object/array/string/number/integer/boolean/null。
 *
 * 正常安装时宿主能解析 `@deepseek-ai/dsh-tools`，`defineTool` 会把紧凑形状编译好。
 * 但**本地 `link:` 安装**时裸包名解析不到（真实路径在工作区里，不在 profile 的
 * node_modules 下），于是走到直通实现——这时如果不翻译，register 会抛：
 *    schema.properties.text.required is not supported on type "string"
 *    schema.properties.images.items.type must be one of object/array/...
 * 整条插件被判失败，而 apply 里的兜底又把它咽掉，表现就是「插件 active、工具不存在」。
 *
 * 所以：直通路径必须自己翻译一份。翻译只覆盖我们用到的关键字。
 */

const JSON_TYPES = new Set(['object', 'array', 'string', 'number', 'integer', 'boolean', 'null']);

/**
 * 递归翻译一个值 schema 节点。
 * `json` 在完整 schema 里没有对应类型，用「任意对象」代替。
 */
export function toJsonSchema(node) {
  if (!node || typeof node !== 'object') return { type: 'object' };
  if (node.type === 'json') return { type: 'object', additionalProperties: true };

  const out = {};
  if (typeof node.type === 'string' && JSON_TYPES.has(node.type)) out.type = node.type;
  if (node.description) out.description = String(node.description);
  if (Array.isArray(node.enum)) out.enum = node.enum.slice();
  if (node.const !== undefined) out.const = node.const;

  if (node.type === 'object' || node.properties) {
    const properties = {};
    const required = [];
    for (const [key, child] of Object.entries(node.properties ?? {})) {
      properties[key] = toJsonSchema(child);
      // 紧凑写法把「必填」写在子节点里；完整 schema 要求提到父级的数组里。
      if (child?.required === true) required.push(key);
    }
    out.type = 'object';
    out.properties = properties;
    if (required.length) out.required = required;
    if (node.additionalProperties !== undefined) {
      out.additionalProperties = node.additionalProperties === true || node.additionalProperties === false
        ? node.additionalProperties
        : true;
    }
  }

  if (node.type === 'array' && node.items) out.items = toJsonSchema(node.items);

  return out;
}

/**
 * 把一份紧凑 spec 变成 `ctx.tools.register` 能直接校验的定义。
 *
 * 只动 `parameters` 与 `output.schema`，其余字段（execute / output.render /
 * timeoutMs / isConcurrencySafe …）原样保留。
 */
export function normaliseDefinition(spec) {
  return {
    ...spec,
    parameters: toJsonSchema({
      type: 'object',
      properties: spec.parameters ?? {},
      additionalProperties: false,
    }),
    output: {
      ...spec.output,
      schema: toJsonSchema(spec.output?.schema ?? { type: 'object' }),
    },
  };
}
