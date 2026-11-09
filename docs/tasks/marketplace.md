# marketplace 任务卡（DSH 插件市场收录）

目标：把 `dsh-framedelta` 提交进 [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin) 的社区目录（一个 `data/plugins/*.yml` 条目；dshmarket 与站点会自动收录）。

## 状态

更新时间：2026-11-10 01:50

| 步骤 | 状态 |
|---|---|
| 插件目录搬成 `plugins/dsh-framedelta/`（CI 只认根或 `packages/`·`plugins/`·`apps/` 子包） | 完成（`76080ed`） |
| 仓库加 `dsh-plugin` topic | 完成 |
| 插件 tarball 挂到 Release `v0.1.0-alpha.2` | 完成 |
| 条目 YAML 草稿（见下） | 完成，待提交 |
| 仓库满 1 天（`created_at` 2026-11-09T13:27:59Z → 21:28） | **等到 2026-11-10 21:28** |
| 提 PR + 跟 gate | 待办（21:35 提醒，投回本会话） |
| 合并后回读：市场卡片可搜到、安装命令可用 | 待办 |

**归档口径**：**GUI 的「归档会话」（Ctrl+Shift+A）之后，会话就看不到了**；记忆层（notes / chains / INDEX）是之后唯一的回顾入口，所以它必须在点归档**之前**做全——不能指望事后追加，那时会话已经打不开。本会话在 2026-11-10 00:5x 做过的那次只覆盖到「筹备完成」，是**阶段存档**，不是收尾。顺序固定为：21:35 提醒投进本会话 → 提 PR、跟 gate、回读 → 在会话还活着时补记忆层归档（T12+ 段、notes/INDEX、commit）→ 最后才点 GUI 归档。**在此之前不要归档**：提醒的投递目标是本会话，归档后它既等不到也看不到了。

## 条目草稿

文件：`data/plugins/nacivageer__framedelta--plugins-dsh-framedelta.yml`

```yaml
url: https://github.com/nacivageer/framedelta/tree/master/plugins/dsh-framedelta
name: nacivageer/framedelta#dsh-framedelta
category: vision
description:
  en: 'Measures how much a short clip changed and when — changed-area share, displacement, cumulative drift — returning one labelled key-frame image plus the number behind each event; it does not pick shots for a storyboard.'
  zh: '测量短片段里「变了多少、何时变」——变化面积占比、位移、累积漂移，返回一张标注代表帧与每个事件的数值；不做分镜选帧。'
tarball: https://github.com/nacivageer/framedelta/releases/download/v0.1.0-alpha.2/dsh-framedelta-0.1.0-alpha.2.tgz
```

## 规则要点（提交前再核一遍）

- 一个 PR 只加一个文件、最多 3 条；README 是生成的，不要手改。
- `description.en` 必填、以句号结尾；**描述会被拿去核代码**，不能写「切点检测」「支持长视频」这类不成立的措辞。
- `tarball` 必须钉 tag：GitHub 的 `releases/latest` 跳过 prerelease，我们全是 prerelease，写 `latest` 会 404。
- 仓库需满 1 天、带 `dsh-plugin` topic、`dsh.bundle` 在根或那三个子包目录里。
- 与已有条目的区分：`dsh-video-storyboard`（分镜选帧）、`dsh-read-animation`（动图解码）、`dsh-ffmpeg`（切/转/抽帧）都是相邻但不同的事；本插件只做「变了多少、何时变」的测量。

## 日志

- 2026-11-10 01:00 评估收录规则：三个硬门槛（1 天龄、`dsh.bundle` 目录名、topic），选目录搬迁方案 A 并完成；tarball 上传到 alpha.2 Release；条目草稿写入本卡。
- 2026-11-10 01:50 归档口径更正：提醒曾改投新会话，按格的意见改回本会话——PR 与结论留在同一条线，本会话归档为阶段存档，PR 后补归档。
- 2026-11-10 01:58 格澄清「真正归档」= GUI 的「归档会话」（会话停用、可恢复），不是记忆层索引。定顺序：提醒先投递 → 提 PR → 补记忆层归档 → 再点 GUI 归档；不提前点，避免提醒被静默吞掉。
