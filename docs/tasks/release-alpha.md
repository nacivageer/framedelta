# release-alpha 任务卡

目标：把 framedelta 从「能跑的 v0.1」修到「可以公开的 alpha」。只做发布硬伤，不动判据与阈值。

## 状态

更新时间：2026-11-09 20:45

| 任务 | 状态 | 依赖 | 负责人 |
|---|---|---|---|
| A 输入加固 | 待办 | 无 | 新会话 A |
| B 发布卫生 | 待办 | 无（可与 A 并行） | 新会话 B |
| C 交付体验 | 待办 | **A 完成** | 新会话 C |
| D 判据（背景区 jump / 溶解） | 发布后 | C 完成 | 另开 |

状态只填：待办 / 进行中 / 完成（日期）/ 阻塞（原因）。每会话结束更新本表 + 在文末日志加一行。

## 通用规则（每个会话都读）

- 只读本任务卡点名的文件，**不要通读 `docs/决策记录.md`**（48 KB）；需要背景时按节定点读。
- 不改 `TH` 阈值、不改 `analyse()` / `pick_key_frames()` / 事件判据——那是 D 的事。
- 改 `gif_digest.py` 后必须 `python tools/sync_plugin.py`，并跑 `--check`。
- 不把 `runs/`、桌面素材、`bilibili` 临时文件加进 Git。
- **A 与 B 并行时，commit 只 `git add` 自己改的文件**；`git status` 里出现对方的文件不要提交。
- commit 前把 `git status --short` 与验收输出给格看，等确认。
- 工作区：`C:\Users\Administrator\Documents\deepseek-harness\framedelta`，分支 `master`，当前无 remote。

## A 输入加固

### 只读

- `gif_digest.py` 的 `load_video()` / `_ffprobe_size()` / `load_frames()`（约 154–200 行）
- `tools/test_resample.py`（测试写法参考）
- 本卡

### 改什么

1. **旋转元数据**
   - `ffprobe -show_streams -of json` 读 `side_data_list[].rotation`，兼容旧版 `tags.rotate`。
   - ffmpeg 解码加 `-noautorotate`，让 raw 尺寸 = 编码宽高，reshape 才安全。
   - 按 rotation 对每帧旋转（90/270 交换宽高）；方向用合成样本实测（左上红块）定，不靠猜。
   - `meta.json` 与 `digest.meta` 加 `rotation` 字段。
2. **帧率解析**
   - 抽一个纯函数 `parse_rate(text) -> float`：`Fraction` 解析、strip 空白与尾逗号；`0/0` 退回 `r_frame_rate`，再退回 25；解析失败不崩。
   - avg 与 r 差 >5% 时在 `meta` 标 `vfr_suspect: true`（不试图修时间轴，只提示）。
3. **错误信息**
   - 坏 GIF / ffprobe 失败 / 0 帧：`SystemExit` 给中文可读提示，不让 `ValueError` traceback 当主输出。
4. **测试** `tools/test_input.py`（新文件）
   - 合成 320×240、左上红块，生成 0/90/180/270 四个样本（`ffmpeg -display_rotation` 是**输入选项**，remux 加旋转）。
   - 断言：`meta.width/height` 正确、红块落在预期角、帧数不变。
   - 断言 `parse_rate("30/1,") == 30`、`parse_rate("30000/1001") ≈ 29.97`、`parse_rate("0/0") == 25`。
   - 用 `runs/_input_cases/` 放临时素材（`runs/` 已 ignore）。

### 验收

```
python tools/test_input.py
python tools/test_resample.py
python tools/regress.py --quick
python tools/sync_plugin.py && python tools/sync_plugin.py --check
```

全过；不重采样的 12 段产物不应变化（`regress --quick` + 抽查一段 digest 即可，不必全量重跑）。

### 已知坑

- ffmpeg 6.0 的 `-display_rotation 90` 必须放在 `-i` 前；`-metadata rotate=90` 不写 display matrix。
- remux 后的 mp4 可能把 `avg_frame_rate` 输出成 `30/1,`（尾逗号），这是本次要修的真实触发。
- ffmpeg autorotate 后输出 240×320，而 ffprobe 报 320×240——不要用 autorotate 后再按 ffprobe 尺寸 reshape。

## B 发布卫生

### 只读

- `README.md`、`plugin/README.md`、`plugin/package.json`
- `docs/决策记录.md` 的「评测」「已知坑」两节（只读这两节）
- 本卡

### 改什么

1. **`LICENSE`**：MIT，年份 2026，版权人署名**开工前问格**（默认用他的 GitHub ID；不要擅自写真名）。
2. **`requirements.txt`**：`numpy>=1.24`、`Pillow>=10.1`（10.1 起 `load_default(size)` 可用，C 会用到）。
3. **`README.md` 首屏**（中文为主 + 5 行英文摘要）：
   - 一句话定位：短片段**变化测量**，不是切点检测。
   - Does / Doesn't 表：能测变化位置/次数/面积/位移；不宣布切镜头、不识别溶解、不保证深色同模板换页、不适合长视频/高分辨率。
   - 安装：Python 3.10+、`pip install -r requirements.txt`、mp4 需要 ffmpeg。
   - 30 秒 quickstart + 产物说明。
   - 已知限制：12 段 GIF 的 16 个 jump 只有 7 个真切点（9 个误报）；溶解漏报；`--max-frames` 不省解码内存；评测样本 9+3 段、次数有 ±1 读图噪声。
   - 许可证。
4. **`plugin/package.json`**：`private` 去掉，加 `"license": "MIT"`，`version` 改 `0.1.0-alpha.1`；`repository` 留空待定。
5. **`.github/workflows/test.yml`**：ubuntu + windows，装 numpy/Pillow，跑 `test_budget.py`、`test_fades.py`、`test_resample.py`（若 A 已交付 `test_input.py` 也跑）。不跑 `regress`/`gifcheck`（需本地素材）。
6. 不改 `gif_digest.py`、不改 `tools/`、不动 `runs/`。

### 验收

- `python tools/test_budget.py && python tools/test_fades.py && python tools/test_resample.py` 全过。
- README 里搜不到「切点检测 / cut detection」作为卖点；限制段落齐全。
- `git status --short` 只含 B 的文件。
- CI 文件语法自查（无 remote 时不能真跑，交付即可）。

## C 交付体验（等 A 完成）

### 只读

- 本卡、A 的改动（`git show` 或 `git diff`）、`plugin/run.js` 的 306–312、`gif_digest.py` 的 `load_video` / `_font` / `render_sheet`
- 不读全量决策记录

### 改什么

1. **内存预算**：解码前用 ffprobe 的 `nb_frames`/`duration` + 宽高估算 `frames × w × h × 3 × 2`；超过 `DSH_FRAMEDELTA_MAX_BYTES`（默认 2.5 GB）→ `SystemExit` 明确说「当前版本会全量解码；请先转码/降分辨率；`--max-frames` 不减少解码内存」。`meta` 写 `estimated_decode_bytes`。不做流式（0.2 再做）。
2. **主图改 annotated**：`plugin/run.js` 优先读 `annotated.png`，没有则退回 `sheet.png`；`tool-spec.js` 描述与返回值说明同步改。是否同时回两张图，开工前问格（默认只回 annotated）。
3. **字体回退**：`_font()` 加 `DejaVuSans.ttf` / `NotoSansCJK-Regular.ttc` / `arial.ttf` 候选；最后 `ImageFont.load_default(size)`。不改版式。

### 验收

- 1080p 长素材触发拒绝（可把阈值临时调低到 1 MB 测 5 秒片段；不要真跑 30 秒）。
- 6 帧 GIF 走插件：返回图是 annotated，文本 headline 仍正确。
- `test_budget.py` / `regress --quick` 不回归。
- 改 `gif_digest.py` 后同步插件副本并 `--check`。

## D 发布后（不在本卡执行）

背景区 jump 判据、溶解判据、更大样本复读。前置：A/B/C 完成、alpha 已发。D 的验收必须包含 `tools/calib.py` 的 17 段 P/R 与 `gifcheck --strict-jumps`。

## 新会话启动语

A：

> 读 `docs/tasks/release-alpha.md`，只做 A 节。先读顶部状态块和 A 节点名的文件，不要通读 `docs/决策记录.md`。完成后跑 A 的验收命令，更新状态块与日志；commit 前先把 `git status --short` 和验收输出给我看。

B：

> 读 `docs/tasks/release-alpha.md`，只做 B 节。不要动 `gif_digest.py` 和 `tools/`；只 add 自己改的文件。LICENSE 署名和 README 语言先问我。完成后跑 B 的验收，更新状态块与日志。

C：

> 读 `docs/tasks/release-alpha.md`，只做 C 节。先确认 A 的状态是「完成」；如果还是「进行中/阻塞」，停手告诉我。完成后按 C 的验收跑，更新状态块与日志。

## 日志

- 2026-11-09 20:45 建卡。工作区干净，`master`，无 remote。
