#!/usr/bin/env python3
"""Run the same pipeline for a directory or a list of lectures."""
import argparse
import sys
from pathlib import Path

from pipeline_config import PipelineError, load_config, log, resolve_input, run_cli, validate_output
from video_to_notes import add_stage_args, process, write_text

MEDIA = set((
    ".wav .mp3 .m4a .aac .flac .ogg .opus .wma .amr .ape "
    ".mp4 .mov .mkv .avi .flv .wmv .ts .mts .webm .m4v .3gp .mpg .mpeg"
).split())


def collect(paths):
    unique = {}
    for path in paths:
        files = sorted(path.iterdir()) if path.is_dir() else [path]
        for file in files:
            if file.is_file() and file.suffix.lower() in MEDIA:
                unique[file.resolve()] = file
            elif not path.is_dir():
                log("跳过", f"无法处理 {file}。")
    return list(unique.values())


def main():
    parser = argparse.ArgumentParser(description="批量生成课程转写与笔记，自动复用匹配的进度。")
    parser.add_argument("paths", nargs="*", type=Path, help="目录或媒体文件，默认配置中的 input_dir")
    parser.add_argument("--out-root", type=Path, help="批量输出根目录，默认配置中的 output_dir")
    parser.add_argument("--out-prefix", default="notes_", help="输出子目录名前缀")
    parser.add_argument("--reuse-legacy", type=Path, help="从这个目录下的 notes_<文件名> 复用旧转写")
    parser.add_argument("--dry-run", action="store_true", help="列出输入和输出，不调用 API")
    add_stage_args(parser)
    args = parser.parse_args()
    config = load_config(args)
    args.out_root = args.out_root or config["output_dir"]
    validate_output(args.out_root, config["input_dir"])
    paths = [resolve_input(path, config) for path in args.paths] or [config["input_dir"]]
    media = collect(paths)
    if not media:
        raise PipelineError("未找到可处理的媒体。")
    if Path(args.out_prefix).name != args.out_prefix and args.out_prefix:
        raise PipelineError("--out-prefix 只能是目录名前缀，请用 --out-root 设置路径。")
    jobs = [(file, args.out_root / (args.out_prefix + file.stem)) for file in media]
    if len({str(out.resolve()).casefold() for _, out in jobs}) != len(jobs):
        raise PipelineError("多个输入会使用同一输出目录，请分批处理同名媒体。")
    if args.reuse_legacy and args.retry_empty:
        raise PipelineError("--reuse-legacy 与 --retry-empty 不能同时使用。")
    log("批量", f"{len(jobs)} 个输入。")
    if args.dry_run:
        for file, out in jobs:
            print(f"{file} → {out}")
        return
    args.out_root.mkdir(parents=True, exist_ok=True)
    report = []
    failed = 0
    for i, (file, out) in enumerate(jobs, 1):
        args.media = file
        args.transcript = (args.reuse_legacy / ("notes_" + file.stem) / "transcript.json"
                           if args.reuse_legacy else None)
        if args.transcript:
            args.skip_asr = True
        log("开始", f"{i}/{len(jobs)} · {file.name}")
        try:
            process(args, config, out)
        except (PipelineError, OSError) as exc:
            message = str(exc) if isinstance(exc, PipelineError) else f"文件读写失败（{type(exc).__name__}）。"
            log("失败", f"{file.name} · {message}")
            report.append(f"[失败] {file.name} · {message}")
            failed += 1
        else:
            report.append(f"[成功] {file.name}")
    summary = f"成功 {len(jobs) - failed}，失败 {failed}，共 {len(jobs)} 个。"
    write_text(args.out_root / "batch_report.txt", summary + "\n\n" + "\n".join(report) + "\n")
    log("完成", summary)
    return int(bool(failed))


if __name__ == "__main__":
    sys.exit(run_cli(main))

