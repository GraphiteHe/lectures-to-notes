"""Desktop interface for the existing course conversion commands."""
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from tkinter import filedialog, font, messagebox, ttk

from pipeline_config import DEFAULT_CONFIG, HERE, PROJECT_DIR, PipelineError, load_config
from video_to_notes import read_json, write_json

EXAMPLE = HERE / "config.example.json"
LEGACY = PROJECT_DIR / "legacy" / "legacy_convert"
FIELDS = (
    ("input_dir", "输入目录"), ("output_dir", "输出目录"),
    ("api_key", "百炼 API Key"), ("base_url", "服务地址"),
    ("asr_model", "转写模型"), ("asr_language", "语种"),
    ("llm_model", "笔记模型"), ("segment_seconds", "分片秒数"),
    ("realtime_ws_url", "实时转写地址"), ("asr_vocabulary_id", "实时转写热词表 ID"),
)


class LectureApp:
    def __init__(self, root):
        self.root = root
        root.title("课程转写与笔记")
        root.geometry("950x730")
        root.minsize(760, 580)
        families = {name.casefold(): name for name in font.families(root)}
        family = next((families[name.casefold()] for name in
                       ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "PingFang SC",
                        "Noto Sans CJK SC") if name.casefold() in families), None)
        if family:
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
                font.nametofont(name, root=root).configure(family=family, size=10)
            style = ttk.Style(root)
            style.configure("TNotebook.Tab", font="TkDefaultFont")
            style.configure("TEntry", font="TkTextFont")
            style.configure("TCombobox", font="TkTextFont")
        self.editor_font = font.nametofont("TkTextFont", root=root).copy()
        self.editor_font.configure(size=11)
        self.config_path = DEFAULT_CONFIG
        self.events = queue.Queue()
        self.process = None
        self.task_temp = None
        self.stopping = False
        self.values = {}
        self.media = tk.StringVar()
        self.legacy = tk.BooleanVar()
        self.only_transcript = tk.BooleanVar()
        self.coarse_srt = tk.BooleanVar()
        self.retry_empty = tk.BooleanVar()
        self.force_notes = tk.BooleanVar()
        self.status = tk.StringVar(value="就绪")
        self.make_ui()
        self.load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(80, self.poll)

    def make_ui(self):
        tabs = ttk.Notebook(self.root)
        tabs.pack(fill="both", expand=True, padx=14, pady=(14, 6))
        run = ttk.Frame(tabs, padding=14)
        settings = ttk.Frame(tabs, padding=14)
        prompts = ttk.Frame(tabs, padding=14)
        tabs.add(run, text="处理课程")
        tabs.add(settings, text="路径与 API")
        tabs.add(prompts, text="提示词")
        run.columnconfigure(0, weight=1)
        run.rowconfigure(5, weight=1)
        self.tabs, self.settings_tab = tabs, settings

        ttk.Label(run, text="单文件或批量").grid(row=0, column=0, sticky="w")
        selector = ttk.Frame(run)
        selector.grid(row=1, column=0, sticky="ew", pady=(5, 10))
        selector.columnconfigure(0, weight=1)
        ttk.Entry(selector, textvariable=self.media).grid(row=0, column=0, sticky="ew")
        ttk.Button(selector, text="选音视频", command=self.choose_media).grid(row=0, column=1, padx=5)
        ttk.Button(selector, text="选转写 JSON", command=lambda: self.choose_media(True)).grid(row=0, column=2)
        options = ttk.Frame(run)
        options.grid(row=2, column=0, sticky="w", pady=(0, 8))
        for label, value in (
            ("只导出转写", self.only_transcript), ("导出分片 SRT", self.coarse_srt),
            ("重试空白片段", self.retry_empty), ("重做笔记", self.force_notes),
            ("批量复用旧转写", self.legacy),
        ):
            ttk.Checkbutton(options, text=label, variable=value).pack(side="left", padx=(0, 12))
        actions = ttk.Frame(run)
        actions.grid(row=3, column=0, sticky="ew")
        self.run_buttons = []
        for label, mode in (("处理所选文件", "single"), ("预览批量", "preview"), ("开始批量", "batch"),
                            ("测试文本 API", "api"), ("测试转写 API", "asr")):
            button = ttk.Button(actions, text=label, command=lambda mode=mode: self.start(mode))
            button.pack(side="left", padx=(0, 7))
            self.run_buttons.append(button)
        self.stop_button = ttk.Button(actions, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Label(run, text="运行日志").grid(row=4, column=0, sticky="w", pady=(16, 5))
        self.logs = self.text_area(run, 5)
        self.logs.configure(state="disabled")

        settings.columnconfigure(1, weight=1)
        self.config_label = ttk.Label(settings)
        self.config_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 9))
        for row, (key, label) in enumerate(FIELDS, 1):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            variable = self.values[key] = tk.StringVar()
            if key == "asr_model":
                entry = ttk.Combobox(settings, textvariable=variable,
                                     values=("qwen3-asr-flash", "paraformer-realtime-v2"))
            else:
                entry = ttk.Entry(settings, textvariable=variable, show="•" if key == "api_key" else "")
            entry.grid(row=row, column=1, sticky="ew")
            if key in ("input_dir", "output_dir"):
                ttk.Button(settings, text="选择目录", command=lambda key=key: self.choose_directory(key)).grid(
                    row=row, column=2, padx=(8, 0))
        ttk.Label(settings, text="环境变量 DASHSCOPE_API_KEY 优先。工作空间地址按百炼控制台填写。",
                  wraplength=640).grid(row=12, column=0, columnspan=3, sticky="w", pady=(10, 4))
        settings_actions = ttk.Frame(settings)
        settings_actions.grid(row=13, column=1, sticky="w")
        ttk.Button(settings_actions, text="打开配置文件", command=lambda: self.open_path(self.config_path)).pack(
            side="left", padx=(0, 8))
        self.reload_button = ttk.Button(settings_actions, text="重新读取配置", command=self.load_settings)
        self.reload_button.pack(side="left")

        prompts.columnconfigure(0, weight=1)
        prompts.rowconfigure(1, weight=1)
        prompts.rowconfigure(3, weight=3)
        ttk.Label(prompts, text="转写上下文 · Qwen3-ASR-Flash 可填课程背景、专业术语和英文词。").grid(
            row=0, column=0, sticky="w", pady=(0, 5))
        self.asr_prompt = self.text_area(prompts, 1, 4)
        ttk.Label(prompts, text="笔记提示词 · 可自由编辑，整节字幕生成 Markdown 笔记。").grid(
            row=2, column=0, sticky="w", pady=(12, 5))
        self.notes_prompt = self.text_area(prompts, 3, 15)
        ttk.Button(prompts, text="恢复默认提示词", command=self.reset_prompts).grid(
            row=4, column=0, sticky="w", pady=(8, 0))

        footer = ttk.Frame(self.root, padding=(14, 3, 14, 12))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status).pack(side="left")
        ttk.Button(footer, text="打开输出目录", command=self.open_output).pack(side="right")
        self.save_button = ttk.Button(footer, text="保存配置", command=self.save_settings)
        self.save_button.pack(side="right", padx=8)

    def text_area(self, parent, row, height=10):
        area = ttk.Frame(parent)
        area.grid(row=row, column=0, sticky="nsew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        text = tk.Text(area, height=height, wrap="word", undo=True, padx=10, pady=8,
                       font=self.editor_font, spacing1=2, spacing3=2)
        scroll = ttk.Scrollbar(area, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        return text

    def load_settings(self):
        try:
            self.defaults = read_json(EXAMPLE)
            self.settings = self.defaults.copy()
            if self.config_path.is_file():
                self.settings.update(read_json(self.config_path))
            for key, variable in self.values.items():
                variable.set(self.settings[key])
            self.set_prompts(self.settings)
            self.config_label.configure(text=f"本地配置  {self.config_path}")
            self.status.set("配置已读取")
        except (PipelineError, TypeError, KeyError, ValueError):
            messagebox.showerror("配置无法读取", "请检查 config.local.json 和 config.example.json。")
            self.root.destroy()

    def set_prompts(self, settings):
        for widget, key in ((self.asr_prompt, "asr_prompt"), (self.notes_prompt, "notes_prompt")):
            value = settings[key]
            widget.delete("1.0", "end")
            widget.insert("1.0", "\n".join(value) if isinstance(value, list) else value)

    def reset_prompts(self):
        self.set_prompts(self.defaults)

    def save_settings(self):
        config = self.settings.copy()
        for key, variable in self.values.items():
            config[key] = variable.get().strip()
        try:
            config["segment_seconds"] = int(config["segment_seconds"])
        except ValueError:
            messagebox.showerror("无法保存", "分片秒数应为整数。")
            return False
        config["asr_prompt"] = self.asr_prompt.get("1.0", "end-1c").strip()
        config["notes_prompt"] = self.notes_prompt.get("1.0", "end-1c").strip().splitlines()
        try:
            load_config(SimpleNamespace(config=self.config_path), values=config)
            write_json(self.config_path, config)
        except (PipelineError, OSError) as exc:
            messagebox.showerror("无法保存", str(exc) if isinstance(exc, PipelineError) else "无法写入配置文件。")
            return False
        self.settings = config
        self.status.set("配置已保存")
        return True

    def choose_media(self, transcript=False):
        directory = (self.config_path.parent / self.values["input_dir"].get()).resolve()
        filetypes = [("转写 JSON", "*.json")] if transcript else [("音视频", "*.mp4 *.wav *.m4a *.mp3 *.mkv *.mov *.flac *.webm"), ("所有文件", "*.*")]
        if transcript and LEGACY.is_dir():
            directory = LEGACY
        name = filedialog.askopenfilename(initialdir=directory, filetypes=filetypes)
        if name:
            self.media.set(name)

    def choose_directory(self, key):
        initial = (self.config_path.parent / self.values[key].get()).resolve()
        path = filedialog.askdirectory(initialdir=initial if initial.is_dir() else initial.parent)
        if path:
            self.values[key].set(path)

    @staticmethod
    def open_path(path):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        except OSError:
            messagebox.showerror("无法打开", str(path))

    def open_output(self):
        try:
            config = load_config(SimpleNamespace(config=None))
            config["output_dir"].mkdir(parents=True, exist_ok=True)
            self.open_path(config["output_dir"])
        except (PipelineError, OSError):
            messagebox.showerror("无法打开输出目录", "请先保存有效的输出目录配置。")

    def command_for(self, mode):
        path = self.media.get().strip()
        transcript = Path(path).suffix.lower() == ".json"
        if mode == "api":
            return ["api_test.py"]
        if mode in ("single", "asr"):
            if not path or (mode == "asr" and transcript):
                raise PipelineError("请先选择音视频文件。" if mode == "asr" else "请先选择音视频或转写 JSON。")
            if mode == "asr":
                return ["asr_test.py", path]
            command = ["video_to_notes.py", "--transcript", path] if transcript else ["video_to_notes.py", path]
        else:
            command = ["batch_run.py"]
            if self.legacy.get():
                command.extend(["--reuse-legacy", str(LEGACY)])
            if mode == "preview":
                return command + ["--dry-run"]
        if self.retry_empty.get() and ((mode == "single" and transcript) or (mode == "batch" and self.legacy.get())):
            raise PipelineError("重试空白片段需要从音视频重新调用转写。")
        for state, flag in ((self.only_transcript, "--skip-llm"), (self.coarse_srt, "--coarse-srt"),
                            (self.retry_empty, "--retry-empty"), (self.force_notes, "--force")):
            if state.get():
                command.append(flag)
        return command

    def start(self, mode):
        if self.process is not None:
            return
        try:
            command = self.command_for(mode)
            if not self.save_settings():
                return
            python = Path(sys.executable)
            if python.stem.lower() == "pythonw" and python.with_name("python.exe").is_file():
                python = python.with_name("python.exe")
            self.task_temp = tempfile.TemporaryDirectory(prefix="lecture-app-")
            temp = self.task_temp.name
            self.process = subprocess.Popen(
                [str(python), "-u", str(HERE / command[0]), *command[1:]],
                cwd=PROJECT_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                         TMPDIR=temp, TEMP=temp, TMP=temp),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, PipelineError) as exc:
            self.clean_task_temp()
            messagebox.showerror("无法开始", str(exc) if isinstance(exc, PipelineError) else "无法启动处理程序。")
            return
        self.stopping = False
        self.set_running(True)
        self.add_log("\n── 新任务 ──\n")
        threading.Thread(target=self.read_process, args=(self.process,), daemon=True).start()

    def read_process(self, process):
        with process.stdout:
            for line in process.stdout:
                self.events.put(("line", line))
        self.events.put(("exit", process.wait()))

    def clean_task_temp(self):
        if self.task_temp is not None:
            try:
                self.task_temp.cleanup()
            except OSError:
                self.add_log("[提示] 临时目录清理失败，请关闭正在占用临时文件的程序。\n")
            self.task_temp = None

    def set_running(self, running):
        self.status.set("处理中" if running else "就绪")
        for button in [*self.run_buttons, self.save_button, self.reload_button]:
            button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def add_log(self, text):
        self.logs.configure(state="normal")
        self.logs.insert("end", text)
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def poll(self):
        for _ in range(200):
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "line":
                self.add_log(value)
            else:
                self.process = None
                self.clean_task_temp()
                self.set_running(False)
                self.status.set("已停止" if self.stopping else "处理成功" if value == 0 else "处理失败，请查看日志")
        self.root.after(80, self.poll)

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.stopping = True
            self.process.terminate()
            self.status.set("正在停止")
            self.add_log("[停止] 已完成的分片可在下次继续使用。\n")

    def close(self):
        if self.process is not None:
            if not messagebox.askyesno("退出", "任务正在运行。停止后退出？"):
                return
            self.stop()
            self.root.after(80, self.close_after_exit)
            return
        self.root.destroy()

    def close_after_exit(self):
        if self.process is None:
            self.root.destroy()
        else:
            self.root.after(80, self.close_after_exit)


def main():
    root = tk.Tk()
    LectureApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

