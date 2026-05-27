"""Markitdown 文档转换器。"""

from __future__ import annotations

from pathlib import Path

class DocumentConverter:
    """将多格式文件转换为 Markdown 文本。"""

    def __init__(self) -> None:
        self._converter = self._build_converter()
    def convert_to_markdown(self, file_path: str) -> str:
        """
        【工具功能】将本地文件转换为 Markdown 文本
        支持：PDF（pymupdf4llm）、Word/Excel/PPT/HTML 等 markitdown 支持的所有格式
        参数：file_path: 文件绝对路径
        返回：str，Markdown 文本内容
        异常：Exception: 转换失败或结果为空
        """
        try:
            suffix = Path(file_path).suffix.lower()
            if suffix == ".pdf":
                return self._convert_pdf(file_path)
            result = self._converter.convert(file_path) 
            markdown = self._extract_markdown(result)
            if not markdown.strip():
                raise Exception("转换结果为空。")
            return markdown
        except Exception as exc:
            file_name = Path(file_path).name
            raise Exception(f"{file_name} 转换失败。") from exc

    #   PDF 专用转换（pymupdf4llm，排版还原更好）
    def _convert_pdf(self, file_path: str) -> str:
        try:
            import pymupdf4llm
            markdown = pymupdf4llm.to_markdown(file_path)
            if not markdown.strip():
                raise Exception("转换结果为空。")
            return markdown
        except ImportError as exc:
            raise Exception("pymupdf4llm 未安装，请执行 uv add pymupdf4llm。") from exc

    #   提取 Markdown
    def _extract_markdown(self, result: object) -> str:
        for attribute in ("text_content", "markdown", "content"):
            value = getattr(result, attribute, "")  # 获取属性值
            if isinstance(value, str) and value.strip():  # 检查值是否为非空字符串
                return value
        return str(result) if result is not None else ""  

    #   构建转换器
    def _build_converter(self) -> object:
        try:
            from markitdown import MarkItDown

            return MarkItDown()
        except Exception as exc:
            raise Exception("markitdown 初始化失败。") from exc


_document_converter: DocumentConverter | None = None


def get_document_converter() -> DocumentConverter:
    """
    【工具功能】获取 DocumentConverter 全局单例
    支持：全应用共享同一转换器实例，避免重复初始化 markitdown
    参数：无
    返回：DocumentConverter 实例
    """
    global _document_converter
    if _document_converter is None:
        _document_converter = DocumentConverter()
    return _document_converter
