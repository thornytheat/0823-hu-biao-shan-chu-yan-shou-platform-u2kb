# 字段与业务定义

输入编码 UTF-8，允许 BOM。首行必须匹配样例列顺序。记录键和业务组保留原始字符串。`kind` 允许 data, position_delete, equality_delete, snapshot。说明列 `note` 可以跨行，限制 200 字。

业务字段按 `file`、`partition`、`sequence`、`position`、`row_key` 排列。最终值单位见阶段结果。数字接受非负整数、全角数字和完整千分位。空串、短横线、N/A、NULL 与未知为缺值。无关字段用 0 占位。

仅使用序列不大于快照的文件。等值删除匹配分区与业务键，且序列必须大于数据；位置删除精确匹配文件与行偏移，同序列可删除。指向已知文件不存在位置的删除进入待复核。

回收接口中的位置可写为 `file:position`，或使用 `old_file`/`old_position`、`new_file`/`new_position` 对象。换位记录支持连续链路；`deletions` 保存按旧位置标记的剔除记录，`version`/`revision` 或换位记录摘要用于拒绝过期结论。

`incoming.csv` 包含四个业务组，第二组提供不同边界，第三组触发业务拒收，第四组需要核对。另含重复表头、冲突记录、重复回放与错误列数。`ready.csv` 含可以签发的两个组。
