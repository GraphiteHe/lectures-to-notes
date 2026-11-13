"""Shared configuration and concise, credential-safe command-line errors."""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
PROGRAM_DIR = HERE.parent
PROJECT_DIR = PROGRAM_DIR.parent
DEFAULT_CONFIG = HERE / "config.local.json"
DEFAULTS = {
    "api_key": "",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "asr_model": "qwen3-asr-flash",
    "llm_model": "qwen-flash",
    "asr_language": "auto",
    "segment_seconds": 60,
    "timeout_seconds": 120,
    "realtime_ws_url": "",
    "glossary": "",
    "input_dir": "../../videos",
    "output_dir": "../../results",
    "notes_prompt": [],
    "asr_prompt": "",
    "asr_vocabulary_id": "",
}


class PipelineError(Exception):
    pass


class ServiceError(PipelineError):
    def __init__(self, status=None, code="", request_id="", transient=False):
        self.status = status
        self.transient = transient
        self.code = str(code).lower()
        if status == 401 or "invalidapikey" in self.code:
            message = "鉴权失败，请核对 Key 与工作空间服务地址。"
        elif status == 402 or "arrear" in self.code or "insufficientquota" in self.code:
            message = "请检查百炼账户余额、可用额度和模型权限。"
        elif status == 403:
            message = "访问受限，请检查模型授权、账户状态和工作空间。"
        elif status == 404 or "modelnotfound" in self.code:
            message = "找不到接口或模型，请核对服务地址及模型名。"
        elif status == 429:
            message = "请求限流或额度不足，请检查百炼额度后重试。"
        elif status is None:
            message = "服务连接失败或超时，请检查网络和服务地址。"
        else:
            message = f"服务拒绝请求（HTTP {status}），请核对模型、接口与输入。"
        # Request IDs from the provider may be shown, never the raw response.
        import re
        if re.fullmatch(r"[0-9a-fA-F]{8,64}(?:-[0-9a-fA-F]{4,16}){0,5}", str(request_id)):
            message += f" 请求 ID {request_id}。"
        super().__init__(message)


def add_config_args(parser):
    parser.add_argument("--config", type=Path, help="配置文件，默认读取脚本旁的 config.local.json")
    for option in ("base_url", "asr_model", "llm_model", "asr_language", "segment_seconds"):
        kind = int if option == "segment_seconds" else str
        parser.add_argument("--" + option.replace("_", "-"), type=kind)
    parser.add_argument("--chunk-chars", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help="显示额外诊断信息，隐藏请求正文和凭据")


def load_config(args, values=None):
    config = DEFAULTS.copy()
    try:
        config["notes_prompt"] = json.loads(
            (HERE / "config.example.json").read_text(encoding="utf-8-sig")
        )["notes_prompt"]
    except (OSError, ValueError, KeyError):
        raise PipelineError("找不到有效的 config.example.json，请与程序一起保留。") from None
    path = args.config or DEFAULT_CONFIG
    if values is None and (args.config or path.exists()):
        try:
            values = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            raise PipelineError(f"无法读取配置 {path.name}，请检查 JSON 格式和路径。") from None
    if values is not None:
        if not isinstance(values, dict) or values.keys() - config.keys() - {"chunk_chars"}:
            raise PipelineError("配置含有未知字段，请对照 config.example.json。")
        config.update({key: value for key, value in values.items() if key != "chunk_chars"})
    for key in config:
        override = getattr(args, key, None)
        if override is not None:
            config[key] = override
    config["api_key"] = os.environ.get("DASHSCOPE_API_KEY") or config["api_key"]
    for key, default in DEFAULTS.items():
        if key == "notes_prompt" and isinstance(config[key], str):
            config[key] = config[key].splitlines()
        if type(config[key]) is not type(default):
            raise PipelineError(f"配置字段 {key} 的类型不正确。")
        if isinstance(default, str):
            config[key] = config[key].strip()
    if not config["notes_prompt"] or any(not isinstance(line, str) for line in config["notes_prompt"]):
        raise PipelineError("notes_prompt 应为非空提示词字符串或字符串数组。")
    config["notes_prompt"] = "\n".join(config["notes_prompt"]).strip()
    if not config["notes_prompt"] or not config["input_dir"] or not config["output_dir"]:
        raise PipelineError("输入目录、输出目录和笔记提示词不能为空。")
    for key in ("input_dir", "output_dir"):
        config[key] = (path.parent / config[key]).resolve()
    validate_output(config["output_dir"], config["input_dir"])
    for key in ("base_url", "realtime_ws_url"):
        value = config[key]
        if key == "realtime_ws_url" and not value:
            continue
        url = urlsplit(value)
        scheme = "https" if key == "base_url" else "wss"
        if url.scheme != scheme or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise PipelineError(f"配置字段 {key} 需要无凭据的 {scheme} 地址。")
    config["base_url"] = config["base_url"].rstrip("/")
    if not 1 <= config["segment_seconds"] <= 180:
        raise PipelineError("segment_seconds 须在 1–180 秒之间。")
    if config["timeout_seconds"] < 1:
        raise PipelineError("timeout_seconds 须大于 0。")
    if not all(config[key] for key in ("asr_model", "llm_model", "asr_language")):
        raise PipelineError("模型名与语种设置不能为空，自动语种请填 auto。")
    if config["input_dir"] == PROGRAM_DIR or PROGRAM_DIR in config["input_dir"].parents:
        raise PipelineError("输入目录应放在程序目录之外。")
    if config["glossary"]:
        try:
            config["glossary"] = (path.parent / config["glossary"]).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            raise PipelineError("无法读取 glossary 指定的 UTF-8 术语表。") from None
    return config


def validate_output(output, input_dir):
    output = output.resolve()
    if output == input_dir or input_dir in output.parents:
        raise PipelineError("输出目录应与输入目录分开。")
    if output == PROGRAM_DIR or PROGRAM_DIR in output.parents:
        raise PipelineError("输出目录应放在程序目录之外。")


def resolve_input(path, config):
    if not path.is_absolute() and not path.exists():
        return config["input_dir"] / path
    return path


def require_key(config):
    if not config["api_key"]:
        raise PipelineError("缺少 API Key，请填写 config.local.json 或设置 DASHSCOPE_API_KEY。")


def new_client(config):
    require_key(config)
    try:
        from openai import OpenAI
    except ImportError:
        raise PipelineError("缺少 OpenAI SDK，请运行 python -m pip install -r requirements.txt。") from None
    return OpenAI(api_key=config["api_key"], base_url=config["base_url"],
                  max_retries=0, timeout=config["timeout_seconds"])


def log(status, message):
    print(f"[{status}] {message}", flush=True)


def retry_call(operation, label, debug=False):
    for attempt in range(1, 4):
        try:
            return operation()
        except PipelineError as exc:
            if not isinstance(exc, ServiceError):
                raise
            error = exc
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if not isinstance(status, int):
                status = None
            name = type(exc).__name__
            if debug:
                log("诊断", f"{label} · {name}")
            if status is None and not any(part in name.lower() for part in ("connection", "timeout", "connect")):
                raise PipelineError(f"{label}出现异常（{name}）。") from None
            error = ServiceError(status, getattr(exc, "code", ""),
                                 getattr(exc, "request_id", ""),
                                 transient=status is None or status in (408, 429) or status >= 500)
        if not error.transient or attempt == 3:
            raise error from None
        log("重试", f"{label} · 第 {attempt + 1}/3 次。{error}")
        time.sleep(2 ** (attempt - 1))


def run_cli(main):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace", line_buffering=True)
    logging.getLogger("dashscope").setLevel(logging.CRITICAL + 1)
    try:
        return main() or 0
    except KeyboardInterrupt:
        log("中止", "已停止，可用相同命令继续。")
        return 130
    except PipelineError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Unexpected exceptions may contain request data. Show their type only.
        print(f"[失败] 运行异常（{type(exc).__name__}）。请检查配置、输入与写入权限。", file=sys.stderr)
        return 1

