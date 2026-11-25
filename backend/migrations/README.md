初始结构由 `src/models/tables.py` 定义，在 API 启动前由 `metadata.create_all` 创建。演示数据通过独立入口填充。

迟到工单补录引入两处结构：

- `backfill_batches`：补录批次，字段与 `data_revisions` 对齐，按规范化内容的 SHA-256 去重，保存备件编号、需求周、入账日和新增件数。
- `analysis_runs.backfill_id`：可空外键，提交分析时钉住所选补录批次；旧任务为 NULL，表示仅用周台账（各周按周一入账）。

`metadata.create_all` 只建新表、不会给已存在的表补列。对已部署的旧库执行以下变更（MySQL 8.0）：

```sql
CREATE TABLE IF NOT EXISTS backfill_batches (
  id INT NOT NULL AUTO_INCREMENT,
  name VARCHAR(180) NOT NULL,
  checksum VARCHAR(64) NOT NULL UNIQUE,
  records JSON NOT NULL,
  owner VARCHAR(40) NOT NULL,
  created DOUBLE NOT NULL,
  PRIMARY KEY (id)
);
ALTER TABLE analysis_runs ADD COLUMN backfill_id INT NULL INDEX;
```
