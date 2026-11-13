# lectures-to-notes

把本地课程录音或录像转成带时间的转写和 Markdown 学习笔记。也可以导入已有的转写 JSON，直接生成笔记。

- **字幕和转写**：使用支持逐句时间的转写模型生成 SRT。也可选择分片级 SRT，用于粗略定位。
- **学习笔记**：文本模型阅读整节带时间的字幕，生成包含知识点、课务原话和待回看事项的 `notes.md`。可以在窗口里修改默认提示词。
- **输入和结果**：音视频来自你的电脑，结果保存到你选择的目录。转写时会把音频分片发送到配置的服务，生成笔记时会发送带时间的转写文本。服务可能按用量计费。

## Windows 快速开始

1. 在 GitHub 仓库点 **Code → Download ZIP**，解压到一个新文件夹。
2. 安装 Python 3.9 或更新版本，保留 Tkinter，并让终端能找到 `python`。在解压目录打开 PowerShell，运行：

   ```powershell
   python -m pip install -r .\convert\app\requirements.txt
   ```

   使用视频或需要转换格式的音频时，安装 FFmpeg 并加入 PATH。运行 `ffmpeg -version` 确认终端能找到它。导入转写 JSON 或使用符合要求的 PCM WAV 时，可直接开始。
3. 双击 `convert\start_app.cmd`。在 **路径与 API** 选择自己的输入和输出目录，填写自己的百炼 API Key。默认服务地址适用于百炼北京经典兼容接口，其他地域或工作空间请按控制台填写。
4. 回到 **处理课程**，选择音视频或转写 JSON，点 **处理所选文件**。任务完成后，点 **打开输出目录** 查看 `notes.md`。如果只需要字幕，勾选 **只导出转写**。

想用命令行、批量处理或导出逐句 SRT，请看[完整使用说明](convert/readme.md)。

## 本地配置和数据

程序示例配置在 [config.example.json](convert/app/config.example.json)，其中 API Key 留空。程序把你的设置保存到本机的 `config.local.json`。请把课程资料和处理结果留在自己的输入、输出目录。仓库根目录的忽略规则适用于通过 Git 提交的文件。通过 GitHub 网页上传时，请手动核对文件清单。
