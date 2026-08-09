"""DeepSeek 生成客户端(OpenAI 兼容)。薄子类,只设默认值。"""
from .openai_compat import OpenAICompatClient
from core.config import env


class DeepSeekClient(OpenAICompatClient):
    def __init__(self, *, api_key=None, base_url="https://api.deepseek.com",
                 model="deepseek-chat", temperature=0.3, max_tokens=4096, **kwargs):
        # api_key 默认从环境变量取;也允许显式传入(测试/覆盖)
        super().__init__(
            api_key=api_key or env("DEEPSEEK_API_KEY"),
            base_url=base_url, model=model,
            temperature=temperature, max_tokens=max_tokens, **kwargs,
        )
