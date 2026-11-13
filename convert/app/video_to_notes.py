#!/usr/bin/env python3
"""Convert a local lecture recording or an existing transcript into study notes."""
import argparse
import base64
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from lecture_notes import generate_notes, notes_payload, time_range, time_text
from pipeline_config import (
    PipelineError, ServiceError, add_config_args, load_config, log, new_client, require_key,
    resolve_input, retry_call, run_cli, validate_output,
)

RATE = 16000
CACHE_VERSION = 1


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise PipelineError(f"无法读取 {path.name}，请检查文件是否完整。") from None


def write_text(path, content):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def fingerprint(path):
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]


@contextlib.contextmanager
def output_lock(out):
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".pipeline.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PipelineError(f"输出目录正在被另一任务使用：{out}") from None
        yield


def wav_frames(path):
    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, RATE):
                return audio.getnframes()
    except (OSError, EOFError, wave.Error):
        pass
    return 0


def audio_for(media, out, skip, debug):
    target = out / "audio.wav"
    if skip:
        if not wav_frames(target):
            raise PipelineError("--skip-audio 需要输出目录中已有有效的 16 kHz 单声道 audio.wav。")
        log("复用", "已有 audio.wav。")
        return target
    if media.suffix.lower() == ".wav" and wav_frames(media):
        log("成功", "音轨准备 · 直接使用输入的 16 kHz 单声道 PCM WAV。")
        return media
    metadata = out / ".audio-source"
    previous_metadata = metadata if metadata.exists() else out / ".audio.json"
    source = fingerprint(media)
    if previous_metadata.exists() and wav_frames(target) and read_json(previous_metadata) == source:
        if previous_metadata != metadata:
            write_json(metadata, source)
        log("复用", "音轨准备 · 已有匹配的 audio.wav。")
        return target
    if not shutil.which("ffmpeg"):
        raise PipelineError("找不到 FFmpeg，请安装后加入 PATH。")
    log("开始", "音轨准备 · 16 kHz 单声道 PCM WAV。")
    with tempfile.TemporaryDirectory(dir=out, prefix=".audio-") as work:
        temporary = Path(work) / "audio.wav"
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(media),
             "-map", "0:a:0", "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(temporary)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode or not wav_frames(temporary):
            if debug and result.stderr.strip():
                log("诊断", result.stderr.strip().splitlines()[-1][:220])
            raise PipelineError("音轨准备失败，请确认文件可播放且有音轨。")
        # Drop the old signature before replacing its audio so an interruption
        # cannot associate the old source with the new WAV.
        metadata.unlink(missing_ok=True)
        temporary.replace(target)
        write_json(metadata, source)
    log("成功", "音轨准备 · audio.wav。")
    return target


def asr_is_coarse(config):
    return (config["asr_model"].startswith("qwen3-asr-flash")
            and not any(kind in config["asr_model"] for kind in ("realtime", "filetrans")))


def validate_asr_config(config):
    if asr_is_coarse(config):
        if config["asr_vocabulary_id"]:
            raise PipelineError("Qwen3-ASR-Flash 请用 asr_prompt 提供领域上下文。")
        return
    if config["asr_model"] not in ("paraformer-realtime-v2", "paraformer-realtime-v1"):
        raise PipelineError("本地录音支持 qwen3-asr-flash 或实时模型，例如 paraformer-realtime-v2。")
    if config["asr_prompt"]:
        raise PipelineError("Paraformer 实时接口请用 asr_vocabulary_id 配置热词，或清空 asr_prompt。")
    if (not config["realtime_ws_url"]
            and config["base_url"] != "https://dashscope.aliyuncs.com/compatible-mode/v1"):
        raise PipelineError("使用自定义服务地址时，请为实时 ASR 明确配置 realtime_ws_url。")


def flash_asr(client, config, path, duration):
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    if len(payload) + 22 > 10_000_000:
        raise PipelineError("分片编码后超过 10 MB，请减小 segment_seconds。")
    options = {"enable_itn": False}
    if config["asr_language"] != "auto":
        options["language"] = config["asr_language"]
    messages = []
    if config["asr_prompt"]:
        messages.append({"role": "system", "content": [{"type": "text", "text": config["asr_prompt"]}]})
    messages.append({"role": "user", "content": [
        {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + payload}}
    ]})
    response = client.chat.completions.create(
        model=config["asr_model"], messages=messages,
        extra_body={"asr_options": options},
    )
    if not response.choices or response.choices[0].finish_reason not in (None, "stop"):
        raise PipelineError("语音回复不完整，请减小 segment_seconds 后重试。")
    message = response.choices[0].message.content
    text = (message or "").strip()
    return [{"start": 0, "end": duration, "text": text, "timing": "segment"}] if text else []


def realtime_asr(config, path, duration):
    try:
        import dashscope
        from dashscope.audio.asr import Recognition
    except ImportError:
        raise PipelineError("缺少 DashScope SDK，请运行 python -m pip install -r requirements.txt。") from None
    dashscope.api_key = config["api_key"]
    # The legacy local-file SDK reads this module variable for WebSocket calls.
    dashscope.base_websocket_api_url = (
        config["realtime_ws_url"] or "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    )
    hints = {} if config["asr_language"] == "auto" else {"language_hints": [config["asr_language"]]}
    recognizer = Recognition(model=config["asr_model"], format="wav", sample_rate=RATE, callback=None)
    reply = recognizer.call(str(path), phrase_id=config["asr_vocabulary_id"] or None, **hints)
    status = reply.status_code
    if status != 200:
        raise ServiceError(status, reply.code, reply.get_request_id(),
                           transient=status in (408, 429) or status >= 500)
    sentences = reply.get_sentence() or []
    if isinstance(sentences, dict):
        sentences = [sentences]
    rows = []
    for item in sentences:
        text = (item.get("text") or "").strip()
        start, end = item.get("begin_time"), item.get("end_time")
        if not text:
            continue
        if type(start) is not int or type(end) is not int or not 0 <= start < min(end, duration):
            raise PipelineError("逐句接口返回无效时间戳，未写入该分片。")
        rows.append({"start": start, "end": min(end, duration), "text": text, "timing": "sentence"})
    return rows


def checked_rows(data):
    if not isinstance(data, list):
        raise PipelineError("转写 JSON 应为包含 start、end、text 的数组。")
    for row in data:
        if (not isinstance(row, dict) or type(row.get("start")) is not int
                or type(row.get("end")) is not int or not 0 <= row["start"] <= row["end"]
                or not isinstance(row.get("text"), str)
                or row.get("timing") not in (None, "segment", "sentence")):
            raise PipelineError("转写条目无效，请检查毫秒时间戳和文本字段。")
    return sorted(data, key=lambda row: row["start"])


def transcribe(audio_path, out, config, args):
    validate_asr_config(config)
    settings = {key: config[key] for key in (
        "base_url", "realtime_ws_url", "asr_model", "asr_language", "segment_seconds",
        "asr_prompt", "asr_vocabulary_id")}
    cache_id = signature([CACHE_VERSION, fingerprint(audio_path), settings])
    cache_path = out / ".asr-progress"
    previous_cache = cache_path if cache_path.exists() else out / "asr_progress.json"
    cache = read_json(previous_cache) if previous_cache.exists() else {}
    if not isinstance(cache, dict) or cache.get("signature") != cache_id:
        cache = {"signature": cache_id, "segments": {}}
    done = cache["segments"]
    if args.retry_empty:
        done = {key: entry for key, entry in done.items() if entry["rows"]}
        cache["segments"] = done
    rows, empty = [], []
    with wave.open(str(audio_path), "rb") as audio, tempfile.TemporaryDirectory(
            prefix="lecture-asr-") as temp:
        stride = config["segment_seconds"] * RATE
        frames = audio.getnframes()
        count = (frames + stride - 1) // stride
        path = Path(temp) / "segment.wav"
        coarse = asr_is_coarse(config)
        client = None
        log("开始", f"转写 · {count} 段 · {config['asr_model']} · {'分片' if coarse else '逐句'}时间。")
        for i, offset in enumerate(range(0, frames, stride)):
            label = f"转写 {i + 1}/{count}"
            key = str(i)
            start = offset * 1000 // RATE
            end = (min(offset + stride, frames) * 1000 + RATE - 1) // RATE
            if key in done:
                segment = checked_rows(done[key]["rows"])
                log("复用", f"{label} · {len(segment)} 条。")
            else:
                require_key(config)
                if coarse and client is None:
                    client = new_client(config)
                audio.setpos(offset)
                raw = audio.readframes(min(stride, frames - offset))
                if len(raw) != min(stride, frames - offset) * 2:
                    raise PipelineError("音轨数据提前结束，请重新准备音轨。")
                log("开始", f"{label} · {time_text(start)}–{time_text(end)}。")

                def request():
                    if not any(raw):
                        return []
                    if coarse:
                        return flash_asr(client, config, path, end - start)
                    return realtime_asr(config, path, end - start)

                try:
                    with wave.open(str(path), "wb") as part:
                        part.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                        part.writeframes(raw)
                    result = retry_call(request, label, args.debug)
                except PipelineError as exc:
                    raise PipelineError(f"{label} · {exc}") from None
                finally:
                    path.unlink(missing_ok=True)
                segment = [dict(row, start=row["start"] + start, end=row["end"] + start) for row in result]
                done[key] = {"rows": segment, "start": start, "end": end}
                write_json(cache_path, cache)
                log("成功" if segment else "提示",
                    f"{label} · {'已保存 ' + str(len(segment)) + ' 条' if segment else '没有识别到文字，已记录待回听范围'}。")
            rows.extend(segment)
            if not segment:
                empty.append({"start": start, "end": end})
    if previous_cache != cache_path and not cache_path.exists() and done:
        write_json(cache_path, cache)
    return checked_rows(rows), empty


def legacy_empty(path):
    coverage = path.with_name("coverage.json")
    if path.name == "transcript.json" and coverage.is_file():
        data = read_json(coverage)
        if isinstance(data, dict) and data.get("transcript_signature") == signature(read_json(path)):
            return data["empty_ranges_ms"]
    progress = path.with_name("asr_progress.json")
    if path.name != "transcript.json" or not progress.is_file():
        return []
    data = read_json(progress)
    if not isinstance(data, dict) or not isinstance(data.get("segments"), dict):
        return []
    size = data.get("segment_seconds")
    if type(size) is int and size > 0:
        total_frames = wav_frames(path.with_name("audio.wav"))
        return [{"start": int(i) * size * 1000,
                 "end": min((int(i) + 1) * size * 1000, total_frames * 1000 // RATE)
                        if total_frames else (int(i) + 1) * size * 1000}
                for i, items in data["segments"].items() if i.isascii() and i.isdigit() and items == []
                and (not total_frames or int(i) * size * RATE < total_frames)]
    if data.get("signature"):
        return [{"start": item["start"], "end": item["end"]}
                for item in data["segments"].values() if not item["rows"]]
    return []


def srt_time(ms):
    return f"{time_text(ms)},{ms % 1000:03d}"


def export_transcript(out, rows, empty, coarse_srt):
    write_json(out / ".transcript-cache", {"rows": rows, "empty": empty})
    valid = [row for row in rows if row["text"].strip()]
    write_text(out / "transcript.txt", "\n".join(
        f"[{time_text(row['start'])}] {row['text']}" for row in valid) + ("\n" if valid else ""))
    precise = [row for row in valid if row["end"] > row["start"] and row.get("timing") != "segment"]
    rough = [row for row in valid if row["end"] > row["start"] and row.get("timing") == "segment"]
    start_only = len(valid) - len(precise) - len(rough)
    coverage = ["# 转写覆盖情况", "", f"有文字的条目：{len(valid)}",
                f"逐句时间：{len(precise)}，分片时间：{len(rough)}，仅有起点：{start_only}。", ""]
    if empty:
        coverage.extend(f"- {time_range(row)} · 语音接口未返回文字，待回听。" for row in empty)
    else:
        coverage.append("无已记录的空白分片。")
    write_text(out / "coverage.md", "\n".join(coverage) + "\n")
    log("成功", f"转写导出 · {len(valid)} 条文本、{len(empty)} 段无文字。")
    srt_path = out / "transcript.srt"
    rough_path = out / "transcript.segments.srt"
    for path, source in ((srt_path, precise if len(precise) == len(valid) else []),
                         (rough_path, rough if coarse_srt and len(rough) == len(valid) else [])):
        if source:
            write_text(path, "\n\n".join(
                f"{i}\n{srt_time(row['start'])} --> {srt_time(row['end'])}\n"
                + " ".join(row["text"].split()) for i, row in enumerate(source, 1)) + "\n")
            log("成功", f"字幕导出 · {path.name}。")
        elif path.exists():
            path.unlink()
    if valid and len(precise) != len(valid):
        log("提示", "转写没有完整逐句时间。逐句 SRT 需要支持句级时间的转写后端。")
    return valid


def write_notes(out, course, rows, empty, config, args):
    notes = out / "notes.md"
    cache_path = out / ".notes-signature"
    payload = notes_payload(course, rows, empty, config["notes_prompt"],
                            config["glossary"], args.source_offset * 1000)
    key = signature([config["base_url"], config["llm_model"], payload])
    if not args.force and cache_path.is_file() and notes.is_file():
        previous = notes.read_text(encoding="utf-8")
        if cache_path.read_text(encoding="ascii").strip() == signature([key, previous]):
            log("复用", "笔记 · notes.md。")
            return
    log("开始", f"整节字幕生成笔记 · {config['llm_model']}。")
    content = generate_notes(new_client(config), config["llm_model"], payload, args.debug)
    write_text(notes, content)
    write_text(cache_path, signature([key, content]) + "\n")
    log("成功", "笔记整理 · notes.md。")


def add_stage_args(parser):
    add_config_args(parser)
    parser.add_argument("--skip-audio", action="store_true", help="使用输出目录中已有的 audio.wav")
    parser.add_argument("--skip-asr", action="store_true", help="复用已有转写生成笔记")
    parser.add_argument("--skip-llm", action="store_true", help="跳过笔记，导出转写")
    parser.add_argument("--retry-empty", action="store_true", help="重试缓存中无文字的分片")
    parser.add_argument("--force", action="store_true", help="重新生成笔记，保留有效的转写缓存")
    parser.add_argument("--coarse-srt", action="store_true", help="另导出带分片级时间的 SRT")
    parser.add_argument("--source-offset", type=int, default=0, help="笔记引用换算为原片时间，单位为秒")


def process(args, config, out):
    media = args.media
    validate_output(out, config["input_dir"])
    if media and not media.is_file() and not args.skip_asr:
        raise PipelineError(f"找不到输入媒体：{media}")
    if args.source_offset < 0:
        raise PipelineError("--source-offset 不能为负数。")
    if args.skip_asr and args.retry_empty:
        raise PipelineError("--retry-empty 需要启用语音转写。")
    with output_lock(out):
        if args.skip_asr and args.skip_llm and not args.transcript:
            audio_for(media, out, args.skip_audio, args.debug)
            log("完成", str(out))
            return
        if args.skip_asr and not args.transcript and (out / ".transcript-cache").is_file():
            saved = read_json(out / ".transcript-cache")
            rows, empty = checked_rows(saved["rows"]), saved["empty"]
            log("复用", "已有转写缓存。")
        elif args.skip_asr:
            transcript = args.transcript or out / "transcript.json"
            rows, empty = checked_rows(read_json(transcript)), legacy_empty(transcript)
            log("复用", f"已有转写 · {transcript}。")
        else:
            audio = audio_for(media, out, args.skip_audio, args.debug)
            rows, empty = transcribe(audio, out, config, args)
        valid = export_transcript(out, rows, empty, args.coarse_srt)
        if not args.skip_llm:
            if not valid:
                raise PipelineError("整段没有识别到文字，已导出覆盖情况，未生成笔记。")
            course = media.stem if media else args.transcript.parent.name.removeprefix("notes_")
            write_notes(out, course, valid, empty, config, args)
    log("完成", str(out))


def main():
    parser = argparse.ArgumentParser(description="中文课程录音 → 转写、字幕与附原话的学习笔记")
    parser.add_argument("media", nargs="?", type=Path, help="本地视频或音频")
    parser.add_argument("--out-dir", type=Path, help="输出目录，默认配置中的 output_dir/notes_<文件名>")
    parser.add_argument("--transcript", type=Path, help="使用指定的旧转写 JSON，跳过语音 API")
    add_stage_args(parser)
    args = parser.parse_args()
    if not args.media and not args.transcript:
        parser.error("请提供音视频路径或 --transcript")
    if args.transcript:
        args.skip_asr = True
    config = load_config(args)
    if args.media:
        args.media = resolve_input(args.media, config)
    source = args.media or args.transcript
    name = args.media.stem if args.media else args.transcript.parent.name.removeprefix("notes_")
    out = args.out_dir or config["output_dir"] / ("notes_" + (name or source.stem))
    process(args, config, out)


if __name__ == "__main__":
    sys.exit(run_cli(main))

