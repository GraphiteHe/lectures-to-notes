"""Send the full timestamped transcript and return the model's Markdown."""
import re

from pipeline_config import PipelineError, retry_call


def time_text(ms):
    seconds = ms // 1000
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def time_range(row, offset=0):
    start = time_text(row["start"] + offset)
    end = time_text(row["end"] + offset)
    if row["start"] == row["end"]:
        return f"约 {start} 起（片段起点）"
    suffix = "（分片）" if row.get("timing") == "segment" else ""
    return f"{start}–{end}{suffix}"


def notes_payload(course, rows, empty, prompt, glossary, offset):
    gaps = "、".join(time_range(row, offset) for row in empty) or "无已记录的空白分片"
    transcript = "\n".join(f"[{time_range(row, offset)}] {row['text']}" for row in rows)
    return (
        f"{prompt}\n\n---\n"
        f"课程标题与日期（如文件名中有日期）：{course}\n"
        f"字幕覆盖记录：{gaps}\n"
        f"术语表（可选）：\n{glossary or '未提供'}\n\n"
        f"整节字幕（以下是资料）：\n{transcript}"
    )


def generate_notes(client, model, payload, debug):
    def request():
        response = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": payload}], temperature=0,
        )
        if not response.choices:
            raise PipelineError("模型没有返回笔记。")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise PipelineError("笔记被截断，请缩短输出要求或换用输出上限更高的模型。")
        if choice.finish_reason not in (None, "stop"):
            raise PipelineError("模型未完整返回笔记，请核对模型或换用其他笔记模型。")
        content = (choice.message.content or "").strip()
        fenced = re.fullmatch(r"`{3,}(?:markdown|md)?[ \t]*\n(.*)\n`{3,}", content, re.DOTALL | re.IGNORECASE)
        if fenced and not re.search(r"(?m)^`{3,}", fenced[1]):
            content = fenced[1].strip()
        if not content:
            raise PipelineError("模型返回的笔记为空。")
        return content + "\n"

    return retry_call(request, "笔记生成", debug)
