# lectures-to-notes 使用说明

输入本地音频、视频或已有转写。流程将整节带时间的字幕交给文本模型，直接生成 Markdown 学习笔记。默认提示词要求课务事项附可核对的原话和时间。视频输入目前只提取声音，PPT 上未念出的文字和手写板书需要回看。

## 安装和配置

需要 Python 3.9+，以及可在命令行运行的 FFmpeg（使用已有转写或直接读取标准 PCM WAV 时无需 FFmpeg）。在 `lectures-to-notes` 项目目录新建 `videos` 并放入自己的录制文件，或在应用里选择输入目录。以下命令在 PowerShell 的 `convert/app` 目录中运行。

```powershell
python -m pip install -r requirements.txt
if (-not (Test-Path config.local.json)) { Copy-Item config.example.json config.local.json }
```

用文本编辑器打开 `config.local.json`，填入自己的 `api_key` 和百炼控制台提供的 `base_url`。也可以在启动进程前设置 `DASHSCOPE_API_KEY`，环境变量优先于文件里的 Key。`--config` 可选择其他 JSON，支持的命令行参数覆盖文件中对应设置。默认配置从脚本旁读取。配置里的相对路径以配置文件所在目录为基准。命令行路径先相对当前终端解析，媒体找不到时再到配置的输入目录查找。

## 桌面应用

Windows 双击上一级目录中的 `start_app.cmd`。也可以运行 `python lecture_app.pyw`。应用使用 Python 自带的 Tkinter，仍需安装上面的依赖。

- 在「路径与 API」选择输入和输出目录，填写 Key、地址和模型。
- 在「提示词」编辑转写上下文和笔记提示词，点「保存配置」写入同一个 `config.local.json`。开始任务前也会自动保存。
- 在「处理课程」选单个音视频或旧转写 JSON。批量前可先预览文件名单，勾选「批量复用旧转写」会读取项目中的 `legacy/legacy_convert`。
- 任务运行时可查看日志、停止任务和打开输出目录。可复用已经完成的转写分片和完整笔记；整节笔记请求中途取消后需重新生成。
- 分片 WAV 只在转写请求期间使用，请求结束后自动删除。结果目录中的点号开头文件供本机续跑使用。

每个人单独创建自己的本地配置。分享时只打包启动器、程序脚本、`requirements.txt`、`config.example.json` 和本说明。运行在线步骤会向配置中的服务发送音频或转写，并按服务规则计费。

| 配置 | 默认值与用途 |
|---|---|
| `input_dir`、`output_dir` | 默认 `../../videos`、`../../results`，与程序目录并列。已有 WAV 可改为 `../../audios`，也可填绝对路径。 |
| `asr_prompt` | Qwen3-ASR-Flash 的可选转写上下文。填写课程背景和术语，如「固体物理：布拉伐格子、倒格矢、Brillouin zone」。 |
| `asr_vocabulary_id` | Paraformer 实时接口的可选百炼热词表 ID。使用此后端时清空 `asr_prompt`。 |
| `notes_prompt` | 自定义笔记提示词，支持非空字符串或按行写成数组。可自由修改重点、语言和篇幅，默认要求直接输出 Markdown。 |
| `api_key`、`base_url` | Key 留空。地址默认百炼北京经典兼容接口，工作空间或其他地域按控制台提供的地址设置。 |
| `asr_model` | `qwen3-asr-flash`。本地录音经兼容接口转写，返回分片级时间。需要逐句字幕时可试 `paraformer-realtime-v2`。 |
| `asr_language` | `auto`。课程夹有较长英文可用自动；普通话为主时可对照 `--asr-language zh`。单一语言提示并非中英混合专用设置。 |
| `segment_seconds` | 60 秒，允许 1–180 秒。越短定位越细、请求越多。建议先用两分钟片段比较。 |
| `llm_model` | 默认 `qwen-flash`。模型需容纳整节字幕和提示词。若输出被截断，可缩短笔记要求或换用输出上限更高的模型。 |
| `timeout_seconds` | 兼容 HTTP 接口超时，默认 120 秒。 |
| `realtime_ws_url` | 实时后端 WebSocket 地址。北京经典地址可留空；使用其他地域或自定义兼容地址时，填与该服务匹配的 `wss://` 地址。 |
| `glossary` | 可选 UTF-8 术语表文件，相对配置文件。每行可放一个术语及英文，如「布拉伐格子 Bravais lattice」。用于笔记术语核对。 |

[百炼 Qwen-ASR 接口说明](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference) · [Paraformer 实时 SDK](https://help.aliyun.com/zh/model-studio/paraformer-real-time-speech-recognition-python-sdk)

## 先跑样片

```powershell
python api_test.py
python asr_test.py "../../sample/01_spoken_chinese_05m14s.mp4"
python video_to_notes.py "../../sample/01_spoken_chinese_05m14s.mp4" --source-offset 314
```

分享包里请将示例路径替换为自己的媒体。前两条可单独诊断文本与转写接口。转写测试默认只提交样片前 15 秒。第三条的笔记时间加 314 秒，指向整节课；转写与 SRT 始终以输入媒体的零点计时。术语样片可以用 `--source-offset 2760`，公式样片用 `--source-offset 2100`。

需要可播放的逐句 SRT 时，先短测实时后端及账号权限：

```powershell
python asr_test.py "../../sample/03_formula_derivation_35m00s.mp4" --asr-model paraformer-realtime-v2
python video_to_notes.py "../../sample/03_formula_derivation_35m00s.mp4" --asr-model paraformer-realtime-v2 --skip-llm --out-dir "../../results/formula_sentence"
```

`qwen3-asr-flash` 的时间只精确到分片。需要粗略定位可加 `--coarse-srt`，文件为 `transcript.segments.srt`。每条可能显示整分钟的文字，适合定位片段。代码不会把文字均摊成估算的逐句时间。原教程中的 `paraformer-v2` 属于另一套录音文件 API。新版用明确指定的转写后端，避免静默切换。

## 复用已有转写和批量处理

用旧转写重做笔记时只调用文本模型。示例中的 `--out-dir` 也可以省略，默认写入配置的输出目录下的 `notes_<文件名>`。

```powershell
python video_to_notes.py --transcript "../../legacy/legacy_convert/notes_课程名称/transcript.json" --out-dir "../../results/revised"
python batch_run.py --reuse-legacy ../../legacy/legacy_convert --dry-run
python batch_run.py --reuse-legacy ../../legacy/legacy_convert
```

新转写整批课程前可先用 `python batch_run.py --dry-run` 核对名单，再用 `python batch_run.py`。批量只扫描目录第一层，个别任务失败会继续处理其余文件。报告写入 `results/batch_report.txt`，存在失败时返回非零退出码。

- `--skip-llm` 只导出转写。媒体输入同时加 `--skip-asr --skip-llm` 可只准备音轨。标准 WAV 会直接使用原文件。
- 相同音轨、分片和转写设置会复用本地转写进度。更改转写模型、提示词、语种或分片会重新转写。只改笔记提示词会重做笔记并复用转写。`--force` 可强制重新生成笔记。
- 先看 `coverage.md`。有讲话的空白分片可用原设置加 `--retry-empty` 重试。旧缓存中的空白范围可随旧转写用于新笔记。覆盖表列的是检测到的空返回，段内漏句需要人工抽查。

## 输出和排错

| 文件 | 内容 |
|---|---|
| `transcript.txt` | 按时间分行的原始识别文本 |
| `transcript.srt` | 逐句后端或外部逐句转写提供有效起止时间时生成 |
| `coverage.md` | 接口未返回文字的范围 |
| `notes.md` | 知识点、课务原话、公式及术语待核对项 |
| `.asr-progress`、`.transcript-cache`、`.notes-signature` | 本机续跑和复用用的缓存 |

终端会标明转写分片和整节笔记的进度。网络、超时及限流可短暂重试；鉴权或模型不匹配会直接指出。遇到错误先看末行的阶段和操作提示。可加 `--debug` 查看错误类别或 FFmpeg 诊断。


