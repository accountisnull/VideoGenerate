"""vector-mcp stdio 薄适配，只暴露本机可信内部工具。"""

from mcp.server.fastmcp import FastMCP

from app.content.adapters import ToolAdapter
from app.content.config import SimilarityConfig
from app.content.factory import open_service


def create_server(adapter: ToolAdapter) -> FastMCP:
    server = FastMCP("vector-mcp")

    @server.tool(name="embed")
    async def embed(script: str) -> dict:
        """离线向量化，返回向量及完整模型/索引 profile。"""
        return await adapter.acall("embed", {"script": script})

    @server.tool(name="vector-search")
    async def vector_search(vector: list[float], content_version_id: str, profile: dict) -> dict:
        """校验向量来源，排除当前版本并查询 top 10。"""
        return await adapter.acall("search", {"vector": vector, "content_version_id": content_version_id,
                                       "profile": profile})

    @server.tool(name="vector-insert")
    async def vector_insert(request: dict) -> dict:
        """审核通过的最终稿幂等保存；内部生成向量，禁止绕过审核。"""
        return await adapter.acall("store", request)

    @server.tool(name="detect")
    async def detect(request: dict) -> dict:
        """返回通过判定、最高分及可供修订的命中稿件。"""
        return await adapter.acall("detect", request)

    @server.tool(name="reconcile")
    async def reconcile(request: dict) -> dict:
        """重启后核对稳定版本及正文，未知写入结果不得直接视为成功。"""
        return await adapter.acall("reconcile", request)

    return server


def main() -> None:
    with open_service(SimilarityConfig.load()) as service:
        service.index.validate()
        create_server(ToolAdapter(service)).run(transport="stdio")


if __name__ == "__main__":
    main()
