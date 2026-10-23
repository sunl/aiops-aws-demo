"""Content of the solution walkthrough video: slides, highlights and narration.

Each slide is a dict with a `kind` (see build_video.py for the renderers) and a
list of `steps`. A step is one narration segment shown over one rendered frame:

    {"say": "旁白文字", "hl": [...keys to highlight...], "label": "步骤标签"}

`hl=None` (or omitted) shows the slide without dimming. Narration is written for
display (subtitles); build_video.py rewrites a few terms for the speech engine.
Code snippets are read from the real source files at build time, located by
text markers rather than line numbers, so they follow the code when it changes.
"""


def step(say, hl=None, label=None, **extra):
    return {"say": say, "hl": hl, "label": label, **extra}


def R(start, end=None, before=0, after=0, elide=()):
    """A source range: from the first line containing `start` to the next line containing `end`."""
    return {"start": start, "end": end or start, "before": before, "after": after, "elide": list(elide)}


S1, S2, S3, S4 = "01 · 总体方案介绍", "02 · 设计说明", "03 · 架构说明", "04 · 代码介绍"

TREE = """aiops-aws-demo/
├── README.md                  项目说明与快速开始
├── template.yaml              CloudFormation 模板（全部资源）
├── deploy.sh                  打包公共层与两个 zip、部署栈
├── scripts/                   凭证录入、MCP 注册、冒烟测试、故障注入与回滚
├── skills/order-service-release-rca/SKILL.md
├── src/
│   ├── common/demo_common/    feishu.py · a2a.py · config.py
│   ├── alert_forwarder/       告警转换
│   ├── notifier/              调查事件 → 卡片
│   ├── chat_worker/           问答 Worker
│   ├── feishu_ws/             长连接客户端（Fargate）
│   ├── es_mcp/                es-mock（FastMCP）
│   ├── nexus_mcp/             nexus-mock（手写 JSON-RPC）
│   └── order_service/ · traffic/
├── tests/test_units.py        单元测试（40 个）
└── docs/                      方案说明、设计文档、部署手册、架构图与生成脚本"""

PAYLOAD = """{
  "eventType": "incident",
  "incidentId": "aiops-demo-order-service-errors-1792569600",
  "action": "created",
  "priority": "HIGH",
  "title": "[order-service] aiops-demo-order-service-errors",
  "description": "<多行中文：告警说明、原因、指标与阈值、
                  服务 / 环境 / 账号 / 区域、定位引导语>",
  "timestamp": "2026-10-21T08:00:00.000Z",
  "data": {"metadata": {"alarmArn": "…", "environment": "demo"}}
}"""

SLIDES = [
    # ------------------------------------------------------------------ opening
    {
        "kind": "cover",
        "title": "基于 AWS 的智能运维（AIOps）方案",
        "subtitle": "AWS DevOps Agent × 飞书应用机器人",
        "desc": "方案 · 设计 · 架构 · 代码",
        "foot": "github.com/sunl/aiops-aws-demo  ·  2026 年 10 月",
        "steps": [step("大家好。这个视频介绍一套基于 AWS 的智能运维方案：以 AWS DevOps Agent 为核心，"
                       "复用现有的 MCP Server 和飞书协作流程，让告警进来之后，机器人自动给出根因和处置建议。")],
    },
    {
        "kind": "agenda",
        "title": "内容安排",
        "items": [
            ("01", "总体方案介绍", "现状、目标、选型、Demo 验证与落地节奏"),
            ("02", "设计说明", "告警接入、飞书集成、MCP 接入、Skill、安全与可靠性"),
            ("03", "架构说明", "目标架构、Demo 部署架构、关键交互时序"),
            ("04", "代码介绍", "代码结构与核心模块走读"),
        ],
        "note": "部署过程见单独的部署视频",
        "steps": [step("内容分四部分：总体方案介绍、设计说明、架构说明和代码介绍。"
                       "部署过程会在另一个视频里单独讲解，这里不展开。")],
    },
    # ------------------------------------------------------------------ 01 overview
    {"kind": "section", "num": "01", "title": "总体方案介绍", "sub": "现状 · 目标 · 选型 · 验证 · 落地",
     "steps": [step("先看总体方案。")]},
    {
        "kind": "current", "section": S1, "title": "现状：一种常见的自建 AIOps 架构",
        "steps": [
            step("先看一种常见的自建 AIOps 架构。告警平台的告警和开发、运维的提问，都进入飞书机器人，再交给自建的 SRE Agent。"
                 "SRE Agent 通过 MCP Server 查询 Elasticsearch 日志、Prometheus 指标和 Nexus 制品，"
                 "结合运维编排平台做根因分析，推理用的是本地部署的开源大模型。"),
            step("这套架构可以工作，但 Agent 编排、模型部署和拓扑关联都要自己建、自己维护。"
                 "方案要替换的正是这几块，而飞书入口和现有的 MCP Server 尽量保留。", hl=["agent"]),
        ],
    },
    {
        "kind": "columns", "section": S1, "title": "目标与设计原则",
        "columns": [
            ("目标", [
                ("体验", "保留并增强“告警进来、机器人给出根因与处置建议”"),
                ("托管", "用托管服务替代自建 Agent 编排、模型部署与拓扑关联"),
                ("可控", "全过程可审计、权限可控，处置动作保留人工确认"),
                ("可度量", "以根因命中率和平均定位时间作为验收指标"),
            ]),
            ("设计原则", [
                ("托管优先", "只为飞书、Nexus 等企业特有系统保留自建集成"),
                ("复用资产", "现有 MCP Server、runbook、告警规则尽量沿用"),
                ("只读起步", "一期只开放只读数据访问，处置由人工确认后执行"),
                ("最小暴露面", "不新增未认证的公网入口，飞书事件经出站长连接接收"),
                ("用数据说话", "先用历史事故回放评估效果，再决定推广范围"),
            ]),
        ],
        "steps": [
            step("方案的目标有四个：保留并增强“告警进来、机器人给出根因”的体验；用托管服务替代自建的 Agent 编排、模型部署和拓扑关联；"
                 "全过程可审计，处置保留人工确认；效果用根因命中率和定位时间来度量。", hl=["c0"]),
            step("设计上遵循五条原则：托管优先、复用现有资产、只读起步、最小暴露面，以及用历史事故回放的数据说话。", hl=["c1"]),
        ],
    },
    {
        "kind": "bullets", "section": S1, "title": "核心选型：AWS DevOps Agent", "cols": 3,
        "items": [
            ("自主事件调查", "告警触发后自动关联指标、日志、链路、部署与配置变更，给出根因与证据链"),
            ("分阶段 Agent", "Triage 分诊 → RCA 根因分析 → Mitigation 处置方案"),
            ("应用拓扑", "自动发现账号内资源及依赖关系，作为关联分析的基础"),
            ("Skills", "把 runbook、排障经验、跳过规则写成 Markdown，并指定生效阶段"),
            ("SRE 对话", "用自然语言查询架构、指标、告警状态与部署历史"),
            ("开放集成", "MCP 接入自定义数据源；Webhook、A2A 供外部调用；调查事件发往 EventBridge"),
        ],
        "steps": [
            step("核心选型是 AWS DevOps Agent，一个由 AWS 托管的运维 Agent。告警触发后它会自动调查，"
                 "关联指标、日志、链路、部署和配置变更，给出根因和证据链；调查分为分诊、根因分析和处置方案三个阶段。", hl=[0, 1]),
            step("它会自动发现应用拓扑；团队的 runbook 可以写成 Skill；支持自然语言的 SRE 对话；"
                 "还能通过 MCP 接入自定义数据源，通过 Webhook 和 A2A 被外部系统调用，调查事件发送到 EventBridge。"
                 "这些开放接口，正是对接飞书和现有 MCP 的基础。", hl=[2, 3, 4, 5]),
        ],
    },
    {
        "kind": "table", "section": S1, "title": "现有组件与 AWS 方案映射",
        "head": ["现有组件", "AWS 方案"], "widths": [30, 70],
        "rows": [
            ["现有告警平台", "CloudWatch / Alertmanager / Grafana 告警 → 告警转换 Lambda → DevOps Agent Webhook"],
            ["飞书 Bot", "企业自建应用（应用机器人）+ 飞书集成层：Fargate 长连接、问答 Lambda、通知 Lambda"],
            ["自建 SRE Agent", "AWS DevOps Agent（托管，无需自建编排与模型）"],
            ["Elasticsearch MCP Server", "注册为自定义 MCP，建议托管在 AgentCore Runtime"],
            ["Prometheus MCP Server", "原生 Grafana 集成，或注册为自定义 MCP"],
            ["Nexus MCP Server", "注册为自定义 MCP，托管在 AgentCore Runtime 或 Lambda"],
            ["运维编排 / 根因分析平台", "应用拓扑 + CloudWatch / X-Ray / CloudTrail 关联"],
            ["本地部署的开源大模型", "DevOps Agent 托管模型；二期自建 Agent 可用 Bedrock 上的 Qwen、DeepSeek"],
        ],
        "steps": [
            step("现有组件和 AWS 方案可以一一对应。告警平台对接告警转换 Lambda，再调用 DevOps Agent 的 Webhook；"
                 "飞书机器人改成一个企业自建应用，由飞书集成层负责收发消息；自建的 SRE Agent 由 DevOps Agent 替代。", hl=[0, 1, 2]),
            step("Elasticsearch、Prometheus 和 Nexus 的 MCP Server 注册为自定义 MCP 继续使用；"
                 "运维编排平台的拓扑和根因能力，由应用拓扑加 CloudWatch、X-Ray、CloudTrail 的关联覆盖；"
                 "模型由 AWS 托管，不再需要自建推理集群。", hl=[3, 4, 5, 6, 7]),
        ],
    },
    {
        "kind": "stats", "section": S1, "title": "Demo 验证结果",
        "scene": ("场景", "订单服务发布 1.4.0 后，配置中的表名指向尚未创建的新表（发布前置的建表迁移没有执行），调用全部失败并触发告警。"
                          "期望结论：回滚到 1.3.2。"),
        "stats": [("4 / 4", "根因判定正确"), ("6–8 分钟", "告警到根因结论"), ("2 个", "自定义 MCP 每次都被调用"),
                  ("30–55 秒", "飞书 @机器人 回复")],
        "head": ["演练", "告警触发", "调查时长", "根因", "证据缺口"],
        "rows": [
            ["第 1 次", "约 2.0 分钟", "约 6.0 分钟", "正确", "CloudTrail 查询、表详情查询被取消"],
            ["第 2 次", "约 1.9 分钟", "约 7.6 分钟", "正确", "表详情查询被取消（已改用只读列表）"],
            ["第 3 次", "约 1.6 分钟", "约 6.5 分钟", "正确", "CloudTrail 查询被取消"],
            ["第 4 次", "约 2.0 分钟", "约 7.4 分钟", "正确", "无"],
        ],
        "steps": [
            step("方案已经用 Demo 做了端到端验证。场景是订单服务发布 1.4.0 之后，配置里的表名指向一张还没创建的新表，"
                 "调用全部失败，触发告警。期望的结论是：发布前置的建表迁移没有执行，建议回滚到 1.3.2。", hl=["scene"]),
            step("四次完整演练，根因判定全部正确，从告警到根因结论大约 6 到 8 分钟，ES 和 Nexus 两个自定义 MCP 每次都被调用，"
                 "第四次演练已经没有证据缺口。在飞书里 @机器人 提问，大约 30 到 55 秒收到回复。", hl=["stats", "runs"]),
        ],
    },
    {
        "kind": "bullets", "section": S1, "title": "落地节奏", "cols": 3,
        "items": [
            ("17 项先决条件", "合规与数据驻留、账号与 Agent Space、可观测性、MCP 协议与认证、网络打通、飞书应用、历史事故样本"),
            ("4 周 PoC", "第 1 周环境就绪 → 第 2 周告警自动调查 → 第 3 周飞书闭环与回放迭代 → 第 4 周正式评估"),
            ("验收标准", "根因命中率 ≥ 70%；首次根因时间 ≤ 15 分钟；飞书问答有效率 ≥ 80%；单次调查成本形成基线"),
        ],
        "aside": "后续路线：按业务线推广 → 处置对接 Systems Manager Automation（保留人工审批）→ 在 AgentCore 上构建 Dev Agent，自动生成修复 PR",
        "steps": [
            step("落地建议分步推进。先确认 17 项先决条件，包括数据合规、账号、可观测性、MCP、网络和飞书应用。"
                 "然后用 4 周做 PoC，以 20 到 50 起根因已知的历史事故回放作为评估基线。", hl=[0, 1]),
            step("验收建议看三个指标：根因命中率不低于 70%，首次根因时间不超过 15 分钟，飞书问答有效率不低于 80%。"
                 "之后按业务线推广，二期可以在 AgentCore 上构建自定义 Agent，比如自动生成修复 PR，并保留人工审批。", hl=[2, "aside"]),
        ],
    },
    # ------------------------------------------------------------------ 02 design
    {"kind": "section", "num": "02", "title": "设计说明", "sub": "告警接入 · 飞书集成 · MCP 接入 · Skill · 安全与可靠性",
     "steps": [step("第二部分是设计说明。")]},
    {
        "kind": "table", "section": S2, "title": "Demo 设计目标",
        "lead": "一个 CloudFormation 栈，搭建闭环：发布后故障 → 告警 → 自动调查 → 根因卡片 → @机器人 追问 → 人工回滚 → 告警恢复",
        "head": ["目标", "设计落点"], "widths": [28, 72],
        "rows": [
            ["与现有拓扑一一对应", "告警转换 ↔ 告警平台；应用机器人 ↔ 飞书机器人；DevOps Agent ↔ SRE Agent 与编排平台；两个模拟 MCP ↔ ES 与 Nexus"],
            ["不新增未认证的公网入口", "所有入口都要认证；飞书事件经出站长连接接收"],
            ["可重复部署与清理", "一个模板加几个脚本；凭证交互录入；一键删除"],
            ["演示稳定", "故障可注入、可回滚；告警去重；卡片发送失败不影响调查"],
            ["代码可迁移到 PoC", "集成层、告警转换与通知逻辑与业务无关，可直接对接实际告警源与运维群"],
        ],
        "steps": [
            step("Demo 用一个 CloudFormation 栈，在单个账号里搭建完整闭环：业务故障、告警、自动调查、飞书根因卡片、"
                 "@机器人 追问、人工回滚，直到告警恢复。", hl=["lead"]),
            step("设计目标有五条：和现有拓扑一一对应；不新增未认证的公网入口；可以重复部署和清理；演示稳定；"
                 "集成层代码可以直接迁移到 PoC。", hl=[0, 1, 2, 3, 4]),
        ],
    },
    {
        "kind": "flow", "section": S2, "title": "告警触发与自动调查流程",
        "nodes": [
            ("1", "故障产生指标", "order-service · Lambda",
             "先写结构化错误日志，再重新抛出异常，调用计为失败、计入 `Errors`"),
            ("2", "告警进入 ALARM", "CloudWatch 告警 → SNS",
             "Sum · 60 秒 · 1 个数据点 ≥ 5；缺数据视为未超阈值；ALARM / OK 都发 SNS"),
            ("3", "去重收敛", "alert-forwarder · DynamoDB",
             "条件写入 `alarm#<告警名>`（15 分钟）；写入失败即收敛返回"),
            ("4", "签名调用 Webhook", "alert-forwarder → DevOps Agent",
             "incident 载荷 + HMAC 签名；返回 200 后推送告警卡片"),
            ("5", "创建调查", "DevOps Agent → EventBridge",
             "发出 `Investigation Created`；notifier 推送“开始调查”卡片"),
            ("6", "按 Skill 取证", "DevOps Agent · MCP",
             "ES 错误摘要 → CloudTrail（子任务）→ Nexus 发布说明 → `list_tables`"),
            ("7", "推送根因", "EventBridge → notifier",
             "`Investigation Completed`；用 `ListJournalRecords` 读摘要，推送根因卡片"),
            ("8", "人工回滚与恢复", "值班人员 · alert-forwarder",
             "告警回到 OK；同一 incidentId 发 `resolved`，不新建调查；推送恢复卡片"),
        ],
        "timing": [("注入故障 → ALARM", "约 1.5–2 分钟"), ("ALARM → 调查开始", "约 13–14 秒"),
                   ("调查开始 → 根因卡片", "约 6–8 分钟")],
        "timing_label": "4 次演练实测",
        "note": "为什么需要告警转换：SNS 的 HTTPS 订阅只按 SNS 自己的格式投递，生成不了 incident 载荷和 HMAC 签名；"
                "调查上下文与去重也在这一层统一处理。告警平台能直接签名调用 Webhook 时，可省掉这一层。",
        "steps": [
            step("这一页把告警触发到自动调查的完整过程串起来，一共八步：业务故障产生指标，告警、去重、调用 Webhook，"
                 "再到 DevOps Agent 调查、推送根因，最后人工回滚、告警恢复。", label="总览"),
            step("第一步，故障要先变成指标。订单服务调用失败时，先写一条结构化错误日志，记下错误类型、版本和表名，"
                 "再把异常重新抛出，这次调用才会计入 Lambda 的 Errors 指标。只记日志、不抛异常的话，指标不会变化，告警也不会触发。",
                 hl=[0]),
            step("第二步，CloudWatch 告警按 60 秒周期统计 Errors 的总数，只要 1 个数据点达到 5 次就进入 ALARM；"
                 "没有数据时按未超阈值处理。ALARM 和 OK 两种状态都发到同一个 SNS 主题，告警和恢复走同一条链路。", hl=[1]),
            step("第三步和第四步在告警转换 Lambda 里完成。先用 DynamoDB 条件写入按告警名去重，15 分钟内同一告警只建一个事件；"
                 "写入成功后构造 incident 载荷，用 HMAC 签名调用 DevOps Agent 的 Webhook，返回 200 之后再推送告警卡片。", hl=[2, 3]),
            step("为什么不让 SNS 直接调用 Webhook？SNS 的 HTTPS 订阅只按 SNS 自己的格式投递，生成不了 Webhook 要求的载荷和 HMAC 签名；"
                 "调查上下文和去重也适合放在这一层统一处理。如果现有告警平台能按同样的格式签名调用 Webhook，生产环境可以省掉这一层。",
                 hl=["note"]),
            step("第五步，DevOps Agent 创建调查，向 EventBridge 发出 Investigation Created 事件，通知 Lambda 推送“开始调查”卡片。"
                 "第六步，Agent 按 Skill 的顺序取证：ES 错误摘要、交给子任务查询的 CloudTrail 变更、Nexus 发布说明，"
                 "最后用 list_tables 核对表是否存在。", hl=[4, 5]),
            step("第七步，调查完成，发出 Investigation Completed 事件。事件只带记录 ID，通知 Lambda 用 ListJournalRecords 读取摘要，推送根因卡片。"
                 "第八步，值班人员确认后人工回滚，告警回到 OK，告警转换用同一个 incidentId 发送 resolved，不会新建调查，并推送恢复卡片。",
                 hl=[6, 7]),
            step("按四次演练的实测，注入故障后约 1.5 到 2 分钟告警进入 ALARM，时间主要花在指标按分钟聚合和告警评估上；"
                 "进入 ALARM 后约 13 到 14 秒调查开始；调查本身约 6 到 8 分钟。载荷和签名的细节，下一页展开。", hl=["time"]),
        ],
    },
    {
        "kind": "split", "section": S2, "title": "告警接入设计",
        "left": [
            ("载荷", "Agent 只读取 title、description、priority 与事件 ID，关键上下文全部写入 description"),
            ("唯一性", "incidentId = 告警名 + 时间戳；恢复时沿用同一 ID，action = resolved"),
            ("收敛", "同一告警 15 分钟内只建一个事件（DynamoDB 条件写入）"),
            ("签名", "HMAC：base64(HMAC-SHA256(密钥, 时间戳:请求体))"),
            ("重试与隔离", "5xx 共尝试 3 次，4xx 立即失败；飞书卡片单独捕获异常"),
        ],
        "right": {"type": "code", "label": "Webhook 载荷（示例）", "lang": "json", "text": PAYLOAD},
        "steps": [
            step("告警接入的关键是 Webhook 载荷。DevOps Agent 只读取 title、description、priority 和事件 ID，"
                 "所以服务名、环境、指标和阈值，以及“请结合配置变更和发布记录定位根因”的引导语，都要写进 description。"
                 "incidentId 用告警名加时间戳，保证唯一。", hl=["L0", "L1", "R"]),
            step("同一个告警 15 分钟内只建一个事件，避免告警风暴占满并发配额。请求用 HMAC 签名；遇到 5xx 最多尝试三次，4xx 立即失败；"
                 "飞书卡片单独捕获异常，飞书出问题也不影响调查触发。", hl=["L2", "L3", "L4"]),
        ],
    },
    {
        "kind": "bullets", "section": S2, "title": "飞书集成设计", "cols": 2,
        "items": [
            ("单一应用机器人", "企业自建应用开启机器人能力；告警、调查、根因、恢复卡片与问答回复都由它发送"),
            ("两项权限", "`im:message.group_at_msg:readonly` 接收群内 @ 消息；`im:message:send_as_bot` 以应用身份发消息"),
            ("长连接接收事件", "客户端经飞书官方 SDK 主动建立 WebSocket；无需公网 IP、域名或回调地址；安全组无入站规则"),
            ("3 秒时限", "客户端只做校验与转交；耗时的 Agent 调用放在异步 Lambda 中"),
            ("幂等与访问控制", "按 `message_id` 条件写入去重；只处理白名单群中 @机器人 的消息；忽略机器人消息"),
            ("租户与凭证", "飞书与 Lark 通过配置切换开放平台地址；App ID / App Secret 存放在 Secrets Manager"),
        ],
        "steps": [
            step("飞书侧只用一个企业自建应用的机器人，权限只要两项：接收群里 @机器人 的消息，以及以应用身份发消息。"
                 "告警、调查、根因、恢复卡片和问答回复，都由它发送。", hl=[0, 1]),
            step("事件订阅采用长连接：客户端用飞书官方 SDK 主动连出去，不需要公网 IP、域名或回调地址，安全组没有任何入站规则。"
                 "Demo 中公网回调曾因账号的安全基线失效，改用长连接后问题消失，暴露面也更小。", hl=[2]),
            step("长连接要求 3 秒内处理完，所以客户端只做群白名单校验和 message_id 去重，然后异步转交，耗时的 Agent 调用放在 Lambda 里。"
                 "App ID 和 App Secret 存在 Secrets Manager，飞书和 Lark 通过配置切换。", hl=[3, 4, 5]),
        ],
    },
    {
        "kind": "split", "section": S2, "title": "MCP 接入设计", "ratio": [38, 62],
        "left": [
            ("传输", "Streamable HTTP（stdio / SSE 需改造或加适配层）"),
            ("访问", "HTTPS 端点"),
            ("认证", "OAuth 2.0 客户端凭证、API Key、Bearer Token、SigV4"),
            ("工具分类", "READ_ONLY / MUTATIVE / DESTRUCTIVE，一期全部只读"),
        ],
        "right": {"type": "table", "label": "两种托管方式（Demo 中均已实现）", "head": ["方式", "认证", "端点", "适合场景"],
                  "rows": [
                      ["A. AgentCore Runtime", "OAuth（JWT 授权器）", "公网可达，必须认证", "迁移现有标准 MCP Server，代码基本不改"],
                      ["B. Lambda + 函数 URL", "SigV4（AWS_IAM）", "公网可达，必须认证", "新写的轻量只读工具"],
                  ]},
        "steps": [
            step("自定义 MCP Server 要满足几项要求：Streamable HTTP 传输、HTTPS 访问、支持 OAuth、API Key 或 SigV4 等认证方式；"
                 "每个工具都要标注只读、变更或破坏性，一期全部只读。", hl=["L"]),
            step("托管方式有两种。A 是 AgentCore Runtime，用 OAuth 认证，现有的标准 MCP Server 基本不用改；"
                 "B 是 Lambda 加函数 URL，用 SigV4 认证，适合新写的轻量工具。"
                 "Demo 用这两种方式各实现了一个，未签名的请求在服务入口就被拒绝，返回 403。", hl=["R"]),
        ],
    },
    {
        "kind": "split", "section": S2, "title": "Skill 与只读调查", "ratio": [52, 48],
        "left": [
            ("写法", "description 写明适用场景；正文写取证顺序与每一步用的工具；输出结构固定"),
            ("只读替代", "表是否存在用 `list_tables` 核对，不调用 `describe_table`"),
            ("子任务查询", "CloudTrail 变更交给后台子任务；查不到时用函数 `LastModified` 与 Nexus 部署记录"),
            ("人工处置", "Skill 只给处置步骤，不执行变更"),
        ],
        "right": {"type": "table", "label": "演练中被取消的操作", "head": ["演练", "被取消的操作", "处理"],
                  "rows": [
                      ["第 1 次", "CloudTrail 查询、表详情查询", "—"],
                      ["第 2 次", "表详情查询", "Agent 自行改用列表查询"],
                      ["第 3 次", "CloudTrail 查询", "Skill 写明只读替代"],
                      ["第 4 次", "无", "Skill 写明子任务查询"],
                  ]},
        "steps": [
            step("DevOps Agent 的自主调查只执行只读操作，被判定为需要审批的调用会直接取消。"
                 "前三次演练里，表详情查询和主流程中的 CloudTrail 查询都被取消过，留下了证据缺口。", hl=["R"]),
            step("办法是写进 Skill：表是否存在改用只读的 list_tables 核对；CloudTrail 变更交给后台子任务查询，并写明查不到时的替代依据。"
                 "改完之后，第四次演练没有再出现证据缺口。Skill 里也写明只给处置步骤，不执行任何变更。", hl=["L"]),
        ],
    },
    {
        "kind": "table", "section": S2, "title": "安全设计：入口与认证",
        "head": ["入口", "方向", "认证", "可达性"], "widths": [24, 28, 28, 20],
        "rows": [
            ["DevOps Agent Webhook", "告警转换 → Agent", "HMAC（时间戳签名）", "AWS 托管端点"],
            ["DevOps Agent A2A", "问答 Worker → Agent", "SigV4 + Agent Space ID", "AWS 托管端点"],
            ["es-mock", "Agent → AgentCore Runtime", "OAuth（Cognito 令牌）", "公网可达，必须认证"],
            ["nexus-mock", "Agent → 函数 URL", "SigV4（未签名返回 403）", "公网可达，必须认证"],
            ["飞书事件", "客户端 → 飞书（出站）", "App ID / App Secret", "无入站端点"],
            ["飞书消息发送", "Lambda → 飞书开放平台", "tenant_access_token", "出站"],
        ],
        "note": "IAM 权限限定到具体资源 · 凭证只在 Secrets Manager 与 Cognito · 依赖与镜像锁定版本 · MCP 工具只读且参数白名单校验",
        "steps": [
            step("安全设计的底线是：没有未经认证的公网入口。Webhook 用 HMAC，A2A 用 SigV4，es-mock 用 OAuth，nexus-mock 用 SigV4，"
                 "飞书事件走出站长连接，订单服务本身没有任何外部入口。", hl=[0, 1, 2, 3, 4, 5]),
            step("此外，IAM 权限全部限定到具体资源；凭证只放在 Secrets Manager 和 Cognito，脚本和日志里都不出现；"
                 "依赖和容器镜像锁定版本；MCP 工具全部只读，查询参数做白名单校验，防止注入。", hl=["note"]),
        ],
    },
    {
        "kind": "table", "section": S2, "title": "可靠性与错误处理",
        "head": ["故障", "行为"], "widths": [40, 60],
        "rows": [
            ["同一告警反复触发", "15 分钟内只创建一个事件"],
            ["Webhook 5xx / 网络错误", "共尝试 3 次，退避重试"],
            ["飞书重推同一消息", "按 message_id 去重丢弃"],
            ["长连接断开 / 任务退出", "SDK 自动重连；ECS 拉起新任务"],
            ["凭证尚未录入", "客户端等待，录入后 30 秒内连接"],
            ["新版本客户端无法启动", "ECS 部署熔断，自动回滚"],
            ["A2A 续接失败（HTTP 400）", "自动开启新对话"],
            ["问答 Worker 失败", "回复“处理失败”卡片，不自动重试"],
        ],
        "steps": [
            step("可靠性方面，每类故障都有明确的行为：重复告警被收敛；Webhook 出错会退避重试；飞书重推的消息按 message_id 丢弃；"
                 "长连接断开由 SDK 自动重连，任务退出由 ECS 拉起；凭证还没录入时，客户端等待而不退出；"
                 "新版本起不来会被部署熔断自动回滚；问答失败只回复一张失败卡片，不重试，避免重复回复。"),
        ],
    },
    # ------------------------------------------------------------------ 03 architecture
    {"kind": "section", "num": "03", "title": "架构说明", "sub": "目标架构 · Demo 部署架构 · 关键交互时序",
     "steps": [step("第三部分是架构说明。")]},
    {
        "kind": "diagram", "section": S3, "title": "目标架构", "svg": "architecture.svg", "zoom": True,
        "steps": [
            step("这是目标架构，从左到右依次是：告警源和飞书群、自有 AWS 账号里的集成层、托管的 DevOps Agent，以及它读取的数据源。",
                 label="总览"),
            step("告警源可以是 CloudWatch、Prometheus Alertmanager 或 Grafana。告警经 SNS 或 EventBridge 进入告警转换 Lambda，"
                 "标准化、去重之后，签名调用 Webhook。", label="告警接入",
                 rects=[(20, 60, 220, 160), (315, 95, 310, 125)]),
            step("飞书集成层有三部分：Fargate 上的长连接客户端接收 @ 消息；问答 Worker 调用 A2A；结果通知 Lambda 订阅调查事件并推送卡片。"
                 "状态存在 DynamoDB，凭证存在 Secrets Manager。", label="飞书集成层",
                 rects=[(20, 300, 220, 290), (315, 240, 310, 350)]),
            step("中间是 DevOps Agent。Webhook 入口触发调查；Agent Space 按业务线划分，结合拓扑和变更记录分析；"
                 "A2A 入口提供对话和调查；调查事件从 EventBridge 发出。模型由 AWS 托管，自主调查只执行只读操作。",
                 label="DevOps Agent", rects=[(690, 60, 270, 640)]),
            step("原生集成通过 IAM 只读角色，直接读取 CloudWatch、X-Ray、CloudTrail、EKS、ECS、RDS 和代码仓库，不需要网络打通。",
                 label="原生集成", rects=[(1010, 60, 370, 190)]),
            step("现有的 ES、Prometheus、Nexus 通过自定义 MCP Server 接入，托管方式就是前面讲的 AgentCore Runtime 和 Lambda 两种；"
                 "数据后端不在同一区域时，用 Transit Gateway、VPN 或专线打通。", label="自定义 MCP",
                 rects=[(1010, 275, 370, 325), (1010, 622, 370, 78)]),
            step("底部是贯穿全局的安全与治理：最小权限、客户托管密钥、CloudTrail 审计、凭证托管、用量与成本告警，"
                 "处置动作由值班人员确认后执行。", label="安全与治理", rects=[(20, 722, 1360, 60)]),
        ],
    },
    {
        "kind": "diagram", "section": S3, "title": "Demo 部署架构", "svg": "demo-deployment.svg", "zoom": True,
        "steps": [
            step("Demo 把目标架构收敛到一个 CloudFormation 栈。左侧是演示脚本和飞书演示群，中间是业务、告警和飞书集成三组组件，"
                 "右侧是 DevOps Agent 和两个模拟 MCP。", label="总览"),
            step("第一组是模拟业务：定时规则每分钟调用订单服务 20 次。故障注入就是发布 1.4.0，把表名指向一张不存在的表。",
                 label="① 模拟业务", rects=[(290, 60, 300, 250)]),
            step("第二组是告警转换：CloudWatch 告警在每分钟错误数达到 5 次时触发，经 SNS 交给 alert-forwarder。",
                 label="② 告警转换", rects=[(290, 335, 300, 190)]),
            step("第三组是飞书集成：Fargate 长连接客户端、问答 chat-worker 和通知 feishu-notifier。所有卡片都由同一个应用机器人发到演示群。",
                 label="③ 飞书集成", rects=[(20, 550, 230, 250), (290, 550, 300, 250)]),
            step("DevOps Agent 侧配置了只读的原生数据源、排障 Skill 和 4 个只读 MCP 工具，并开放 Webhook、A2A 和 EventBridge 三个对接点。",
                 label="DevOps Agent", rects=[(650, 60, 280, 740)]),
            step("es-mock 托管在 AgentCore Runtime，用 Cognito 签发的令牌认证，查询 CloudWatch Logs，返回 Elasticsearch 风格的结果；"
                 "nexus-mock 托管在 Lambda 函数 URL，用 SigV4 认证，返回发布说明，其中写着 1.4.0 的建表迁移尚未执行。",
                 label="两个模拟 MCP", rects=[(990, 60, 390, 465)]),
            step("共享资源包括 DynamoDB 状态表、Secrets Manager 凭证和 S3 制品桶。", label="共享资源",
                 rects=[(990, 550, 390, 250)]),
        ],
    },
    {
        "kind": "diagram", "section": S3, "title": "关键交互时序", "svg": "sequence-flows.svg",
        "steps": [
            step("再看关键交互的时序：上半部分是告警自动调查，第二部分已经逐步讲过，"
                 "这张图可以对照各组件之间的调用顺序；下面重点看下半部分的飞书问答。",
                 label="A 告警自动调查", rects=[(18, 112, 1364, 544)]),
            step("问答流程中，客户端启动时主动建立长连接。@ 消息到达后，3 秒内完成校验、去重和异步转交；"
                 "问答 Worker 调用 A2A 的 chat 模式，在原消息话题内回复，并把 taskId 存进 DynamoDB，延续多轮对话。"
                 "如果发的是“调查”加问题描述，就走 investigation 模式，结果按上面的流程回推到同一话题。",
                 label="B 飞书问答", rects=[(18, 664, 1364, 242)]),
        ],
    },
    {
        "kind": "table", "section": S3, "title": "状态存储：DynamoDB aiops-demo-state",
        "head": ["键", "写入方", "内容", "有效期"], "widths": [32, 20, 28, 20], "mono_first": True,
        "rows": [
            ["alarm#<告警名>", "告警转换", "当前事件 ID", "15 分钟（恢复时删除）"],
            ["feishu-msg#<message_id>", "长连接客户端", "去重标记", "1 天"],
            ["conv#<root_id>", "问答 Worker", "A2A taskId", "2 小时"],
            ["task#<任务 ID>", "问答 Worker", "发起调查的飞书消息 ID", "1 天"],
        ],
        "note": "分区键 pk · 按需计费 · ttl 属性自动过期，无需清理任务",
        "steps": [
            step("所有状态都放在同一张 DynamoDB 表里，用键前缀区分：alarm 用于告警去重，feishu-msg 用于消息去重，"
                 "conv 保存多轮会话，task 记录调查和飞书话题的对应关系。全部靠 TTL 自动过期，不需要清理任务。"),
        ],
    },
    # ------------------------------------------------------------------ 04 code
    {"kind": "section", "num": "04", "title": "代码介绍", "sub": "沿着“告警 → 调查 → 通知 → 问答”的链路走读",
     "steps": [step("第四部分，我们沿着告警、调查、通知和问答的链路走读代码。")]},
    {
        "kind": "code", "section": S4, "title": "代码结构", "label": "aiops-aws-demo/", "lang": "text", "text": TREE,
        "steps": [
            step("项目根目录下，template.yaml 定义全部资源，deploy.sh 负责打包和部署，scripts 目录是演示和测试脚本，"
                 "skills 目录是排障 Skill。", hl=["template.yaml", "deploy.sh", "scripts/", "skills/"]),
            step("src 目录是业务代码：common 是公共层，封装飞书、A2A 和配置读取；alert_forwarder、notifier、chat_worker、feishu_ws 是集成层；"
                 "es_mcp 和 nexus_mcp 是两个模拟 MCP；order_service 和 traffic 是模拟业务。", hl=[("src/", "order_service")]),
            step("tests 目录有 40 个单元测试，docs 目录是三份文档、架构图和生成脚本。", hl=["tests/", "docs/"]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "告警转换：去重收敛与触发顺序", "file": "src/alert_forwarder/app.py",
        "ranges": [R('if state == "ALARM":', 'return {"incident_id": incident_id, "action": "created"}')],
        "steps": [
            step("先看告警转换。告警进入 ALARM 时，用 DynamoDB 条件写入做去重：键是告警名，"
                 "写入失败说明已经有未结束的事件，本次告警直接收敛返回。",
                 hl=[("table.put_item(", 'return {"suppressed": True}')]),
            step("写入成功才调用 Webhook，然后推送告警卡片。飞书通知放在 Webhook 之后，并且单独捕获异常，飞书故障不会影响调查。",
                 hl=["post_webhook(", "_notify_feishu("]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "告警转换：Webhook 载荷与 HMAC 签名", "file": "src/alert_forwarder/app.py",
        "ranges": [R("def build_incident(", "return payload"), R("def sign(", "return base64.b64encode")],
        "steps": [
            step("Webhook 载荷里，title 带上服务名，description 由 describe_alarm 生成多行中文说明，incidentId 由告警名加时间组成。"
                 "恢复时沿用同一个 incidentId，action 改成 resolved，description 换成恢复说明。",
                 hl=['"title"', '"description"', "if action != ", 'payload["description"]']),
            step("签名按文档要求：对“时间戳、冒号、请求体”做 HMAC-SHA256，再做 Base64 编码，放进请求头。",
                 hl=[("def sign(", "return base64.b64encode")]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "排障 Skill：取证顺序与只读替代",
        "file": "skills/order-service-release-rca/SKILL.md", "lang": "markdown", "wrap": True,
        "ranges": [R("---", "description:"), R("## 排查步骤", "处置建议只给出步骤")],
        "steps": [
            step("调查的方法写在 Skill 里。frontmatter 的 description 说明适用场景；排查步骤给出取证顺序："
                 "先查 ES 错误摘要，再查变更，再读 Nexus 发布说明，最后核对资源是否存在。",
                 hl=["description:", ("## 排查步骤", "5. 判断根因")]),
            step("第二步和第四步写明了只读替代：CloudTrail 交给后台子任务，表用 list_tables 核对。"
                 "输出要求固定为根因、证据链、影响范围、处置建议和预防措施，并且只给步骤、不执行变更。",
                 hl=["2. 核对", "4. 核对", ("## 输出要求", "处置建议只给出步骤")]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "es-mock：标准 FastMCP 迁移到 AgentCore Runtime", "file": "src/es_mcp/main.py",
        "ranges": [R("mcp = FastMCP(", "READ_ONLY = ToolAnnotations"), R("def es_error_summary(", "return es_tools.error_summary", before=1)],
        "steps": [
            step("es-mock 用的是标准 MCP SDK 的 FastMCP。迁移到 AgentCore Runtime 只需要三处设置：监听 0.0.0.0、端口 8000、无状态 HTTP。"
                 "认证由 Runtime 的 JWT 授权器在请求进入代码之前完成。",
                 hl=["host=", "port=", "stateless_http"]),
            step("每个工具都用 readOnlyHint 标注为只读，参数用类型和取值范围约束，例如时间窗口限定在 1 到 1440 分钟；每次调用先写一条审计日志。",
                 hl=["READ_ONLY = ", "@mcp.tool", "minutes:", "_audit("]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "es-mock：查询参数的白名单校验", "file": "src/es_mcp/es_tools.py",
        "ranges": [R("# User text goes into a Logs Insights", "_SAFE_TEXT = "), R("def build_search_query(", 'return " | ".join(parts)')],
        "steps": [
            step("工具参数会拼进 Logs Insights 查询，所以 query 只接受字母、数字、空白和少数几个符号，最长 100 个字符；"
                 "level 和 size 也做白名单与范围校验，服务名只能映射到预先配置的日志组。这就是防注入的输入校验。",
                 hl=["_SAFE_TEXT", "raise ToolError"]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "nexus-mock：Lambda 上手写的 JSON-RPC", "file": "src/nexus_mcp/app.py",
        "ranges": [R("def handle_rpc(", 'return _error(req_id, -32601',
                     elide=[('"protocolVersion": requested', '"instructions":')])],
        "steps": [
            step("nexus-mock 没有用 SDK，而是手写了 JSON-RPC 的最小子集：initialize、ping、tools/list 和 tools/call，"
                 "通知类消息直接返回 202。", hl=['if method == "initialize"', 'if method == "ping"', 'if method == "tools/list"',
                                               "if req_id is None"]),
            step("tools/call 按工具名分派到实现函数，结果以文本内容返回。它部署在 Lambda 函数 URL 上，认证由 AWS_IAM 完成，"
                 "未签名的请求根本不会执行这段代码。", hl=[('if method == "tools/call"', '"isError": True')]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "通知：调查事件 → 飞书卡片", "file": "src/notifier/app.py",
        "ranges": [R("def fetch_summary(", "return summary_text(record.get", after=1), R("def deliver(", "return via")],
        "steps": [
            step("通知 Lambda 订阅 aws.aidevops 的调查事件。事件里只有记录 ID，所以用 ListJournalRecords 按 ID 找到调查摘要正文，再生成根因卡片。",
                 hl=[("def fetch_summary(", "return summary_text")]),
            step("发送目标有优先级：如果调查是从飞书话题里发起的，就回复到那个话题；否则以应用机器人身份发到演示群。",
                 hl=[("def deliver(", "return via")]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "长连接客户端：过滤、去重、异步转交", "file": "src/feishu_ws/client.py",
        "ranges": [R("def __call__(self, payload", 'return "accepted"')],
        "steps": [
            step("长连接客户端的核心是这个分发器。先调用 route 过滤：机器人自己发的消息，以及不在白名单里的群，一律忽略。",
                 hl=[("kind, value = route(", 'return "ignored"')]),
            step("然后用 message_id 做条件写入去重。飞书重推时可能带新的 event_id，所以不用它做键。",
                 hl=[("self.table.put_item(", 'return "duplicate"')]),
            step("最后以 Event 方式异步调用问答 Worker，只转交消息字段，发送者身份不出客户端。整个回调在 3 秒内完成。",
                 hl=[("self.lambda_client.invoke(", 'return "accepted"')]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "长连接客户端：启动与重连", "file": "src/feishu_ws/client.py",
        "ranges": [R("cfg = wait_for_credentials(", ").start()")],
        "steps": [
            step("启动时先等凭证。栈创建的 Secret 是空的，客户端每 30 秒检查一次，读到 App ID 和 App Secret 才连接，"
                 "这样首次部署不会被 ECS 的部署熔断回滚。", hl=["cfg = wait_for_credentials("]),
            step("消息回调里捕获所有异常，不往外抛；长连接用飞书官方 SDK 的 ws.Client，开启自动重连。"
                 "另外，SDK 打印的连接地址带临时票据，日志过滤器会把它脱敏。",
                 hl=[("def on_message", 'LOG.exception("dispatch failed")'), ("lark.ws.Client(", ").start()")]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "问答 Worker：命令解析与多轮会话", "file": "src/chat_worker/app.py",
        "ranges": [R("def parse_command(", 'return "chat", text'), R('conv_key = {"pk"', "table.put_item(Item={**conv_key")],
        "steps": [
            step("问答 Worker 先解析命令：空消息回复使用说明；以“调查”开头的走 investigation 模式；其余都是普通对话。",
                 hl=[("def parse_command(", 'return "chat", text')]),
            step("多轮对话以话题根消息的 ID 为键，保存 A2A 返回的 taskId，下一轮带上它延续上下文，有效期两小时。",
                 hl=[('conv_key = {"pk"', "table.put_item(Item={**conv_key")]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "A2A 客户端：SigV4 签名与对话续接", "file": "src/common/demo_common/a2a.py",
        "ranges": [R('headers = {"A2A-Version"', "SigV4Auth(creds"), R("def chat(", 'return extract_text(resp), task.get("id")')],
        "steps": [
            step("A2A 客户端用 botocore 做 SigV4 签名，签名服务名是 aidevops，请求头带上 A2A 版本和 Agent Space ID。",
                 hl=['headers = {"A2A-Version"', "SigV4Auth(creds"]),
            step("chat 模式同步返回回答；如果带着旧的 taskId 续接失败，返回 400，就自动开启一轮新对话。",
                 hl=[("except A2AError", "raise")]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "模板：es-mock 的 AgentCore Runtime", "file": "template.yaml", "lang": "yaml",
        "ranges": [R("EsMcpRuntime:", "LifecycleConfiguration:")],
        "steps": [
            step("再看模板。es-mock 用 AgentCore Runtime 的直接代码部署：代码包放在 S3，运行时 Python 3.12，协议设为 MCP。",
                 hl=[("AgentRuntimeArtifact:", "EntryPoint:"), "ProtocolConfiguration"]),
            step("认证配置为自定义 JWT 授权器，发现地址指向 Cognito 用户池，只允许指定的机器对机器客户端调用。",
                 hl=[("AuthorizerConfiguration:", "AllowedClients")]),
        ],
    },
    {
        "kind": "code", "section": S4, "title": "模板：长连接客户端不构建镜像", "file": "template.yaml", "lang": "yaml",
        "ranges": [R("      ContainerDefinitions:", "Command: [\"python -m zipfile",
                     elide=[("          LogConfiguration:", "awslogs-stream-prefix: fetch")])],
        "steps": [
            step("长连接客户端不构建镜像。任务里有两个容器：fetch 用锁定版本的 aws-cli 镜像，把代码包从 S3 拷到共享卷之后退出。",
                 hl=[("- Name: fetch", "MountPoints")]),
            step("client 等 fetch 成功之后启动，用 python 3.12 slim 镜像解压并运行。两个镜像都按 digest 锁定；"
                 "代码变化时包名随哈希变化，触发 ECS 滚动更新。", hl=[("- Name: client", "Command:")]),
        ],
    },
    {
        "kind": "bullets", "section": S4, "title": "测试与质量", "cols": 2,
        "items": [
            ("单元测试 40 个", "告警载荷与签名、去重、卡片构造、A2A 解析、MCP 工具与注入防护、长连接路由 / 去重 / 转交 / 凭证等待 / 日志脱敏"),
            ("模板检查", "cfn-lint 与 validate-template 无错误；Rules 在缺少代码包或网络参数时拒绝部署"),
            ("冒烟测试", "`test-mcp.sh`：两个 MCP 的 initialize、tools/list、tools/call；`test-feishu.sh`：按 Lambda 相同路径发送测试卡片"),
            ("端到端演练", "4 次完整演练：注入 → 告警 → 调查 → 根因卡片 → 回滚 → 恢复"),
        ],
        "aside": "运行单元测试：.venv/bin/python -m unittest discover -s tests",
        "steps": [
            step("质量方面，tests 目录有 40 个单元测试，覆盖告警载荷与签名、去重、卡片、A2A 解析、MCP 工具与注入防护，以及长连接的路由、去重和日志脱敏。"
                 "模板用 cfn-lint 检查，并通过 Rules 在缺少代码包或网络参数时拒绝部署。两个冒烟脚本分别验证 MCP 和飞书发送，最后是四次端到端演练。"),
        ],
    },
    # ------------------------------------------------------------------ closing
    {
        "kind": "end", "title": "总结",
        "points": [
            "托管的 DevOps Agent 替代自建 Agent 编排与模型",
            "复用现有 MCP Server，托管在 AgentCore Runtime 或 Lambda",
            "飞书体验不变：一个应用机器人 + 出站长连接",
            "所有入口都要认证；调查只读，处置人工确认",
        ],
        "links": [
            ("代码与文档", "github.com/sunl/aiops-aws-demo"),
            ("配套文档", "01-方案说明 · 02-设计文档 · 03-部署手册"),
            ("部署过程", "见部署视频"),
        ],
        "steps": [
            step("总结一下：方案用托管的 DevOps Agent 替代自建的 Agent 编排和模型，复用现有 MCP Server，飞书体验保持不变；"
                 "所有入口都要认证，调查只读，处置由人工确认。Demo 的代码、三份文档和架构图都在 GitHub 仓库 aiops-aws-demo 里，"
                 "部署过程请看单独的部署视频。谢谢观看。"),
        ],
    },
]
