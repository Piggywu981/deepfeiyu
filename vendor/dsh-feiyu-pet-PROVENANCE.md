# dsh-feiyu-pet 溯源

- 上游仓库：https://github.com/QCYTSN/dsh-dafeiyu
- 版本：`v0.1.23`（tag 与 main HEAD 同一提交）
- 提交：`084323845ab82b68d5388d5c638bec09d8544a8a`
- 引入日期：2026-10-29

## 素材来源（两条都在，默认走子模块）

`vendor/dsh-feiyu-pet` 子模块已接入并 pin 到 `v0.1.23`（tag 与 HEAD 同一提交，
`084323845ab82b68d5388d5c638bec09d8544a8a`）；`.gitmodules` 带 `shallow = true`，
与相邻的 `vendor/dsh-whale-widget` 写法一致。`tools/pet_assets.py` 默认就读它。

npm 官方包保留为弱网兜底（`--from <tgz>`）与完整性锚点。本仓库首次接入时 GitHub 传输
两次被 `Recv failure: Connection was reset` 打断（小仓库的 whale-widget 能过，98MB 的这个过不去），
当时就是靠这条兜底路把功能做完的。npm 包的 `files` 白名单结构上不含 `legacy/`，
所以用它装配时，"排除受限帧"甚至不依赖脚本自觉。

## 许可证构成

| 内容 | 许可 | 归属 |
|---|---|---|
| `runtime/*.py` 六个文件 | MIT | QCYTSN（`LICENSE`） |
| `assets/pet/` 3444 帧 16 组 412×344 透明 WebP | MIT | 上游 PC2005-cloud/dsh-pet @ `549fff2ea33c2acfb16612c76912c6137da4a758`（`assets/dsh-pet-LICENSE.txt`）；`assets/pet-manifest.json` 记录每段源视频 SHA-256 与取帧范围 |
| `assets/cursor_grab*.cur` + `assets/cursors/` | BSD-3-Clause | The Chromium Authors（原样复制自 `ui/resources/cursors/`） |
| `assets/sounds/` | MIT | 上游脚本 `generate_notification_sounds.py` 生成，无第三方录音 |
| 提示音之外的 `ASSET_LICENSE.md` | — | 随包保留 |

**`legacy/dafeiyu/`（老「大肥鱼」帧）不受 MIT 覆盖、禁止在仓库外分发**：npm 包的 `files` 白名单
结构上就不含它（实测包内 `legacy/` 条目为 0），`app/pet/assets.py:LEGACY_ROOTS` 再挡一道，
装配时会主动删除 `data/pet/legacy/`。

## 本地改动

**对上游代码：零。** `tools/pet_assets.py` 逐字节复制 `runtime/*.py` 与 `assets/`。

唯一的转换发生在装配产物的 `pet-manifest.json` 副本上，且只在 `--minimal` 时：从 `clips`
删掉未保留项、把 `workingActivityMap` 全部改写为 `working`、`idleMicroClips` 置空。
原因见上游 `animation_model.py`：`_clip_for` 返回映射值后直接 `self.clips[...]` 索引，
裁帧不重写映射表就是 KeyError。

## 真素材上验过的裁剪规则

`--minimal` 在真实 manifest（16 组 3444 帧）上跑通：保留 6 组 1323 帧，
`workingActivityMap` 与 `stateMap` 全部指向保留 clip（不重写就是上游 KeyError），
`idleMicroClips` 置空；`--check` 通过；源里确实存在的 `legacy/` **没有**进 bundle。

## 已做的验证

- `data/pet/runtime/helper.py --headless` 收 `config/state/pulse/ping/shutdown` 五条：
  回 `ready` 与 `pong`，退出码 0，五条全部落进 `--event-log`。
- 缺 PySide6 时 visual 模式退出码 2 并报 `PySide6 is required for visual mode`。
- **每条消息必须带 `protocolVersion: 1`**：上游 `parse_message`（helper.py 第 60-68 行）逐条校验，
  缺了就整条 `unsupported protocol version`。信封由 `app/pet/protocol.py:wrap()` 在写 stdin 的
  唯一出口统一盖，生产者（reducer/bubble/settings）只产出领域消息。
