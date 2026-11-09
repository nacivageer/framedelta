# release-alpha 任务卡

目标：把 framedelta 从「能跑的 v0.1」修到「可以公开的 alpha」。只做发布硬伤，不动判据与阈值。

## 状态

更新时间：2026-11-09 21:48

| 任务 | 状态 | 依赖 | 负责人 |
|---|---|---|---|
| A 输入加固 | 完成（2026-11-09） | 无 | 新会话 A |
| B 发布卫生 | 完成（2026-11-09） | 无（可与 A 并行） | 新会话 B |
| C 交付体验 | 完成（2026-11-09） | **A 完成** | 新会话 C |
| D 判据（背景区 jump / 溶解） | 背景区 jump 完成（2026-11-09）；溶解、更大样本待新会话 | C 完成 | 本会话 + 新会话 |

状态只填：待办 / 进行中 / 完成（日期）/ 阻塞（原因）。每会话结束更新本表 + 在文末日志加一行。

## 通用规则（每个会话都读）

- 只读本任务卡点名的文件，**不要通读 `docs/决策记录.md`**（48 KB）；需要背景时按节定点读。
- 不改 `TH` 阈值、不改 `analyse()` / `pick_key_frames()` / 事件判据——那是 D 的事。
- 改 `gif_digest.py` 后必须 `python tools/sync_plugin.py`，并跑 `--check`。
- 不把 `runs/`、桌面素材、`bilibili` 临时文件加进 Git。
- **两个测试素材（regress 的 `clipA` / `clipB`）的画面内容不得公开**：文件本身、截图、抽帧、base64 一律不进仓库；文件名与文字描述可以写，但代码里只写代号、路径走 `runs/local/regress_sources.json`（已 ignored，缺配置时这两个案例 SKIP）。
- **A 与 B 并行时，commit 只 `git add` 自己改的文件**；`git status` 里出现对方的文件不要提交。
- commit 前把 `git status --short` 与验收输出给格看，等确认。
- 工作区：`C:\Users\Administrator\Documents\deepseek-harness\framedelta`，分支 `master`，remote `origin` = `https://github.com/nacivageer/framedelta.git`（public，2026-11-09 21:28 首推；推送授权由格逐次给，别默认能推）。

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

## D 判据（发布后）

前置：A/B/C 完成、alpha 已发。D 的验收必须包含 `tools/calib.py` 的 17 段 P/R 与 `gifcheck --strict-jumps`。

1. **背景区 jump 判据——完成（2026-11-09）**。做法、阈值依据、数字与已知残留见 `docs/决策记录.md`「背景区 jump 判据」一节（只读那一节即可）。摘要：12 段 GIF 的 jump 16→11（7/7 真切点保留、误报 9→4），calib 17 段 P/R/F1 = 1.00，eval 12 段产物结构不动。
2. **溶解判据——未做**。L1 的 11.1 / 14.2 两处（文字在持续运动的底片上淡出；两个实拍镜头的叠化）。三条整幅统计已判死（`docs/方案_渐变判据.md` 第 2 节），**不要再给 `fade_events` 加阈值**；方向是区域级（文字/图层区域的变化）或场景身份（局部特征匹配率）。样本只有 2 处，先写测量脚本把「可分 / 不可分」定下来，再谈实现与阈值。
3. **更大样本复读——未做**。现有读数建立在 3 段 15 秒 + 9 段 3 秒上，且 15 秒里只有 1 段真人。要新语料 + 独立读图方；**开工前先跟格定语料与预算**（读图方 token 成本要点头）。

接手状态（新会话先看）：

- `regress` 现在是 **2/2 通过 + 2 跳过**：两段保密素材已从本机移走，规模覆盖缺口见 22:40 日志；长片段看 `eval_run`（L1 452 帧）、GIF 结构看 `gifcheck`。
- 改 `gif_digest.py` 后必须 `python tools/sync_plugin.py` 并 `--check`。

## 新会话启动语

A：

> 读 `docs/tasks/release-alpha.md`，只做 A 节。先读顶部状态块和 A 节点名的文件，不要通读 `docs/决策记录.md`。完成后跑 A 的验收命令，更新状态块与日志；commit 前先把 `git status --short` 和验收输出给我看。

B：

> 读 `docs/tasks/release-alpha.md`，只做 B 节。不要动 `gif_digest.py` 和 `tools/`；只 add 自己改的文件。LICENSE 署名和 README 语言先问我。完成后跑 B 的验收，更新状态块与日志。

C：

> 读 `docs/tasks/release-alpha.md`，只做 C 节。先确认 A 的状态是「完成」；如果还是「进行中/阻塞」，停手告诉我。完成后按 C 的验收跑，更新状态块与日志。

D（剩余两项，按需选一个）：

> 读 `docs/tasks/release-alpha.md` 的 D 节与顶部状态块。背景区 jump 已完成并提交，别重做；先只读 `docs/决策记录.md` 的「背景区 jump 判据」一节，不要通读整份决策记录。本轮只做 <溶解判据 | 更大样本复读>。**动手前先给我方案**：溶解要写清判据族与测量办法、预期能补几个漏点；更大样本要写清语料从哪来、几个读图方、大约多少 token。我点头后再改代码。改完跑 `calib.py`、`gifcheck --strict-jumps`、相关单测与 `regress.py`，更新状态块与日志；commit 前把 `git status --short` 与验收输出给我看。

## 日志

- 2026-11-09 20:45 建卡。工作区干净，`master`，无 remote。
- 2026-11-09 20:51 A 完成：`probe_video()`（`-show_streams` 读 `side_data_list[].rotation`，旧版退 `tags.rotate`；avg/r 差 >5% 标 `vfr_suspect`）、`parse_rate()`（尾逗号 / `0/0` / 解析失败不崩）、解码加 `-noautorotate` 后按显示矩阵旋转（方向以 320×240 左上红块对 ffmpeg autorotate 实测：90→`rot90(k=1)`、180→k=2、270→k=-1）、`meta.json` 与 `digest.meta` 加 `rotation`/`vfr_suspect`、坏 GIF / ffprobe 失败给中文 `SystemExit`；新增 `tools/test_input.py`。验收：`test_input` 63 项 ALL PASS、`test_resample` ALL PASS、`regress --quick` 2/2 PASS，`sync_plugin --check` 一致（41b5b70464051d8e）。GIF 产物抽查：仅 meta 多两个字段，测量值未变。commit `84667a2`。
- 2026-11-09 20:54 B 完成：新增 `LICENSE`（MIT，© 2026 `72NG`——署名与 README 语言开工前问过格）、`requirements.txt`（`numpy>=1.24` / `Pillow>=10.1`）、`.github/workflows/test.yml`（ubuntu + windows × py3.10 / 3.12，跑 `test_budget` / `test_fades` / `test_resample`；`tools/test_input.py` 存在即跑，Windows 缺 ffmpeg 先装，不跑 `regress` / `gifcheck`）。README 首屏重写：定位「变化测量，不是剪辑点」+ 5 行英文摘要 + Does/Doesn't 表 + 安装 + 30 秒 quickstart + 产物表 + 已知限制（16 jump 仅 7 真切 / 溶解漏报 / 深色换页隐形 / `--max-frames` 不省解码内存 / 样本 9+3 且次数 ±1 噪声）+ MIT。`plugin/package.json`：去 `private`、`license: MIT`、`0.1.0-alpha.1`，`repository` 留空待定。验收：三单测 ALL PASS（11 组 / 8 组 / 13 项），README 搜不到「切点检测 / cut detection」，`git status --short` 只含 B 文件，workflow 经 `yaml.safe_load` 解析（7 步、矩阵正确）。未动 `gif_digest.py` 与 `tools/`。commit `20d2aad`（见 21:02 条）。
- 2026-11-09 21:02 B 按格的四点反馈修订 README：① 限制段数字改「现算」——`python tools/gifcheck.py --refresh` 重跑 12 段（14 s）：`jump` 16 处 / 真切点 7/7 命中 / 已知误报 9 / 新误报 0 / 漏 0；② 5 行英文摘要从 blockquote 改成独立 `## English` 一节；③ 补回状态行 `0.1.0-alpha.1`（与 `package.json` 版本对应，给读者一个成熟度锚点）；④ `calib.py` 示例措辞回退成「已知切点」——「换场」并不比「切点」更安全，两者都指向剪辑点，验收拦的是「当卖点」，工具说明该用准确词。验收结论不变（无代码改动，三单测沿用 20:54 的 ALL PASS）。commit `20d2aad`（21:05 格确认后提交）。
- 2026-11-09 21:21 C 完成（A 状态先确认为「完成」）：① 内存预算：`probe_video()` 加 `nb_frames`（缺失退 `duration × fps`）→ `estimate_decode_bytes()` = 帧数 × w × h × 3 × 2；`build()` 在 **解码前** `check_decode_budget()`，上限读 `DSH_FRAMEDELTA_MAX_BYTES`（默认 2.5 GiB，`0`/非法值分别＝关闭/默认），超限给中文 `SystemExit`（估算值、上限、转码示例，并写明 `--max-frames` / `--fps` 不省解码内存）；`meta.json` 与 `digest.meta` 加 `estimated_decode_bytes`（video＝解码前估算；GIF/目录＝解完后的实际解码量，口径相同）。② 主图：`run.js` 优先 `annotated.png`、缺失退 `sheet.png`（返回字段名仍 `sheet` 以兼容调用方），文本 `picks` 行改「labels on the image」，`tool-spec.js` 的描述 / 产物列表 / 「别重复读图」同步；按格确认**只回一张**。③ `_font()` 加 `DejaVuSans.ttf` / `NotoSansCJK-Regular.ttc` / `arial.ttf` 候选，末位 `ImageFont.load_default(size)`（Pillow < 10.1 退老签名），版式不变。验收：`test_budget` 11 组 / `test_fades` 8 组 / `test_resample` / `test_input` 全 ALL PASS，`regress --quick` 2/2（drift 0），`sync_plugin --check` 一致 `32d5a2d0f18060a4`；阈值临时降到 1 MB 跑 1080p 5 s（150 帧，est 1.7 GB）：exit 1、中文提示、无 `meta.json` 产出；6 帧 GIF 走插件：返回 18,962 B 与 `annotated.png` 逐字节相同，headline `framedelta · six.gif · 6f · 0.600s · 10.0fps · picks 4/4 · auto`；删 `annotated.png` 后复用 digest 正确回退 `sheet.png`，两张都删也不抛异常（`sheet: null`，文本照常）。`plugin/locale/en.json` 描述 `contact sheet` → `labelled key-frames image`（格点头一并改）。commit `db5da5c`。
- 2026-11-09 21:28 首发 GitHub：格在网页建空仓（owner `nacivageer`，display name `72NG`——`LICENSE` 署名因此不动；public；未勾 README/.gitignore/license），`git remote add origin` + `push -u origin master` 一次成功（GCM 已存 `github.com` 凭据）。回读：`ls-remote` 的 `refs/heads/master` = 本地 HEAD = `001e6a0`，`origin/master` 41 个 commit，`git status -sb` 无 ahead/behind。仓库：https://github.com/nacivageer/framedelta
- 2026-11-09 21:34 CI 首跑：run #2（`ebe6064`）conclusion `success`，4 个 job 全绿（ubuntu / windows × py3.10 / py3.12）。run #1（`001e6a0`）被 concurrency 取消，属预期。**但有一处未确认**：Windows 上 `Input tests` 步 4 s（ffmpeg 由 choco 装），ubuntu 上同一步只记 **0 s**（本地实测 7.7 s），怀疑走了 `test_input.py` 的「没有 ffmpeg → 打印 SKIP」分支（SKIP 也 exit 0，job 结论照样 success）；匿名拉 Actions 日志返回 403（需 admin 权限），没看到实际输出。要堵这个洞：让 CI 在缺 ffmpeg 时硬失败（如加一步 `command -v ffprobe`，或给 test_input 加 `--require-ffmpeg`），否则 ubuntu 上的 mp4 / 旋转用例等于没跑。
- 2026-11-09 21:41 上条疑点追查完毕：**是真空跑**，已修。根因两条——① `ubuntu-latest` 就是 Ubuntu 24.04（runner-images README 的 label 表），24.04 镜像的软件清单里**没有 ffmpeg / ffprobe**（只有 mediainfo，不是一回事），`test_input.py:194` 命中 SKIP 分支后照样 `exit 0`；② 原 `Show ffmpeg version` 步是 `ffmpeg -version | head -n 1`，默认 `bash -e` 没开 `pipefail`，退出码取最后一个命令（`head`）的 0，所以缺 ffmpeg 也是绿——这一步绿着本身即证明 pipefail 没开。本地复现：把 ffmpeg 摘出 PATH 跑 `test_input.py` → 0.51 s、exit 0、`SKIP … / ALL PASS`，与 ubuntu 的 0 s + success 完全一致；Windows 腿是 choco 真装了 ffmpeg，所以 `Input tests` 4 s 是实跑。影响：Linux 腿从未覆盖旋转 / `ffprobe` 解析 / 坏输入。修法：`test_input.py` 加 `--require-ffmpeg`（缺依赖 `exit 1`，默认行为仍是 SKIP，本地不受影响）；workflow 拆成 `Install ffmpeg (Linux)`（`apt-get install -y ffmpeg` + 不用管道的 `ffmpeg -version` / `ffprobe -version`）与 `Install ffmpeg (Windows)`（装完再 `Get-Command` 断言并抛错），删掉被管道掩盖的 `Show ffmpeg version`，`Input tests` 改跑 `python tools/test_input.py --require-ffmpeg`。本地验证：YAML 解析、有/无 ffmpeg × 带/不带 flag 四条路径（ALL PASS / FAIL exit 1 / SKIP exit 0）、其余单测与 `regress --quick` 全过。代价：ubuntu 每 job +约 30 s。
- 2026-11-09 21:48 21:41 的修复已验证：run #3（`a381796`）conclusion `success`，4 条腿全绿——`Install ffmpeg (Linux)` apt 16–19 s，`Input tests` 在 ubuntu 从 **0 s 变成 2–3 s**（Windows 仍 4 s）。结论可判定：`--require-ffmpeg` 缺依赖会 `exit 1`，所以绿就等于没走 SKIP，Linux 腿的旋转 / `ffprobe` 解析 / 坏输入这次真跑过。`origin/master` = `a381796`，工作区干净。
- 2026-11-09 22:05 D 节第一项（背景区 jump）落代码，**未提交、等格确认**。`background_ncc()`：jump 前后各 500ms 中位画面的 NCC，≥0.75 判为「同一画面上的运动/遮挡」，降级 motion/subtle（`bg_suppressed`），`flow.csv` 与 `digest.jumps[].bg_ncc` 带证据；`TH` 加 `jump_bg_ncc / jump_bg_window_ms / jump_bg_min_frames`。阈值取真切点上限 0.623 与误报下限 0.82 的间隔中点。结果：12 个 GIF jump 16→11（7/7 真切点全留、误报 9→4、新误报 0）；calib 17 段 P/R/F1 仍 1.00/1.00/1.00（15/15）；eval 12 段 jump/transitions/shots 全不动（13/16 读数不用重测）。基线更新：clip74 16→7（#66–70 仍带 occlusion_like，#9/#47 差一点没压到）、clipA 6/3/3→4/2/2（bg 0.87）。新增 `tools/test_jump_bg.py` 6 组；`regress` 4/4、`gifcheck --strict-jumps` 0、`sync_plugin --check` 一致。细节见决策记录「背景区 jump 判据」。D 剩溶解判据（要换判据族）与更大样本复读。
- 2026-11-09 22:25 格指出两个测试素材含保密内容：工作树里的引用已全部清掉——`regress` 改成从 `runs/local/regress_sources.json`（已 ignored）读路径、缺文件就 SKIP，案例改名 `clipA` / `clipB`；文档与 `gif_digest.py` 注释里的文件名、内容描述换成代号；确认仓库从未收进这两个文件本身（无任何 gif/mp4/png 被跟踪）。**历史里还有 4 个早期 commit 带原文件名（已随首发公开）**，是否改写历史待格决定。
- 2026-11-09 22:35 格澄清：**只有画面内容不能公开，文件名无所谓**。核对：仓库历史上从未提交过任何 gif/mp4/png（`git log --diff-filter=A` 无记录），当前跟踪里也没有二进制——两个素材的画面从未离开本机，抽帧与产物都在 ignored 的 runs/ 下。因此**不改写历史**；代号 clipA/clipB 与 `runs/local/` 路径配置保留（顺带把机器相关路径移出公开代码，克隆者跑 regress 时这两例会 SKIP 而不是报错）。
- 2026-11-09 22:40 两个保密素材已从 D:\Desktop 移走（22:35 检查时已不在）：它们原先提供的「规模素材 ±15%」两例（clipA/clipB）现在 SKIP，`regress` 报 **2/2 通过 + 2 跳过**（exit 0，不再当成素材缺失硬失败）。**覆盖缺口要记住**：regress 现在只剩两段短素材，长素材 / 多变数量级由 `eval_run`（L1 452 帧）与 `gifcheck`（12 段 GIF）覆盖；要恢复这两例，把文件放回 `runs/local/regress_sources.json` 指的路径即可。
- 2026-11-09 22:50 D 第一项（背景区 jump）已提交 `4b45487`（7 个文件，367+/27-），**未推送**。提交前验收：`test_jump_bg` / `test_budget` / `test_fades` / `test_resample` 全过、`gifcheck --strict-jumps` 0、`regress` 2/2 + 2 跳过、`sync_plugin --check` 一致（6846b4356455f4fc）。派生画面按格的意思保留，不删。D 剩下的溶解判据与更大样本复读交给新会话，启动语已写进本卡。
