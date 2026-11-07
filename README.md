# framedelta

把一段连续帧压成「一段可读的变化描述 + 几张能读懂的图」。

**做**：逐帧变化检测、突变与转场定位、位移与缩放估计、时间切片、标注帧、代表帧挑选。
**不做**：判断这段内容在讲什么。那是第二遍的事，读图方来做。

输入：GIF / mp4 / 帧序列目录。输出：一个目录，六个文件（`meta.json`、`sheet.png`、`flow.csv`、`kymograph.png`、`annotated.png`、`digest.json`）。

```
python gif_digest.py D:\Desktop\xxx.gif --out runs\xxx
python gif_digest.py D:\frames\ --fps 10 --out runs\dir
python gif_digest.py D:\clip.mp4 --max-frames 90 --out runs\mp4
```

## 作为 Harness 插件用（`framedelta` 工具）

`plugin/` 是一个 Host-only bundle（`dsh-framedelta`），已在 `desktop` profile 里：

```
framedelta({ input: "D:\\clip.mp4" })          # 一次调用 = 一张 sheet + 文本时间线
framedelta({ input: "...", maxFrames: 90 })    # 长素材先稀释
```

返回里就带图，不用再 `read_image`；同样的输入与参数会命中缓存。安装、参数与已知限制见 `plugin/README.md`。
**改完 `gif_digest.py` 要跑 `python tools/sync_plugin.py`**（插件跑的是 `plugin/py/` 下的副本，`--check` 会因漂移报错）。

依赖：Python 3.10+、numpy、Pillow；读 mp4 需要 PATH 上有 `ffmpeg`/`ffprobe`。

代表帧预算：默认按时长自适应（≤8s 4 张；超过 8s 后每 4 秒 +1，上限 8），`digest.json` 的 `budget` 字段写着 `k`、实出张数 `images` 与推法 `derivation`；`--key N` 可写死。

切点标定：`python tools/calib.py`（拼接已知切点的视频片段，扫阈值出精确率/召回），报告写 `runs/calib/report.json`。

状态：v1 已跑通（2026-11-07）。设计、阈值口径、标定结果与回归数字见 `docs/决策记录.md`。
