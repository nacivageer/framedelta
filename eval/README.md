# 评测集

回答的不是「切点检测准不准」（那是 `tools/calib.py` 的事），而是**产品问题**：只给产物，读图方能不能说出「变化在哪、多大、几次」。

- 真值：`eval/truth.json`（人工看全帧写的，一行一段，带来源与时间）
- 协议：本文件
- 产物：`runs/eval/<case>/`（git 忽略；重跑命令见文末）

## 素材

从 bilibili 工作区评审时留下的视频里切 3 秒窗口（`tools/scan.py` 扫变化量最大的窗口再切），共 6 段：

| case | 来源 | 内容 |
|---|---|---|
| `e1_slides` | BV1rop26FEZf 13.5–16.5s | 幻灯片文字整页切换 |
| `e2_ui` | BV13Z4B6wERz 10.5–13.5s | 同布局文字卡片切换（能量最小的正样本） |
| `e3_a` | BV1yNnC6nEyo 6.5–9.5s | 暗场动画闪烁 + 轻推镜 |
| `e4_b` | BV1YM4P6qE8r 8.0–11.0s | 整页文案替换、立绘消失 |
| `e5_c` | BV11jnm6bENb 392.5–395.5s | 基本静止（负样本，全零基线） |
| `e6_d` | BV1MdpT6tENu 143.5–146.5s | 幻灯片/窗口多次切换 |

## 做法

1. 人工看全帧写真值：第几秒、几次、什么性质；不写语义（在讲什么不属于 L1/L2）。
2. 读图方**只看** `runs/eval/<case>/` 里的 `digest.json` + 至多 4 张图，作答同样三项。不看原片段、不看 `truth.json`。
3. 对分：次数一致、时间误差 ≤1s、性质不错 = 命中。`python tools/score.py eval/truth.json <answers.json>`。

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
