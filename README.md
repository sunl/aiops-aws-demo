# aiops-aws-demo

AWS DevOps Agent × 飞书应用机器人的 AIOps 演示项目：用一个“发布后故障”场景，把“告警 → 自动根因分析 → 飞书推送与问答 → 人工回滚 → 恢复”的闭环在 us-east-1 端到端跑通。

> 订单服务发布 1.4.0 → 调用大量失败 → CloudWatch 告警 → 飞书群收到告警卡片 → DevOps Agent 自动调查（读 CloudWatch、CloudTrail，并通过两个 MCP 查询“Elasticsearch 日志”和“Nexus 制品库”）→ 飞书群收到根因卡片 → 运维在飞书里 @机器人 追问 → 回滚 → 告警恢复卡片

也可以在群里 @机器人 发送“调查 + 问题描述”主动发起调查，根因卡片回复到该消息的话题，示例见《03-部署手册》10.2。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/01-方案说明.md](docs/01-方案说明.md) | 方案选型、目标架构、Demo 验证结果、先决条件、PoC 计划、费用 |
| [docs/02-设计文档.md](docs/02-设计文档.md) | 组件、交互流程、详细设计、安全、可靠性与可观测性 |
| [docs/03-部署手册.md](docs/03-部署手册.md) | 分步部署、飞书应用配置、验证、演练、清理与故障排查 |

## 组件与对应关系

| Demo 组件 | 对应现有组件 |
|---|---|
| CloudWatch 告警 + 告警转换 Lambda | 现有告警平台 |
| 飞书应用机器人：告警 / 根因卡片 + 问答（ECS Fargate 长连接） | 飞书 Bot |
| AWS DevOps Agent | 自建 SRE Agent + 运维编排 / 根因分析平台 |
| `es-mock` MCP（Amazon Bedrock AgentCore Runtime，OAuth） | Elasticsearch MCP Server |
| `nexus-mock` MCP（Lambda 函数 URL，SigV4） | Nexus MCP Server |

架构图见 `docs/diagrams/`（SVG 为源文件，PNG 由 `docs/diagrams/export_png.sh` 导出）。

## 目录结构

```
aiops-aws-demo/
│
├── README.md                       项目说明与快速开始（本文件）
├── template.yaml                   CloudFormation 模板：栈 aiops-demo 的全部资源
├── deploy.sh                       打包代码与 linux/arm64 依赖，部署栈
├── .gitignore                      排除虚拟环境、构建产物与本地日志
│
├── src/                            Lambda、Fargate 客户端与 MCP Server 代码
│   ├── common/demo_common/         Lambda 公共层：飞书、A2A、配置
│   ├── alert_forwarder/            告警 → DevOps Agent Webhook，告警 / 恢复卡片
│   ├── notifier/                   调查事件 → 开始调查 / 根因卡片
│   ├── chat_worker/                飞书问答：调用 A2A，回复到原消息话题
│   ├── feishu_ws/                  飞书长连接客户端，运行在 ECS Fargate
│   ├── es_mcp/                     模拟 Elasticsearch MCP（AgentCore Runtime，OAuth）
│   ├── nexus_mcp/                  模拟 Nexus MCP（Lambda 函数 URL，SigV4）
│   ├── order_service/              模拟订单服务，故障注入的对象
│   └── traffic/                    每分钟调用订单服务 20 次的模拟流量
│
├── scripts/                        部署后的操作脚本，均在项目根目录执行
│   ├── setup-secrets.sh            交互录入 Webhook 与飞书凭证，回车保留原值
│   ├── register-mcp.sh             注册两个 MCP，只开放只读工具
│   ├── test-mcp.sh                 两个 MCP 的冒烟测试
│   ├── test_es_mcp.py              es-mock 的 MCP 客户端，由 test-mcp.sh 调用
│   ├── test-feishu.sh              按 Lambda 相同路径向演示群发测试卡片
│   ├── inject-fault.sh             注入故障：发布 1.4.0
│   ├── rollback.sh                 回滚到 1.3.2
│   ├── feishu-ws.sh                在本机运行长连接客户端，仅用于调试
│   └── destroy.sh                  删除栈与制品桶
│
├── skills/
│   └── order-service-release-rca/
│       └── SKILL.md                上传到 Agent Space 的排障 Skill
│
├── tests/
│   └── test_units.py               单元测试，40 个，不调用 AWS
│
└── docs/                           文档（Markdown）
    ├── 01-方案说明.md              方案选型、目标架构、Demo 验证结果、PoC 计划
    ├── 02-设计文档.md              组件、交互流程、安全、可靠性与可观测性
    ├── 03-部署手册.md              分步部署（含飞书应用配置）、演练、排障
    ├── build_docs.py               按需生成 Word / PDF（不纳入版本库）
    ├── diagrams/                   架构图：SVG 为源文件，PNG 由脚本导出
    │   ├── architecture.svg        目标架构
    │   ├── demo-deployment.svg     Demo 部署架构
    │   ├── aws-architecture.svg    Demo 部署架构（AWS 官方图标版）
    │   ├── aws_architecture.py     生成上一张图，需官方图标包
    │   ├── sequence-flows.svg      关键交互时序
    │   └── export_png.sh           SVG → 2 倍 PNG
    └── video/                      方案讲解视频的源文件，输出到 dist/video/
        ├── slides.py               幻灯片内容、高亮与旁白
        └── build_video.py          截图、Polly 配音、字幕、ffmpeg 合成
```

AWS 资源统一使用 `aiops-demo` 前缀（栈名、函数名、Secret 名等），与项目目录名无关。

## 快速开始

完整步骤（含飞书开发者后台的每一步）见《03-部署手册》。概要：

```bash
export AWS_PROFILE=<演示账号 profile> AWS_REGION=us-east-1
export AGENT_SPACE_ID=<Agent Space ID>          # 控制台创建 Agent Space 与 HMAC Webhook 后获得
ENABLE_CHAT_BOT=true ./deploy.sh                # 部署栈（含飞书长连接客户端）
./scripts/setup-secrets.sh                      # 录入 Webhook 与飞书 App ID / App Secret
# 飞书后台：事件订阅选“使用长连接接收事件”，添加“接收消息 v2.0”，发布版本，把机器人拉进群
FEISHU_CHAT_ID=<oc_ 开头的群 ID> ./deploy.sh     # 绑定演示群：卡片由应用机器人发送
./scripts/test-mcp.sh && ./scripts/register-mcp.sh
# Web App → Knowledge → Skills 上传 skills/order-service-release-rca
./scripts/inject-fault.sh                       # 演练；结束后 ./scripts/rollback.sh
```

## 开发与测试

```bash
python3.12 -m venv .venv && .venv/bin/pip install boto3==1.43.113 cfn-lint==1.57.2
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/cfn-lint template.yaml

# 本地运行 es-mock（只绑定 127.0.0.1，无认证，仅用于开发）
python3.12 -m venv .venv-esmcp && .venv-esmcp/bin/pip install -r src/es_mcp/requirements.txt
(cd src/es_mcp && MCP_HOST=127.0.0.1 LOGS_REGION=us-east-1 LOG_GROUPS='{"order-service":"/aws/lambda/aiops-demo-order-service"}' ../../.venv-esmcp/bin/python main.py) &
python3 scripts/test_es_mcp.py --local --endpoint http://127.0.0.1:8000/mcp
```

## 生成文档

仓库只保存 Markdown。需要 Word / PDF 时再生成，输出在 `docs/` 下，已被 `.gitignore` 排除：

```bash
python3.12 -m venv .venv-docs && .venv-docs/bin/pip install python-docx==1.2.0 lxml==6.1.3 typing_extensions==4.16.0
(cd docs && ../.venv-docs/bin/python build_docs.py --pdf)    # Markdown → Word，并用 LibreOffice 转 PDF
```

修改架构图时编辑 `docs/diagrams/*.svg`，再运行 `docs/diagrams/export_png.sh` 重新导出 PNG。

## 生成讲解视频

```bash
python3.12 -m venv .venv-video && .venv-video/bin/pip install imageio-ffmpeg==0.6.0 pygments==2.19.2
AWS_PROFILE=<profile> .venv-video/bin/python docs/video/build_video.py   # 输出 dist/video/方案讲解.mp4 与 .srt
```

需要 playwright-cli、Noto Sans CJK 字体，以及有 `polly:SynthesizeSpeech` 权限的 AWS 凭证。截图和配音缓存在 `build/video/`，只改一页时只重做这一页。

## 安全要点

- 所有入口都需认证：Webhook（HMAC）、A2A（SigV4）、MCP（OAuth / SigV4）；飞书事件经出站长连接接收，栈内没有公网回调地址。
- MCP 只开放只读工具，DevOps Agent 自主调查只执行只读操作，处置由人工执行。
- 凭证存放在 Secrets Manager 与 Cognito，脚本不打印凭证。

## 许可证

本项目以 [MIT 许可证](LICENSE) 发布。
