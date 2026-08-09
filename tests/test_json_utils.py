"""core/json_utils 的直接单测(clean_json + json_balanced_substring)。

json_balanced_substring 是 M2 冒烟踩坑后加的「从散文里抠平衡 JSON」核心函数,
是整条解析链鲁棒性的关键,必须有直接单测(此前仅经 dispatch/judge 间接覆盖)。
"""
from core.json_utils import clean_json, json_balanced_substring


# ---------- clean_json ----------

def test_clean_json_strips_markdown_fence():
    assert clean_json('```json\n{"a":1}\n```') == '{"a":1}'
    assert clean_json('{"a":1}') == '{"a":1}'
    assert clean_json("") == ""
    assert clean_json("  ```\n{}\n```  ") == "{}"


def test_clean_json_strips_bare_fence():
    assert clean_json("```\n{\"a\":1}\n```") == '{"a":1}'


# ---------- json_balanced_substring ----------

def test_json_balanced_substring_plain_object():
    assert json_balanced_substring('{"a":1}') == '{"a":1}'


def test_json_balanced_substring_extracts_from_prose():
    # M2 冒烟的真实形态:JSON 前有思考散文
    text = '思考中…让我整理。\n\n{"findings":[{"id":"f1"}]}'
    assert json_balanced_substring(text) == '{"findings":[{"id":"f1"}]}'


def test_json_balanced_substring_handles_nested_objects():
    text = '前缀 {"a":{"b":2},"c":3} 尾巴'
    assert json_balanced_substring(text) == '{"a":{"b":2},"c":3}'


def test_json_balanced_substring_ignores_braces_inside_strings():
    # 字符串字面量里的 { } 不应计入大括号深度
    text = '{"note":"a { b } c"}'
    assert json_balanced_substring(text) == '{"note":"a { b } c"}'


def test_json_balanced_substring_handles_escaped_quotes_in_strings():
    # 转义引号 \" 不应被误判为字符串结束 → 后续 {x} 仍在串内,不计深度
    text = '前缀 {"k":"val\\"{x}"} 后缀'
    assert json_balanced_substring(text) == '{"k":"val\\"{x}"}'


def test_json_balanced_substring_unbalanced_returns_original():
    # 无大括号 → 原样返回(交由 safe_parse_arguments 兜底成 {})
    assert json_balanced_substring("no braces here") == "no braces here"
    # 不平衡 → 原样返回
    assert json_balanced_substring('{"a":') == '{"a":'


def test_json_balanced_substring_no_brace_returns_text():
    assert json_balanced_substring("纯散文,没有大括号") == "纯散文,没有大括号"
