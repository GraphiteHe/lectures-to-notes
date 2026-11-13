#!/usr/bin/env python3
"""Check access to the configured text model."""
import argparse
import sys

from pipeline_config import add_config_args, load_config, log, new_client, retry_call, run_cli


def main():
    parser = argparse.ArgumentParser(description="测试配置中的百炼文本模型")
    add_config_args(parser)
    args = parser.parse_args()
    config = load_config(args)
    client = new_client(config)
    log("开始", f"检查文本模型 {config['llm_model']}。")
    retry_call(lambda: client.chat.completions.create(
        model=config["llm_model"], max_tokens=16,
        messages=[{"role": "user", "content": "请只回复 OK"}],
    ), "文本接口", args.debug)
    log("成功", "文本接口已返回。")


if __name__ == "__main__":
    sys.exit(run_cli(main))

