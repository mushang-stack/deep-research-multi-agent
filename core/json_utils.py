"""JSON 清洗工具(纯函数)。供 agents.dispatch 与 eval.* 共用。

模型/裁判输出严格 JSON,但常套 markdown 围栏或在 JSON 前后输出思考散文。
这里两步清洗:
- clean_json: 去 ```json ... ``` 围栏与首尾空白。
- json_balanced_substring: 从散文里抠第一个平衡的 {...} 子串。
"""
import re


def clean_json(text: str) -> str:
    """去除 markdown 代码块包裹(```json ... ```)与首尾空白。"""
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()


def json_balanced_substring(text: str) -> str:
    """从文本中抠出第一个平衡的 JSON 对象子串。

    模型(尤其 DeepSeek)常在 JSON 前后输出思考散文("我已经收集了…让我整理…{json}")。
    clean_json 只去围栏,去不掉这种散文 → json.loads 整段必失败。这里用大括号深度
    扫描(识别字符串字面量与转义),把第一个平衡的 {...} 抠出来。找不到则原样返回,
    交由 safe_parse_arguments 兜底成 {}。纯 JSON / 围栏 JSON 不受影响(首字符即 {)。
    """
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text  # 不平衡:原样返回,让上层兜底
