"""GLM 逐条裁判(spec §4.2)。封装 GLMClient,小输出(一个 verdict),坏 JSON 清洗兜底。

两类裁判:
- judge_finding: 判 excerpt 是否支撑 claim(support)+ 来源是否真实(source_real)
- judge_key_fact: 判报告全文是否覆盖某关键事实(covered)

输出不可解析时重试(parse_retries 次);最终失败按 spec §6 保守计为
unsupported / not covered 并打 parse_failed=True。瞬时网络错误的重试由
GLMClient(OpenAICompatClient)内部 retry_with_backoff 负责,本模块不重复。
"""
from core.json_utils import clean_json, json_balanced_substring
from core.robust import safe_parse_arguments
from llm.base import LLMResponse

_FINDING_SYSTEM = """你是严格的来源核查裁判。给定一条研究发现的论断(claim)、其引用的原文摘录(excerpt)、来源链接(source_url),判定两件事:
1. support: excerpt 是否支撑 claim?取值 "supported"(明确支撑)/ "partial"(部分支撑或间接)/ "unsupported"(不支撑或无关)。
2. source_real: source_url 是否像真实、相关、可达的来源(非编造 / 非死链 / 非无关)?布尔。

输出格式(极其重要):直接以 { 开头,只输出一行 JSON。绝对不要任何思考过程、解释、markdown 或代码块。reason 必须是简短纯文本(≤30字,不含双引号、换行、反斜杠),否则会破坏 JSON。

{"support": "supported" | "partial" | "unsupported", "source_real": true | false, "reason": "一句依据"}"""

_KEYFACT_SYSTEM = """你是严格的覆盖度裁判。给定一个关键事实(key_fact)与一份研究报告全文(report),判定报告是否覆盖该关键事实(明确陈述或可直接推出)。
仅输出一行 JSON,不要任何解释或 markdown:
{"covered": true | false, "reason": "一句依据"}"""

_VALID_SUPPORT = {"supported", "partial", "unsupported"}


def _ask(client, system: str, user: str, parser, parse_retries: int = 2):
    """调一次裁判并解析;输出不可解析时重试,最多 parse_retries+1 次。

    返回 parser 的结果(dict);全部失败返回 None(由调用方兜底成保守 verdict)。
    """
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    for _ in range(parse_retries + 1):
        resp = client.chat(messages=messages, temperature=0.0, max_tokens=512)
        content = (resp.content or "") if isinstance(resp, LLMResponse) else str(resp)
        try:
            parsed = parser(content)
        except Exception:
            parsed = None  # parser 异常视同不可解析 → 继续重试,不击穿整条评估
        if parsed is not None:
            return parsed
    return None


def _parse_obj(content: str) -> dict | None:
    data = safe_parse_arguments(json_balanced_substring(clean_json(content)))
    return data if isinstance(data, dict) else None


def judge_finding(*, claim: str, excerpt: str, source_url: str,
                  client, parse_retries: int = 2) -> dict:
    user = f"claim: {claim}\nexcerpt: {excerpt}\nsource_url: {source_url}"

    def _parse(content):
        d = _parse_obj(content)
        if not d or d.get("support") not in _VALID_SUPPORT \
                or not isinstance(d.get("source_real"), bool):
            return None
        return {"support": d["support"], "source_real": d["source_real"],
                "reason": str(d.get("reason", ""))}

    v = _ask(client, _FINDING_SYSTEM, user, _parse, parse_retries)
    if v is None:
        # parse_failed = 裁判输出不可解析(技术失败),非 finding 真无支撑 → 取中性 partial
        # (source_real 仍兜底 false,幻觉率照计,防幻觉不弱化)
        return {"support": "partial", "source_real": False,
                "reason": "judge output unparseable", "parse_failed": True}
    v["parse_failed"] = False
    return v


def judge_key_fact(*, key_fact: str, report_text: str,
                   client, parse_retries: int = 2) -> dict:
    user = f"key_fact: {key_fact}\n\nreport:\n{report_text}"

    def _parse(content):
        d = _parse_obj(content)
        if not d or not isinstance(d.get("covered"), bool):
            return None
        return {"covered": d["covered"], "reason": str(d.get("reason", ""))}

    v = _ask(client, _KEYFACT_SYSTEM, user, _parse, parse_retries)
    if v is None:
        return {"covered": False, "reason": "judge output unparseable",
                "parse_failed": True}
    v["parse_failed"] = False
    return v
