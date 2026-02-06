# 情绪词典数据（NRC-VAD）

本目录存放情绪模块使用的 VAD（Valence–Arousal–Dominance）词典及内部格式。

## 文件说明

| 文件 | 说明 |
|------|------|
| `Chinese-Simplified-NRC-VAD-Lexicon.txt` | 源词表：Tab 分隔，列依次为 English Word, Valence, Arousal, Dominance, Chinese-Simplified Word；V/A/D 原值域 [0,1]。 |
| `nrc_vad_internal.json` | 运行时加载的内部格式，由脚本从上述 txt 生成。**key 为中文词**，便于中文分词匹配。 |

## 内部 JSON 格式

- 结构：`{ "词": { "v": float, "a": float, "d": float } }`
- 键：中文词（同一中文对应多条英文时，V/A/D 取平均）
- 值域：**[-1, 1]**（由源 [0,1] 经 `v_new = 2 * v_old - 1` 转换）

供 `src.mood.vad_lexicon` 加载；缺失时情绪估计返回 (0,0,0,0)。

## 重新生成内部 JSON

从项目根目录执行：

```bash
python scripts/convert_nrc_vad_to_internal.py
```

可选参数：`--input` / `-i` 指定源 txt，`--output` / `-o` 指定输出 json。详见脚本内 docstring。
