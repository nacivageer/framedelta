# framedelta

把一段连续帧压成「一段可读的变化描述 + 几张能读懂的图」。

**做**：逐帧变化检测、切点定位、位移与缩放估计、时间切片、标注帧。
**不做**：判断这段内容在讲什么。那是第二遍的事，读图方来做。

输入：GIF / mp4 / 帧序列目录。输出：一个目录，六个文件（`meta.json`、`sheet.png`、`flow.csv`、`kymograph.png`、`annotated.png`、`digest.json`）。

状态：空壳，代码未开始。先读 `docs/决策记录.md`。
