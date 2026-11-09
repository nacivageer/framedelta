# framedelta

短片段**变化测量**：把一段连续帧压成「一段可读的变化描述 + 几张读得懂的图」。

它回答「画面在哪一刻变了、变了几次、变了多大面积、位移多少」，
**不**回答「这里切了镜头」——`jump` 是量出来的尖峰，不是剪辑点。

状态：`0.1.0-alpha.1`（2026-11-09）——核心测量可用；溶解识别与长视频支持还没做。

## English

framedelta measures **change** in short clips, not cuts.
It reports where the picture jumps, is replaced, drifts or fades, and by how much.
Input: GIF / mp4 / numbered frames (≤30 s). Output: one labelled contact sheet, `digest.json` and a text timeline.
Every number is measured (Δarea, HSV, NCC, displacement); the tool never declares "this is a scene cut".
Python 3.10+, numpy + Pillow; ffmpeg/ffprobe needed for mp4.

## 能做 / 不能做

| 能测 | 不做 / 不保证 |
|---|---|
| 变化发生在哪一刻（源帧号 + 毫秒） | 不宣布「这里切了镜头」：`jump` 是**量出来的尖峰**，快速运镜、遮挡成簇也会尖峰 |
| 变了几次（`counts`，聚簇后的 `transitions` 更接近「几次转场」） | 不识别溶解 / 交叉叠化——现有判据的公共漏报全在这一类 |
| 变了多大面积（`changed_frac`）、位移多少（`resid`） | 不保证深色同模板换页：整页替换可能 0 jump、`Δarea` 只有 1% |
| 分类：突变 / 整幅替换 / 累积漂移 / 淡入淡出 | 不适合长视频（>30 s / 30 fps 按当前设计会失效）与高分辨率全量解码 |
| 一张标注联系表 + 运动箭头 + 时间切片，供读图方复核 | 不判断内容在讲什么 |

## 安装

```
Python 3.10+
python -m pip install -r requirements.txt     # numpy>=1.24, Pillow>=10.1
```

读 mp4 还需要 PATH 上有 `ffmpeg` / `ffprobe`（GIF 与帧序列目录不需要）。
运行前提、插件安装与参数见 [`plugin/README.md`](plugin/README.md)。

## 30 秒上手

```
python gif_digest.py D:\clip.mp4 --out runs\clip
# 先看 runs\clip\annotated.png（代表帧 + 运动箭头），再读 runs\clip\digest.json 的事件

python gif_digest.py D:\frames\ --fps 10 --out runs\dir      # 帧序列目录，30 fps 稀释到 10
python gif_digest.py D:\clip.mp4 --max-frames 90 --out runs\mp4   # 长素材只分析前 90 帧
```

终端会打印一条文本时间线（`jump` / `swap` / `drift` / `fade`、代表帧与位移），产物落在一个目录里：

| 文件 | 内容 |
|---|---|
| `digest.json` | 全部事件（帧号 + 毫秒）、代表帧与选帧理由、`counts`、预算 `budget`、阈值 `TH` |
| `annotated.png` | 代表帧 + 运动箭头，首行写变化面积与位移 |
| `sheet.png` | 网格联系表，每格标 `帧号  时间  Δarea%` |
| `kymograph.png` | 时间切片（一行 = 一帧的横向剖面） |
| `flow.csv` | 逐帧位移 / 缩放序列 |
| `meta.json` | 输入信息：尺寸、帧率、时长、旋转、`key_budget` |

`Δarea` 是**相对上一帧**的像素变化占比：离上一格很远、但自己处在安静时刻的格子会标 0%，
别据此判断「没变化」。

## 作为 Harness 插件用（`framedelta` 工具）

`plugin/` 是一个 Host-only bundle（`dsh-framedelta`），用 Plugin Manager 的 `install_bundle` 指向本目录安装：

```
framedelta({ input: "D:\\clip.mp4" })          # 一次调用 = 一张拼图 + 文本时间线 + 产物目录
framedelta({ input: "...", maxFrames: 90 })    # 长素材先稀释
```

返回里就带图，不用再 `read_image`；同样的输入与参数命中缓存（第二次是瞬时的）。
**改完 `gif_digest.py` 要跑 `python tools/sync_plugin.py`**——插件跑的是 `plugin/py/` 下的副本，`--check` 会因漂移报错。

## 已知限制

- **12 段 GIF 例图：`jump` 报 16 处，只有 7 处是逐帧核过的真换场**，9 处是已知误报（横掠镜头、连续走远、爆炸闪光等）。2026-11-09 `python tools/gifcheck.py --refresh` 现算：7/7 真切点命中、已知误报 9、新误报 0、漏 0。成簇的 `jump` 多半是遮挡，`digest.jumps[].occlusion_like` 会标出来——**别把成簇的 `jump` 当剪辑点**；明细在 `eval/gifs/truth.json`。
- **溶解系统性漏报**：交叉叠化与「一直缓慢运动的底片上的淡出」都报不出来，`drift` 要求变化前后安静，溶解不满足。
- **深色同模板换页近乎隐形**：整页替换、整幅压暗时 `jump` 够不到阈值、`Δarea` 也接近 0，别信这两个数字，看图。
- **`--max-frames` 不省解码内存**：它只减少分析的帧数，素材仍是全量解码。
- **评测样本小**：9 段 3 秒 + 3 段 15 秒（挑变化最密的窗口）+ 6 段 15 秒（2026-11-09 加，固定 30% 处取窗、4 段真人）；三批读数 12/13、13/16、11/14 命中，次数一致 5/9、2/3、3/6。事件密集的素材上（15 秒 4 次切换）读者会按代表帧个数反推次数，多报与漏报都可能出现；看命中率与事件 `kind`，别把「次数」当单项指标。
- **长片段慢**：452 帧约 30 秒，时间线性增长。

## 开发

```
python tools/test_budget.py && python tools/test_fades.py && python tools/test_resample.py
python tools/regress.py --quick      # 需要本地素材
python tools/calib.py                # 拼接已知切点的视频片段，扫阈值出精确率/召回
```

- 代表帧预算：默认按时长自适应（≤8 s 4 张；超过 8 s 每 4 秒 +1，上限 8），实出张数与推法写在 `digest.budget`；`--key N` 可写死。
- 设计、阈值口径、评测与标定数字见 [`docs/决策记录.md`](docs/决策记录.md)；真值在 `eval/`，产物目录（`runs/`）不入库。

## 许可证

[MIT](LICENSE) © 2026 72NG
