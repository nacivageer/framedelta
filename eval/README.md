# 评测集

回答的不是「切点检测准不准」（那是 `tools/calib.py` 的事），而是**产品问题**：只给产物，读图方能不能说出「变化在哪、多大、几次」。

- 真值：`eval/truth.json`（9 段 3 秒）+ `eval/truth_long.json`（3 段 15 秒），都是人工抽帧核过、带来源与时间
- 协议：本文件
- 产物：`runs/eval/<case>/`（git 忽略；重跑命令见文末）

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

## 重跑

```
python tools/scan.py <视频> --topk 3                     # 找变化窗口
ffmpeg -ss <t> -t 3 -i <视频> -an -c:v libx264 -crf 20 runs/eval/_clips/<case>.mp4
python gif_digest.py runs/eval/_clips/<case>.mp4 --out runs/eval/<case>
python tools/uniform.py runs/eval/_clips/<case>.mp4 --n 8 --out runs/eval/_uniform/<case>.png
```

预算对照实验（同一段、同一批问题，只差给几张图）：

```
python gif_digest.py runs/eval/_clips/L1_trailer.mp4 --out runs/eval4/L1_trailer --key 4
python tools/score.py eval/truth_long.json eval/answers_L1-L3_k4.json --tol 0.5
python tools/score.py eval/truth_long.json eval/answers_L1-L3_k6.json --tol 0.5
```
