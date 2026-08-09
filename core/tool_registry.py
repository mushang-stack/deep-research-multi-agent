"""工具注册 + 分发(spec §4 ToolRegistry)。
外部工具(web_search/web_read)与内部工具(M2 的 dispatch_research/verify_findings/write_report,
派发子 agent)统一注册;schemas() 导出 OpenAI 兼容 tools 参数。"""


class ToolNotFoundError(KeyError):
    """调用了未注册的工具。"""


class ToolRegistry:
    def __init__(self):
        self._tools = {}  # name -> {"fn": callable, "schema": dict}

    def register(self, name, fn, *, description, parameters):
        """注册一个工具。parameters 是 JSON Schema(描述参数给模型看)。"""
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }
        self._tools[name] = {"fn": fn, "schema": schema}

    def schemas(self) -> list[dict]:
        """导出 OpenAI 兼容的 tools 参数,传给 client.chat(tools=...)。"""
        return [t["schema"] for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def execute(self, name: str, arguments: dict):
        """按 name 找到工具,以 arguments 解包为 kwargs 调用。未注册抛 ToolNotFoundError。"""
        if name not in self._tools:
            raise ToolNotFoundError(name)
        return self._tools[name]["fn"](**arguments)
