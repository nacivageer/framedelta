# 评测集

回答的不是「切点检测准不准」（那是 `tools/calib.py` 的事），而是**产品问题**：只给产物，读图方能不能说出「变化在哪、多大、几次」。

- 真值：`eval/truth.json`（9 段 3 秒）+ `eval/truth_long.json`（3 段 15 秒，scan.py 取窗）+ `eval/truth_big.json`（6 段 15 秒，固定 30% 处取窗，2026-11-09 加），都是人工抽帧核过、带来源与时间
- 协议：本文件
- 产物：`runs/eval/<case>/`、`runs/eval_big/<case>/`（git 忽略；重跑命令见文末）

## 素材

九段 3 秒窗口，三种口味：

| case | 来源 | 内容 |
|---|---|---|
| `e1_slides` | BV1rop26FEZf 13.5–16.5s | 幻灯片文字整页切换 |
| `e2_ui` | BV13Z4B6wERz 10.5–13.5s | 同布局文字卡片切换（能量最小的正样本） |
| `e3_a` | BV1yNnC6nEyo 6.5–9.5s | 暗场动画叠化换场 |
| `e4_b` | BV1YM4P6qE8r 8.0–11.0s | 整页文案替换、立绘消失 |
| `e5_c` | BV11jnm6bENb 392.5–395.5s | 基本静止（负样本，全零基线） |
| `e6_d` | BV1MdpT6tENu 143.5–146.5s | 幻灯片/窗口多次切换 |
| `e7_trailer` | 影之刃零 最终预告 133.5–136.5s | 动作片：爆炸爆闪，无硬切 |
| `e8_fufu` | BV1gf4P6YEnS 45.5–48.5s | 游戏动画：一次硬切 + 连续快速运动 |
| `e9_aimanhua` | BV1KjnK6EE46 9.5–12.5s | AI 漫剧：两次换镜（第二次是切人） |

前六段从 bilibili 工作区评审时留下的视频里切（`tools/scan.py` 找变化量最大的窗口）；后三段是 2026-11-08 补的「别的口味」素材（预告片来自 `see-now/_scratch/trailer.mp4`，另外两段按 BV 号下载 480p）。

## 长片段（15 秒）

补的三段 15 秒窗口，用来回答「代表帧预算够不够」——3 秒片段回答不了这个问题。

| case | 来源 | 内容 | 真值次数 |
|---|---|---|---|
| `L1_trailer` | 影之刃零 最终预告 130–145s | 打斗 → 闪白片名 → 花海 → 女子近景 → 骑兵，两次交叉溶解 | 5 |
| `L2_aimanhua` | BV1KjnK6EE46 5–20s | 六次换场，全是硬切或带运动模糊的切 | 5 |
| `L3_screen` | 屏幕录制 138–153s | 幻灯片 + 开始菜单窗口进出 + 换页 + 整幅换到落地页 | 4 |

真值在 `eval/truth_long.json`（写法同 `truth.json`，每条带抽帧复核记录）。计数仍按 5% 面积与「状态切换」口径。已评估但未计入的点（例如 L3 顶部标签栏出现，面积约 4%）写在各自的 `note` 里。

## 做法

1. 人工看全帧写真值：第几秒、几次、什么性质；不写语义（在讲什么不属于 L1/L2）。
2. 读图方**只看** `runs/eval/<case>/` 里的 `digest.json` + `digest.budget.k` 张图（≤8s 是 4 张，15 秒是 6 张），作答同样三项。不看原片段、不看 `truth.json`。
3. 对分：次数一致、时间误差 ≤0.5s、性质不错 = 命中。`python tools/score.py eval/truth.json <answers.json> --tol 0.5`。

读图方必须换一个 agent（自读自答没有意义）。

## 对照实验：均匀 8 帧

`tools/uniform.py` 按 dsh-read-animation 的交付形态（时间均匀 8 帧、帧名带 `#frameNNN@毫秒`）生成 `runs/eval/_uniform/*.png`；同一批素材、同一套问题，比较「均匀 8 帧」与「代表帧 + 数值」谁答得准。

## 结果

见 `docs/决策记录.md` 的「评测（L2/L3 端到端）」一节。第二轮起产物自带 `Δarea`（每张代表帧相对上一帧的变化像素占比，`annotated.png` 头行、`sheet.png` 每格、`digest.key_frames[].delta` 三处一致）；第三轮起 `digest.json` 多两类事件：`swaps`（布局不变的大块内容替换）与 `drifts`（连续中等变化累积成的一次变化）。

长片段的预算对照（4 / 6 / 8 张，每臂各一个独立读图方）：命中 **12 / 11 / 12**（共 16 点，±0.5s），结构覆盖 7 / 12 / 14。**多给格子没有换来更准的读数**，但原因不是「槽位被切点前后对照对吃掉」——2026-11-08 的普查推翻了这条：对照对只占 2 / 4 / 6 格，两格内容不重复（后格 Δarea 66–75%，前格与其它格最高 NCC 0.33–0.52），删掉「切点前」那格会丢掉真值点唯一的图。真正的缺陷是**末帧没有保证**（L2 的白屏在 6/8 张臂里都没进格）与**静止段重复取格**（L3 的 8 格只有 4 种画面）。原始答案：`answers_L1-L3_k4.json` / `_k6.json` / `_k8.json` / `answers_tool_long.json`（工具自报基线）。

**修后（末帧保证 + 同图去重，只在 >8s 启用）**：同一预算 k=6 的读数 **12/16、次数完全一致 2/3**（`answers_fix_k6.json`，修前 11/16、1/3）；结构上 L3 的 8 格重复从 4 降到 0、L2 的末格变成全白、L3 k6 的 ±0.5s 覆盖 3/4 → 4/4。剩下的 4 处漏报全部是溶解/渐变（L1 11.1 / 14.2、L2 14.72、L3 6.55）。

**加渐变判据后**：k=6 的 3 段 **13/16、次数一致 2/3**（`answers_fade_k6.json`）；L2 的白屏由 `fades`（mode=blank，中点 14.57）补上，L1 的两处溶解抽帧后确认不是「静 → 溶解 → 静」（文字在持续运动的底片上淡出），没检出，原因写在 `docs/方案_渐变判据.md` 第 8 节。

**9 段 3 秒素材（2026-11-08 重读）**：`76c3839` 把去重/末态下放到所有时长后 7 段代表帧变了，旧读数作废。用当前产物的重读是 **12/13 命中、5/9 次数一致**（`answers_short9_2026-11-08.json`）。次数这一项有 ±1 的读图噪声：`e7_trailer` 的产物与历史逐帧相同，换个读图方就从 1 变 2。历史那句「9/9 命中、6/6 次数一致」是 v4（六段）+ v5（三段）拼起来的读数，不是一次跑完 9 段。

## 更大样本（2026-11-09，D 节第三项）

前两批样本都是「挑变化最密的窗口」（`tools/scan.py`），且 15 秒里只有 1 段真人。这一批换口径：

- **固定位置取窗**：每段视频从**时长的 30% 处**开始切 15 秒，不看内容挑窗口。这样样本里会出现单镜头长段和负样本，与「挑最热闹的 15 秒」是两种分布，读数不能与上面的数字相加。
- **6 段新素材**（480p，全部是没读过的公开视频）：

| case | 来源 | 类型 | 真值次数 |
|---|---|---|---|
| `r1_oral` | BV1WVfcYFEiX 罗翔说刑法 | 真人单人口播 | 1 |
| `r2_talk` | BV1X198BrEzU 新世相 | 真人访谈（25fps，同场景换机位） | 3 |
| `r3_vlog` | BV1DtZCYdEH8 KerryDowdle | 真人 vlog（负样本） | 0 |
| `r4_skit` | BV1eBZhYhEa9 李如儒也是李蠕蠕 | 真人小剧场（竖屏 480×852） | 4 |
| `a1_anime` | BV1UbZ8YmEP8 寞桀緣才 | 手书动画（854×418） | 3 |
| `s1_screen` | BV1arZJYfE9h 赤川鹤鸣_Channel | 录屏 | 3 |

真值在 `eval/truth_big.json`，答案在 `eval/answers_big_2026-11-09.json`，产物在 `runs/eval_big/<case>/`（git 忽略）。

**读数**：6 个独立读图方，每段一个，只给 `digest.json` + `annotated.png` → **11/14 命中（±0.5s）、次数完全一致 3/6**。

| case | 真值 | 读图方 | 结果 |
|---|---|---|---|
| r1_oral | 1 | 1 | ✓ 10.1s（真值 10.3）；工具 0 jump，靠图读出来 |
| r2_talk | 3 | 3 | ✓ 6.4 / 11.1 / 14.3 |
| r3_vlog | 0 | 0 | ✓ 负样本没有误报 |
| r4_skit | 4 | 5 | ✗ 命中 3；漏 1.4s，多 2.5s 与 14.8s |
| a1_anime | 3 | 2 | ✗ 漏 5.5s 的交叉溶解 |
| s1_screen | 3 | 4 | ✗ 命中 2；12.2s 报成 13.2s，多报 6.7s |

三条观察：

1. **与旧样本持平**：13/16（0.81）对 11/14（0.79）。换了取窗口径、补了三段真人，命中率没变差也没变好——「读数不随样本换而崩」现在有两批独立证据。
2. **漏报仍集中在「没有 jump 的转场」**：a1 的 5.45 交叉溶解、s1 的 12.2 暗场换卡；r4 的 1.4s 硬切掉在第一个代表帧（2.48s）之前，读图方无从判断。溶解判据已关门（见 `docs/决策记录.md`「溶解判据（不可分）」），所以这是选帧与判据的共同上限，不是阈值松紧。
3. **次数会被「采样密度」骗**：r4 的读图方原话是「6 个采样帧互不相同，故记 5 次」——它把代表帧的个数当成事件个数，多报 2 次、漏 1 次。事件密集的素材上，「6 个时刻能不能盖住 15 秒」比判据更能决定次数对不对。

**真值修订**：r4 首版只写了 1 次（擦除转场），s1 首版漏了 1.58s 的窗格布局变化；两处都是读图方先指出、再按帧号复核补上的（`truth_big.json` 的「修订」字段记了过程）。计数边界也写进了真值：独立内容卡计入（r1），随台词逐句换的花字不计（r4）。

重跑（源视频需先按 BV 号下 480p；窗口 = 时长 × 30%）：

```
ffmpeg -ss 108.2 -t 15 -i runs/_corpus/raw/r1_oral.mp4 -an -c:v libx264 -crf 20 runs/eval/_clips_big/r1_oral.mp4
python gif_digest.py runs/eval/_clips_big/r1_oral.mp4 --out runs/eval_big/r1_oral
python tools/score.py eval/truth_big.json eval/answers_big_2026-11-09.json --tol 0.5
```

## 重跑

```
python tools/scan.py <视频> --topk 3                     # 找变化窗口
ffmpeg -ss <t> -t 3 -i <视频> -an -c:v libx264 -crf 20 runs/eval/_clips/<case>.mp4
python gif_digest.py runs/eval/_clips/<case>.mp4 --out runs/eval/<case>
python tools/uniform.py runs/eval/_clips/<case>.mp4 --n 8 --out runs/eval/_uniform/<case>.png
```

核真值一律按帧号取帧（`digest.json` 里 `jumps[].frame` 就是帧号）：

```
python tools/frames.py runs/eval/_clips/L1_trailer.mp4 --out runs/_probe/x.png 448 449 450 451
python tools/frames.py runs/eval/_clips/L1_trailer.mp4 --out x.png --around 451 3
```

预算对照实验（同一段、同一批问题，只差给几张图）：

```
python gif_digest.py runs/eval/_clips/L1_trailer.mp4 --out runs/base/k4/L1_trailer --key 4
python gif_digest.py runs/eval/_clips/L1_trailer.mp4 --out runs/base/k6/L1_trailer --key 6
python gif_digest.py runs/eval/_clips/L1_trailer.mp4 --out runs/base/k8/L1_trailer --key 8
python tools/score.py eval/truth_long.json eval/answers_L1-L3_k4.json --tol 0.5
python tools/score.py eval/truth_long.json eval/answers_L1-L3_k6.json --tol 0.5
python tools/score.py eval/truth_long.json eval/answers_L1-L3_k8.json --tol 0.5
```

结构侧（不花钱、不用读图方）：`python tools/coverage.py --arm 4=runs/base/k4 --arm 6=runs/base/k6 --arm 8=runs/base/k8`；查重复格用 `python tools/simmat.py runs/base/k8/L3_screen`。
