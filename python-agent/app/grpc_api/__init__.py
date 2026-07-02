"""gRPC 接入层。

只负责:接收 proto 消息 -> 转成内部参数 -> 调用 services 层 -> 组装 proto 响应。
不写业务逻辑,业务逻辑放在 app/services。
"""
