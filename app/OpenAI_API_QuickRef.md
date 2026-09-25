# VoxCPM2 OpenAI 兼容 API 快速参考（v5.3.7 新增，2026-09-25 修订）

> 2026-09-25 修订：本次为 Web UI 界面改进（见《使用手册》§12），OpenAI 兼容 API 本身无变化；
> 顺带修正合成示例的 voice 值（preset_default → default，后端不认 preset_default 会 400）。

## 启动
双击安装目录 `Scripts\start_openai_api.bat`（一键拉起后端 19001 + 适配层 8020）。
后端首次启动需加载模型（约 1-3 分钟）。

## 端点总览
| 端点 | 说明 |
|---|---|
| `GET /health` | 存活（适配层）+ 后端健康 + 可用音色 |
| `GET /v1/models` | OpenAI 兼容模型列表 |
| `POST /v1/audio/speech` | 文生语音（返回 WAV 二进制） |
| `GET /v1/audio/voices` | 已登记音色列表 |
| `POST /v1/audio/voices` | 上传参考音色（JSON 或 multipart） |
| `DELETE /v1/audio/voices/{id}` | 删除上传音色 |
| `POST /v1/files`、`GET /v1/files` | 音频文件管理（代理后端） |

Base URL（接工作台/第三方工具）：`http://127.0.0.1:8020/v1`，model 填 `voxcpm2`（任意 `voxcpm`/`openbmb/VoxCPM2` 前缀均可）。

## 合成示例
```
curl -X POST http://127.0.0.1:8020/v1/audio/speech ^
  -H "Content-Type: application/json" ^
  -d ^{"model":"voxcpm2","input":"今天天气怎么样？","voice":"default"}^ -o out.wav
```

## voice 参数
- `default` 或 10 个命名内置预设（默认通道共 11 个：default +
  calm_male / cheerful_girl / cool_guy / elder_woman /
  energetic_broadcaster / gentleman / storyteller / sweet_girl / teacher / warm_woman）→ voice_design 预设通道；
- 上传的 voice_id（`POST /v1/audio/voices`，参考音频 + 自动元数据）→ 参考克隆通道（音色稳定，推荐）；
- 未知 voice → 400（返回可用列表，绝不静默回退）。

## 高级参数
| 参数 | 作用 |
|---|---|
| `style`（推荐） | 风格控制词（语气/语速/情绪）→ control 通道。**2026-09-24 修订起参考克隆(fixed_clone)模式真正生效**：自动附加 `(指令)` 前缀、指令不会被念出；voice_design 预设通道仍有念出风险，响应头会挂 X-VoxCPM-Warning，交付前必须试听 |
| `instructions` / `control` | `style` 的等价别名（优先级低于 style） |
| `prompt_text` | 场景描述文本 → 隐式切 hifi 模式（响应头警示；请求带 `"strict": true` 可让适配层直接 400 拒绝） |
| `ref_audio` | 指定参考音频路径（配合上传音色） |
| `seed`（或 `voxcpm.seed`） | **可复现种子（官方特性，2026-09-24 补齐）**：同种子+同文本+同设置 → 结果可复现（已验证字节级一致，适合对比/复现某个候选取）；不传=随机。官方建议同一文本多次生成 1~3 次取优：可换不同 seed 拿多个候选 |
| `voxcpm.cfg_value` / `voxcpm.inference_timesteps` | 引擎推理参数透传 |

## 响应头（护栏语义）
- `X-VoxCPM-Warning`：本次合成触发了某条已知风险（control 通道/隐式 hifi 切换），必须人工试听；
- `X-VoxCPM-Duration-Anomaly`：合成时长异常偏长（疑似控制词被念出），交付前必须试听。

## 引擎铁律（不可绕过，适配层只是护栏）
1. v5.3.6 旧版 control 通道有念出缺陷（CLI `-c` / 设计模式）；2026-09-24 修订后 `style` 在参考克隆模式下已结构化生效（指令不被念出），预设 voice_design 通道仍有念出风险——优先 上传音色(参考克隆) + `style` 组合；
2. `fixed_clone + prompt_text` 会隐式切 hifi（文档 1.7）—— 要么不带 prompt_text，要么显式 `mode=hifi`。
