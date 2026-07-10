"""RAG 层。

规划中的组成部分:
- loader: 文档加载(pdf / markdown / url ...)
- splitter: 文本切分
- embedding: 向量化
- vector_store: 向量库读写(如 milvus / pgvector / chroma)
- retriever: 对外的检索入口,见 retriever.py
"""
