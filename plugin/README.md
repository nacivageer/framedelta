# dsh-framedelta

把一段连续帧压成「一段可读的变化描述 + 一张读得懂的图」，作为宿主工具 `framedelta` 提供。

- 输入：GIF / mp4 / 帧序列目录（≤30 秒；长素材先用 `maxFrames` / `fps` 稀释）
- 输出：**一张拼图**（网格里是抽出来的帧，被选中的几格描边；每格标 `帧号  时间  Δ%`）
  + 一条文本时间线（jump / swap / drift / fade、代表帧与位移）+ 产物目录
- 产物目录里另有 `digest.json`、`annotated.png`（运动箭头）、`kymograph.png`、`flow.csv`、`meta.json`

它**不判断内容在讲什么**，只报「变化发生在哪、多大、几次」。语义留给读图的人。

## 装

在 Harness 里用 Plugin Manager 装这个包（`install_bundle`，target 指向本目录）。
本地目录安装是 `link:`，所以改完 `index.js` 重载插件即生效，不用重装。

## 运行前提

- Python 3.10+，带 **numpy + Pillow**。顺序：`config.python` → `DSH_FRAMEDELTA_PYTHON`
  → `DSH_PYTHON` → `~/.dsh/dsh-runtimes/*/dependencies/python/python.exe`
  → PATH 上的 `python` / `py`。探测标准是真能 `import numpy, PIL`。
- 读 mp4 需要 PATH 上有 **ffmpeg / ffprobe**（GIF 与帧目录不需要）。

## 参数

| 参数 | 说明 |
|---|---|
| `input` | 必填。GIF / mp4 / 帧序列目录 |
| `key` | 代表帧张数 1–16；不传按时长自适应（≤8s 4 张，每超 4s +1，上限 8） |
| `fps` | 分析前重采样（30fps 素材用 10 可省约 2/3 时间） |
| `maxFrames` | 最多分析多少帧（长素材最省的一刀） |
| `out` | 产物目录；不传则落到 `%TEMP%\framedelta\<名字>-<hash>` |

同样的输入（路径 + mtime + size）与参数会命中同一个目录并直接复用产物，第二次调用是瞬时的。
要强制重跑，换一个 `out` 或动一下素材。

## 改 Python 侧代码时

插件包里那份 `py/gif_digest.py` 是**副本**，源文件在仓库根目录。改完源文件跑：

```
python tools/sync_plugin.py          # 同步
python tools/sync_plugin.py --check  # 只校验（不一致退出码 1）
```

## 已知限制

- >30 秒 / 30fps 的长视频按当前设计会失效；先 `maxFrames` 稀释。
- `jump` 是量出来的尖峰，**不是**「切成镜头」的结论：快速运镜也会尖峰。反过来，深色同模板换页、
  整幅压暗这类变化对现有判据近乎隐形（见 `../docs/交接.md` 的「已知坑」）。
- 图上 `Δarea` 是「相对上一帧」，所以「离上一张代表帧很远、但自己处在安静时刻」的格子会标成
  0%——别据此判断「没变化」（见 `../docs/交接.md` 的「已知坑」）。
