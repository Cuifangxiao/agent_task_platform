"""应用生命周期层: 只管启动顺序与关停, 不写业务。

- application.py        信号处理 + run/shutdown 编排
- bootstrap.py          资源初始化(不碰网络)
- transport_runtime.py  协议层启停
"""
