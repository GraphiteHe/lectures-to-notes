#!/usr/bin/env python3
"""Check the selected speech backend on the first seconds of a local sample."""
import argparse
import sys
import tempfile
import wave
from pathlib import Path

from pipeline_config import PipelineError, add_config_args, load_config, log, require_key, resolve_input, run_cli
from video_to_notes import RATE, audio_for, transcribe


def main():
    parser = argparse.ArgumentParser(description="用真实语音测试配置中的 ASR，默认只发送前 15 秒")
    parser.add_argument("media", type=Path, help="有老师说话的短片段")
    parser.add_argument("--seconds", type=int, default=15)
    add_config_args(parser)
    args = parser.parse_args()
    config = load_config(args)
    args.media = resolve_input(args.media, config)
    if not 1 <= args.seconds <= 60 or not args.media.is_file():
        raise PipelineError("请提供有效的音视频文件，--seconds 范围为 1–60。")
    require_key(config)
    args.retry_empty = False
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory)
        audio = audio_for(args.media, out, False, args.debug)
        sample = out / "sample.wav"
        with wave.open(str(audio), "rb") as source, wave.open(str(sample), "wb") as target:
            target.setparams(source.getparams())
            target.writeframes(source.readframes(args.seconds * RATE))
        rows, _ = transcribe(sample, out, config, args)
        if not rows:
            raise PipelineError("接口已返回，但片段无转写文字。请换一段有清楚人声的录音。")
    log("成功", f"转写接口已返回 {len(rows)} 条，共 {sum(len(x['text']) for x in rows)} 字。")


if __name__ == "__main__":
    sys.exit(run_cli(main))

