---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 5931fa5612ae011c3dd49956bd422af2_c1a827b7b3ea11f1ba1b525400638852
    ReservedCode1: QyWediUHL14YwAkKac/A93hP+AvgK+vaLmoA6QtfrRP4rwgJ62t+TIz8lHR5x/S0Pmb8ogHUMdLJEuIaUD3uybFcAhHek2CJmgC3paz/hp19r2LlHfJvFytJVKwyTVXNj3inb2xAzz8BHZWMt3c8EYJaxPYe1A4SAzQyz2JDoIbx6JE7YCZvBIA0z/g=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 5931fa5612ae011c3dd49956bd422af2_c1a827b7b3ea11f1ba1b525400638852
    ReservedCode2: QyWediUHL14YwAkKac/A93hP+AvgK+vaLmoA6QtfrRP4rwgJ62t+TIz8lHR5x/S0Pmb8ogHUMdLJEuIaUD3uybFcAhHek2CJmgC3paz/hp19r2LlHfJvFytJVKwyTVXNj3inb2xAzz8BHZWMt3c8EYJaxPYe1A4SAzQyz2JDoIbx6JE7YCZvBIA0z/g=
---

# VoxCPM2 三项改造落地文档（voicebox 参考）

- 日期：2026-09-19
- 改造副本：`D:\AI\Build\VoxCPM2Dist_fixed`（仅本地 git，不推送）
- 原项目：`D:\AI\Build\VoxCPM2Dist`（本任务仅在本目录存放文档，代码零改动）
- 三个 commit（本地）：

| # | commit | 说明 |
|---|--------|------|
| 1 | `0e0c1f8` | REST 接口标准化：语料/音色档案公共后端 + /api/profiles + CLI 档案管理 |
| 2 | `fa836ca` | 模型目录环境变量 VOXCPM_MODELS_DIR 兜底（Web+CLI），输出实际模型目录 |
| 3 | `34d43b1` | 语料/profile 导入导出：REST 路由 + Web 弹窗按钮 + CLI 参数，统一坏行校验 |

---

## 一、Commit 1：REST 接口标准化

### 1.1 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `app\Scripts\voxcpm_api.py` | 新增公共后端层：语料 `read_corpus / write_corpus / validate_corpus_text`、音色档案 `list_profiles / save_profile / delete_profile / validate_profiles_text / import_profiles_text`、模型目录 `resolve_model_dir / model_dir_info` |
| `app\Scripts\vox_web_ui.py` | `/api/corpus` 改为委托 `voxcpm_api`；新增 `/api/profiles`（GET 列表 / POST 新增 / POST delete）；新增 `/api/tts` 支持 `reference_path`（与 CLI `--reference` 对齐）；前端新增「音色档案」按钮与弹窗（open/close/refresh/save/apply/delete/esc，`applyProfile` 用 `selectVoice + setMode` 恢复界面） |
| `app\Scripts\voxcpm_tts_v5_longtext.py` | 引入 `voxcpm_api`；新增 `--list-profiles / --profile-save / --profile-delete` 参数与处理逻辑 |

### 1.2 标准 REST 接口文档

#### 语料（多音字语料 overlay_user_override.txt）

| 方法 | 路径 | 入参 | 出参 | 说明 |
|------|------|------|------|------|
| GET | `/api/corpus` | 无 | `{path, exists, content, mtime}` | 读取当前用户语料 |
| POST | `/api/corpus` | `{content}` | `{ok, path, mtime, message}` | 整体写回语料（保存即热加载） |
| POST | `/api/corpus/import` | `{content}` | `{ok, imported, skipped, skipped_detail, message}` | 导入语料文本，坏行跳过并统计，有效行合并写入（last-wins） |
| POST | `/api/corpus/export` | 无 | `{ok, content, filename, exists, file_path}` | 导出语料内容（UTF-8），同时落盘 `exports/` |

curl 示例：
```bash
# 读取语料
curl.exe http://127.0.0.1:19001/api/corpus
# 导入语料（文本含坏行时自动跳过）
curl.exe -X POST http://127.0.0.1:19001/api/corpus/import -H "Content-Type: application/json" --data-binary "@corpus.txt"
# 导出语料（返回 content + 落盘 exports/corpus_user_override_<ts>.txt）
curl.exe -X POST http://127.0.0.1:19001/api/corpus/export
```

#### 音色档案（profiles.json）

| 方法 | 路径 | 入参 | 出参 | 说明 |
|------|------|------|------|------|
| GET | `/api/profiles` | 无 | 档案 JSON 数组 | 列出全部音色档案 |
| POST | `/api/profiles` | `{name, voice, control_text, mode, reference_wav_path, prompt_text}` | `{ok, name, overwritten, count, message}` | 新增/覆盖档案（重名覆盖） |
| POST | `/api/profiles/delete` | `{name}` | `{ok, name, count, message}` | 删除档案 |
| POST | `/api/profiles/import` | `{content}`（JSON 数组文本） | `{ok, imported, skipped, skipped_detail, count, message}` | 导入档案：坏项跳过、字段白名单清洗、同名覆盖 |
| POST | `/api/profiles/export` | 无 | `{ok, content, filename, file_path}` | 导出全部档案 JSON，同时落盘 `exports/` |

curl 示例：
```bash
# 列表
curl.exe http://127.0.0.1:19001/api/profiles
# 新增档案
curl.exe -X POST http://127.0.0.1:19001/api/profiles -H "Content-Type: application/json" --data-binary "{\"name\":\"我的音色\",\"voice\":\"v1\",\"mode\":\"fixed_clone\",\"reference_wav_path\":\"C:/ref.wav\"}"
# 删除
curl.exe -X POST http://127.0.0.1:19001/api/profiles/delete -H "Content-Type: application/json" --data-binary "{\"name\":\"我的音色\"}"
# 导入
curl.exe -X POST http://127.0.0.1:19001/api/profiles/import -H "Content-Type: application/json" --data-binary "@profiles.json"
# 导出
curl.exe -X POST http://127.0.0.1:19001/api/profiles/export
```

#### CLI 档案管理（与 Web 共用 voxcpm_api，无重复实现）

```bash
python voxcpm_tts_v5_longtext.py --list-profiles
python voxcpm_tts_v5_longtext.py --profile-save "演示:default:温柔女声" --voice v1 --mode voice_design
python voxcpm_tts_v5_longtext.py --profile-delete "演示"
```

---

## 二、Commit 2：模型目录环境变量 VOXCPM_MODELS_DIR

### 2.1 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `app\Scripts\vox_web_ui.py` | `resolve_model_dir()`：优先读 `VOXCPM_MODELS_DIR`，再兼容旧 `VOXCPM_MODEL_DIR`，最后回退默认路径；`/api/status` 新增 `model_dir` 与 `models_dir_env` 字段；`/api/set_model` 同时写入两个环境变量；`/api/paths` 的 `model_dir` 改用 `resolve_model_dir()`；Web 启动横幅打印实际模型目录与两个环境变量值 |
| `app\Scripts\voxcpm_tts_v5_longtext.py` | `LOCAL_MODEL_PATH = os.environ.get("VOXCPM_MODELS_DIR") or os.environ.get("VOXCPM_MODEL_DIR","")`；`--show-config` 打印实际模型目录与两个环境变量值 |

### 2.2 行为说明

- 设置 `VOXCPM_MODELS_DIR` 时：Web 与 CLI 均从该目录加载模型；
- 未设置时回退旧变量 `VOXCPM_MODEL_DIR`，再回退默认路径（原逻辑不变）；
- 排查方式：Web 启动横幅 / `GET /api/status` 的 `model_dir` / CLI `--show-config` 均输出当前实际模型目录。

---

## 三、Commit 3：语料/profile 导入导出

### 3.1 改动文件清单

| 文件 | 改动内容 |
|------|----------|
| `app\Scripts\voxcpm_api.py` | 新增 `import_corpus_text`（逐行校验、坏行跳过并统计、有效行合并写入，与 g2p_phoneme 同口径）、`export_corpus_text`（返回内容供下载）、`export_corpus_to_file`（落盘 exports/）、`export_profiles_text`（返回 JSON 文本）、`export_profiles_to_file`（落盘 exports/）；`import_profiles_text` 复用校验白名单 |
| `app\Scripts\vox_web_ui.py` | 新增 4 个路由：`POST /api/corpus/import`、`POST /api/corpus/export`、`POST /api/profiles/import`、`POST /api/profiles/export`；「编辑多音字语料」弹窗头部新增「导出语料」「导入语料」按钮 + 文件选择；「音色档案管理」弹窗头部新增「导出档案」「导入档案」按钮 + 文件选择；JS 新增 `downloadBlob / exportCorpusFile / importCorpusFile / exportProfilesFile / importProfilesFile`（导入后刷新编辑器/列表，坏行以 toast + console 提示） |
| `app\Scripts\voxcpm_tts_v5_longtext.py` | 新增 `--corpus-export [PATH]`、`--corpus-import PATH`、`--profile-export [PATH]`、`--profile-import PATH`，缺省路径自动生成 `exports/` 文件 |

### 3.2 导入格式与校验规则

- 语料每行格式（与 `g2p_phoneme._OVERLAY_RE` 一致）：
  `上下文词 · 目标字: pypinyin默认=X → 强制=Y`（TONE3 数字声调，ü 写作 v，# 开头为注释）
- 校验失败（格式不符 / 目标字不在上下文词中）的语料行：跳过不写入，返回 `skipped` 与 `skipped_detail`（行号+原文）；
- profile 导入必须是 JSON 数组；缺 `name` 的项跳过；合法项仅保留字段白名单 `(voice, control_text, mode, reference_wav_path, prompt_text, created_at)`，同名覆盖；
- 语料导入为**合并**（保留现有内容 + 追加有效行，last-wins 语义与 g2p 一致），profile 导入为**合并覆盖同名**。

### 3.3 导出文件位置

`app\Scripts\exports\`（运行时产物，不入库，仅本任务验证生成示例后可自行清理）：
- `corpus_user_override_<ts>.txt`（UTF-8 语料）
- `voxcpm_profiles_<ts>.json`（档案 JSON）

---

## 四、实测记录（2026-09-19）

### 4.1 单元级（voxcpm_api 直调）

- `import_corpus_text`：4 条合法 + 1 条坏行 → `imported=4, skipped=1`，坏行带行号返回；
- `export_corpus_text / export_corpus_to_file`：返回内容与落盘文件正常（UTF-8）；
- `import_profiles_text`：2 条合法 + 1 条坏项 → `imported=2, skipped=1`；
- `export_profiles_text / export_profiles_to_file`：返回 JSON 与落盘正常；
- 测试数据已清理（语料 git checkout 恢复原状，档案删除，exports 测试文件删除）。

### 4.2 Web 路由级（端口 19002 实测）

- `POST /api/corpus/import`：curl 传入 2 条合法 + 1 条坏行 → `{"ok":true,"imported":3,"skipped":1,...}`（注释行计入保留，坏行跳过）；
- `POST /api/corpus/export`：返回 `content` 完整语料（含模板注释 + 3 条已核对规则）+ `file_path` 落盘；
- `POST /api/profiles/import`：2 条合法 + 1 条坏项 → `imported=2, skipped=1`；
- `POST /api/profiles/export`：返回 JSON 数组 + `file_path` 落盘；
- `GET /api/corpus`：导入后读取确认合并生效；
- 测试服务已停止，测试档案/语料测试行已清理。

### 4.3 用户可复现的验证命令

```bash
cd D:\AI\Build\VoxCPM2Dist_fixed\app\Scripts
# Web 方式
D:\AI\Build\VoxCPM2Dist_fixed\app\python_cuda\python.exe vox_web_ui.py --port 19001
# 浏览器打开 http://127.0.0.1:19001 → 音色设计区「编辑多音字语料」/「音色档案」弹窗内点导入/导出

# CLI 方式（同一公共后端）
D:\AI\Build\VoxCPM2Dist_fixed\app\python_cuda\python.exe voxcpm_tts_v5_longtext.py --corpus-export
D:\AI\Build\VoxCPM2Dist_fixed\app\python_cuda\python.exe voxcpm_tts_v5_longtext.py --profile-export
# 模型目录环境变量验证
set VOXCPM_MODELS_DIR=D:\some\models
D:\AI\Build\VoxCPM2Dist_fixed\app\python_cuda\python.exe voxcpm_tts_v5_longtext.py --show-config
# 启动横幅 / GET /api/status 的 model_dir 字段应显示 D:\some\models
```

---

## 五、注意事项

- 原项目 `D:\AI\Build\VoxCPM2Dist` 仅新增本文档，代码未改动；
- 副本 `D:\AI\Build\VoxCPM2Dist_fixed` 三个 commit 均为本地提交，未配置远程推送；
- `app\Scripts\exports\`、`verify_*/`、`test_out/`、`voxcpm_profiles.json` 等为运行时产物，不入库；
- 语料导入采用 last-wins 合并，重复上下文位置以最后规则为准（与 g2p 加载语义一致）。
*（内容由AI生成，仅供参考）*
