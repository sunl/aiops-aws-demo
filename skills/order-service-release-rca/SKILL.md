---
name: order-service-release-rca
description: 排查 order-service（订单服务）报错、失败率升高或延迟异常，尤其是发布或配置变更之后出现的问题。
---

# order-service 发布后故障排查

## 适用场景
order-service 的调用失败数、错误日志或延迟出现异常，尤其是异常出现前 2 小时内有发布或配置变更。

## 系统背景
- order-service 是 Lambda 函数 `aiops-demo-order-service`，订单数据存放在 DynamoDB。
- 表名由环境变量 `ORDERS_TABLE` 决定，稳定版本使用 `aiops-demo-orders`。
- 应用日志在 Elasticsearch（索引 `logs-order-service-*`），通过 MCP 工具 `es_error_summary`、`es_search_logs` 查询。
- 制品与发布说明在 Nexus 制品库，通过 MCP 工具 `nexus_list_versions`、`nexus_get_release` 查询。

## 排查步骤
1. 用 `es_error_summary` 确认错误开始时间、错误类型、受影响的版本和表；需要原始日志时用 `es_search_logs`（`level=ERROR`），关注 `error_type`、`table`、`version` 字段。
2. 核对异常开始前后对该函数的 `UpdateFunctionConfiguration` / `UpdateFunctionCode` 事件，列出变更前后的差异。CloudTrail 查询交给一个后台子任务执行（例如命名为 `cloudtrail-lambda-changes`，可与第 1、3 步并行）；主流程不要直接调用 `cloudtrail lookup_events`，主流程中它会被当作不允许的服务操作取消。子任务也查不到时不要重试，改用 Lambda 函数配置的 `LastModified` 和 Nexus 的部署记录作为变更时间依据，并在结论中注明这一缺口。
3. 用 `nexus_list_versions` 确认当前部署的版本和部署时间，用 `nexus_get_release` 读取该版本的发布说明、配置变更、发布前置步骤和已知问题。
4. 核对配置引用的资源是否真实存在。DynamoDB 表用只读的 `list_tables` 核对；不要调用 `describe_table`：自主调查中它会被当作需要审批的操作取消。
5. 判断根因是否为“发布前置步骤未执行”或“配置与环境不一致”。

## 输出要求
- 使用中文。
- 按以下结构给出结论：**根因**、**证据链**（带时间点）、**影响范围**、**处置建议**（优先给出回滚到上一个稳定版本的具体做法）、**预防措施**。
- 处置建议只给出步骤，不要自行执行任何变更。
