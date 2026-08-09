"""GLM 裁判客户端(OpenAI 兼容)。薄子类,默认 GLM-5.2。仅评估环节用,规避自评偏差。"""
from .openai_compat import OpenAICompatClient
from core.config import env


class GLMClient(OpenAICompatClient):
    def __init__(self, *, api_key=None, base_url="https://open.bigmodel.cn/api/paas/v4",
                 model="glm-5.2", temperature=0.0, max_tokens=2048, **kwargs):
        super().__init__(
            api_key=api_key or env("GLM_API_KEY"),
            base_url=base_url, model=model,
            temperature=temperature, max_tokens=max_tokens, **kwargs,
        )
