# 回测口径测试

这三个脚本只依赖 Python 标准库，可在不安装 FastAPI/SQLAlchemy 的环境直接运行：

```sh
python3 -I tests/test_domain_point_in_time.py
python3 -I tests/test_pit_training.py
python3 -I tests/test_backfill_edges.py
```

覆盖的口径：

- `test_domain_point_in_time.py`：无补录时复现既有 HB-041 结果（SBA 5.32、TSB 4.69、补货点 8、缺口 6）；结账日后入账的补录不改变任何窗口与选模；结账日前入账的补录抬高对应窗口实际需求与误差；入账日恰好等于预测当日时按“未入账”处理；补录校验与 JSON 可序列化。
- `test_pit_training.py`：同一补录对预测当日之前的起点不可见、对之后的起点进入训练集，验证训练信息集截止；全史预测随补录上修。
- `test_backfill_edges.py`：逐窗口 MAE 与汇总 MAE 一致、结账周当周补录留待下一次分析、同批次重复拦截、跨周补录在覆盖窗口内累计。

端到端的“导入周台账 → 导入补录批次 → 提交并钉住批次 → worker 计算 → 逐窗口结果”流程依赖 FastAPI/SQLAlchemy/Redis，在完整 Compose 环境内通过界面或 `/docs` 验证。
