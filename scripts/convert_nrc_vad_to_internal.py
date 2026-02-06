"""
将 NRC-VAD 中英对照词表转换为情绪模块使用的内部 JSON 格式。

从 data/emotion/Chinese-Simplified-NRC-VAD-Lexicon.txt 读取，
输出 data/emotion/nrc_vad_internal.json：
- key 为中文词（便于中文分词匹配）
- 同一中文词对应多条英文时，对 V/A/D 取平均
- 值域由 [0,1] 转为 [-1,1]：v_new = 2 * v_old - 1

用法:
    python scripts/convert_nrc_vad_to_internal.py
    python scripts/convert_nrc_vad_to_internal.py --input path/to/lexicon.txt --output path/to/out.json
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from src.common.logger import get_logger
    logger = get_logger("vad_convert")
except Exception:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger = logging.getLogger("vad_convert")

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_INPUT = os.path.join(ROOT_PATH, "data", "emotion", "Chinese-Simplified-NRC-VAD-Lexicon.txt")
DEFAULT_OUTPUT = os.path.join(ROOT_PATH, "data", "emotion", "nrc_vad_internal.json")


def _to_norm(val: float) -> float:
    """将 [0, 1] 转为 [-1, 1]。"""
    return round(2.0 * float(val) - 1.0, 4)


def convert_lexicon(input_path: str, output_path: str) -> int:
    """
    读取 TSV 词表，按中文词聚合 V/A/D（取平均），写出内部 JSON。

    Args:
        input_path: 输入文件路径（Tab 分隔：English, Valence, Arousal, Dominance, Chinese）
        output_path: 输出 JSON 路径

    Returns:
        写入的词条数量
    """
    if not os.path.isfile(input_path):
        logger.error("输入文件不存在: %s", input_path)
        sys.exit(1)

    # 中文词 -> 累加 (v_sum, a_sum, d_sum, count)
    agg: dict[str, tuple[float, float, float, int]] = defaultdict(lambda: (0.0, 0.0, 0.0, 0))

    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    header = lines[0].strip().lower()
    if "valence" not in header or "chinese" not in header:
        logger.warning("首行可能不是表头，将尝试按列解析: %s", lines[0][:80])

    for i, line in enumerate(lines[1:], start=2):
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            logger.debug("跳过列数不足的行 %d: %s", i, line[:60])
            continue
        try:
            _en, v, a, d, cn = parts[0], float(parts[1]), float(parts[2]), float(parts[3]), parts[4].strip()
        except (ValueError, IndexError) as e:
            logger.debug("跳过解析失败的行 %d: %s", i, e)
            continue
        if not cn:
            continue
        v_sum, a_sum, d_sum, cnt = agg[cn]
        agg[cn] = (v_sum + v, a_sum + a, d_sum + d, cnt + 1)

    result = {}
    for word, (v_sum, a_sum, d_sum, cnt) in agg.items():
        if cnt <= 0:
            continue
        result[word] = {
            "v": _to_norm(v_sum / cnt),
            "a": _to_norm(a_sum / cnt),
            "d": _to_norm(d_sum / cnt),
        }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("已写入 %d 个词条到 %s", len(result), output_path)
    return len(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="NRC-VAD 词表转内部 JSON（中文 key，V/A/D 在 [-1,1]）")
    parser.add_argument(
        "--input",
        "-i",
        default=DEFAULT_INPUT,
        help="输入 TSV 路径（默认: data/emotion/Chinese-Simplified-NRC-VAD-Lexicon.txt）",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT,
        help="输出 JSON 路径（默认: data/emotion/nrc_vad_internal.json）",
    )
    args = parser.parse_args()
    count = convert_lexicon(args.input, args.output)
    if count == 0:
        logger.warning("未生成任何词条，请检查输入文件格式")
        sys.exit(1)


if __name__ == "__main__":
    main()
