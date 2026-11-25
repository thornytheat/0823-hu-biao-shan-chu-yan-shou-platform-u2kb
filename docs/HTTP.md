# 阶段接口

`POST /api/stages/<stage>` 接收 `multipart/form-data`。字段 `sample=dirty`、`sample=clean` 或 `file=<CSV>` 三选一。阶段为 `intake`、`prepare`、`calculate`、`policy`、`balance`。预览不入库。

`POST /api/runs` 接收同样的输入，执行全批并返回运行报告。`GET /api/runs` 返回最近 50 批。`GET /api/runs/<id>` 查询一批和当前复核状态。

`PATCH /api/runs/<id>` 接收 `revision`、`status` 和 `note`。状态使用 `APPROVED` 或 `HELD`。冲突返回 409，客户端读取新版本后重试。

`GET /api/runs/<id>/evidence` 下载内含运行清单、行号隔离、业务证据和复核信息的 JSON。

旧文件回收使用 JSON 提交 `files`、`moves`/`relocations`、`deletions`。`POST /api/reclaim/evaluate` 只判定不入库；`POST /api/reclaims` 保存带换位记录摘要的结论，`PATCH /api/reclaims/<id>` 或 `POST /api/reclaims/<id>/confirm` 提交回收确认。上游换位记录变化后，旧摘要返回 409 `STALE_RELOCATION`。也可用 `PUT /api/relocations` 更新服务端保存的当前换位记录。
