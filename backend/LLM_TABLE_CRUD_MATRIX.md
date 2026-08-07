# Table 关联与 LLM CRUD 矩阵

审计日期：2026-08-07。这里的 “LLM” 指 Python 对话代理
`app/orchestrator.py`，不是 Express 管理后台。项目已移除通用
`/api/llm/*` CRUD；模型只能调用按场景开放的 typed tools，不能自行选择表名或执行 SQL。

缩写：C = Create、R = Read、U = Update、D = Delete、— = 不允许。

| Table | 主要关联（同一 `company_id`） | LLM CRUD | 何时会发生 |
|---|---|---:|---|
| `companies` | 根租户；被所有业务表引用 | R | 建立公司上下文、回答公司资料/政策；LLM 不修改设置 |
| `accounts` | `company_id → companies`、`auth_user_id → auth.users` | — | 登录与角色由 Express/Supabase Auth 处理，LLM 无工具 |
| `customer` | `company_id → companies`；被 `pet`、`loyaltymember` 引用 | C/R | 按 webhook/会话手机号读取；仅在 `MAKE_BOOKING` 或 `MEMBER` 场景、确认客户不存在且取得姓名后新增；无 U/D 工具 |
| `pet` | `(company_id, customer_id) → customer` | C/R/U | `MAKE_BOOKING` 时新增；读取必须属于当前客户；U 仅限客户主动报告疫苗到期日，且写入待现场核验证明的备注；无 D |
| `staff` | `company_id → companies`；被预约、请假、付款审核引用 | R | 查询可用员工/排班；LLM 不新增、修改或删除员工 |
| `grooming_booking` | 同公司 `pet`、`staff`、`payment` | C/R/U | `MAKE_BOOKING` 经价格、疫苗、营业时间、员工冲突检查与确认后 C；`CANCEL_BOOKING` 输入真实宠物名后二次调用才取消；`RESCHEDULE_BOOKING` 同样需宠物名确认并重验时段；不 D |
| `daycare_booking` | 同公司 `pet`、`staff`、`payment` | C/R/U | 与 grooming 相同，另校验接送时间、时长、营业时间与 add-on；不 D |
| `boarding_booking` | 同公司 `pet`、`staff`、`payment`；`room_type` 对应公司 settings 中房型 | C/R/U | 与 grooming 相同，另要求入住/退房两个日期并检查整段房间容量；不 D |
| `payment` | 同公司 `redemption`；由三个 booking table 的 `payment_id` 反向关联 | C/R/U | C 只作为 `create_booking_atomic` 的副作用；R 仅当前客户付款历史；U 只会在原子取消中变 `Cancelled/Refunded`。LLM 不能收款、核销、手改金额或 D |
| `loyaltymember` | `(company_id, customer_id) → customer` | C/R/U | R 查询余额；C 必须先提示权益并得到明确同意，再以 `confirmed=true` 二次调用；U 只由付款/退款/核准兑换 SQL 事务间接完成，LLM 不能手调积分；不 D |
| `coupon` | `company_id → companies`；被 `redemption` 引用 | R | 仅列出未过期且积分足够的奖励；LLM 无 C/U/D |
| `redemption` | 同公司 `loyaltymember`、`coupon`；被 `payment` 引用 | C/R/U | C 仅在已有真实未付款 `payment_id`、真实可用 `coupon_id` 且客户确认后提交 `Pending` 请求；LLM 不能 Approve/Reject；取消预约事务可将关联请求取消或退回已扣积分；不 D |
| `leave` | 同公司申请员工与审核员工均关联 `staff` | — | 员工请假与经理审核只在管理后台，LLM 无工具 |
| `messages` | `sender_id` 是按 `sender_type` 解释的多态字段；回复员工同公司关联 `staff` | — | 对话 LLM 当前没有 message CRUD 工具；后台可记录/回复 enquiry |
| `company_business_hours` | `company_id → companies` | R | 可用时段检查必读；LLM 不改营业时间 |
| `company_closed_dates` | `company_id → companies` | R | 可用日期检查必读；LLM 不改休息日 |
| `company_documents` | `company_id → companies` | — | 文件上传、索引状态与删除由管理后台控制 |
| `chunks_bge_large` | `company_id` 可为空（共享知识）或属于公司；`document_id` 对应来源文件 | R | 仅 `retrieve_policy` 通过受控 RPC 检索当前公司 + 共享 chunks；禁止匿名直接 SELECT，无 C/U/D 工具 |
| `loyalty_points_adjustment` | 同公司 `loyaltymember`、操作者 `accounts` | — | 仅经理后台的人工积分调整 RPC；永久审计，LLM 无工具 |
| `company_chat_key` | `company_id → companies` | — | `/chat` 入口用 SHA-256 digest 解析租户；模型本身不可读写密钥 |
| chunks/状态备份表 | 历史备份 | — | 后端维护用途；RLS + REVOKE 后 anon/authenticated 不可访问 |

## 写动作的统一门槛

1. `company_id` 和 `customer_id` 由已验证的请求/会话注入；模型传入的值会被覆盖，不能借参数切换租户或客户。
2. 未识别场景默认只有 read-only tools。写工具只有在对应场景中才会绑定。
3. 新预约与兑换请求采用两回合确认；取消/改期必须让客户输入目标预约的真实宠物名，普通 “yes” 不足够。
4. 价格、优惠券、积分、疫苗、营业时间、员工冲突和 boarding 容量都由代码/SQL 重验，不信任模型自行计算的值。
5. 付款核销、退款、兑换审批、人工积分调整、员工/账号/公司设置属于管理后台动作，不开放给客户对话 LLM。
6. 对话 LLM 永远不删除业务记录。取消是状态转换，并保留 booking/payment/redemption 审计链。

## Hybrid constraint 覆盖（Prompt + Orchestrator + DB）

这里的规则不能只存在于 system prompt。Prompt 说明业务语义；orchestrator
绑定身份、顺序、证据和确认；tool/SQL 在最终边界再次重验。只要后两层没有
对应门禁，就不算已落实。

| Flow | Prompt 语义 | Orchestrator 硬约束 | Tool / DB 最终约束 |
|---|---|---|---|
| 新客户 / 新宠物 | 收集客户原话，不猜姓名、品种、身高 | 手机号、company/customer scope 由会话覆盖；姓名、宠物名、species、breed、height 必须能在客户消息中找到 | 防重复客户/宠物；身高转 cm 后才算 size；租户/客户关联校验 |
| 普通预约 | 先选真实套餐、日期、时间，再确认；会员与 loyalty 都是可选项，不得阻断预约 | scenario 才开放写工具；pet/name/service 绑定；价格、add-on、staff preference、日期、当前回合 availability 和 exact preview 全部验证；只有客户明确询问或选择 loyalty 时才进入对应分支 | 原子建立 booking + payment；非会员可正常预约；营业时间、疫苗、staff/pet 冲突、价格与房量重验 |
| 照上次预约 | 使用同一宠物、同一服务的最后一次已完成记录 | `pet_id + service_type + Done/Completed` 精确历史；当前目录重新匹配；然后强制 availability；MAKE_BOOKING 不开放重复的 policy/latest 工具 | 历史查询限定当前租户/客户拥有的宠物；写入仍走普通预约全部边界 |
| 取消预约 | 先 preview，下一回合输入宠物名 | 第一次调用即使原消息含宠物名也会清空确认值；只有 prior `preview_turn` 后的新客户消息才接受；候选 booking/service 绑定 | 仅当前客户 active booking；取消、付款/兑换逆转走事务；不 DELETE |
| 改期 | preview 精确目标和变更，再输入宠物名 | 同取消的跨回合门槛；新日期必须来自 resolver；确认签名绑定 booking + 新日期/时间，改变任一字段需重新 preview | active booking、营业时间、冲突、daycare 时长、boarding 两日期/容量重新校验 |
| Loyalty / 兑换 | 只用真实余额、真实 eligible coupon 和真实 payment | customer scope 覆盖；空 eligibility 会清除旧券；coupon/payment ID 绑定；booking 与 redemption 不可同批；exact preview 两回合确认；新 booking 清除旧 loyalty 决定 | 只允许未付款 payment；积分/有效期再验；提交 Pending，审批和实际扣分仅后台事务 |
| Membership | 说明后取得同意；会员登记不创建第二条 customer | `register_loyalty_member` 的 preview payload 与后续独立肯定回复绑定；预约中的会员 side-flow 保留原预约目标；已有 customer 被模型误送到 `create_customer` 时按已验证身份改道到会员工具 | 已是会员时幂等返回；否则只建立同租户 loyalty account；不重复建立 customer |
| Payment 查询 | 只回答真实付款记录 | 只读工具；company/customer scope 由会话覆盖；失败结果不能当证据 | 查询限定当前客户关联 booking/payment；LLM 无收款、退款、mark-paid 工具 |
| Policy / Enquiry | 只用公司上下文或 RAG | company/species/size scope 注入；失败的 RAG/tool call 不算 evidence；明确人工请求由代码写 enquiry | pgvector RPC 限 tenant；浏览器角色不能直接读 chunks；staff enquiry 写后核验 |
| 确认单 | 仅在当前消息明确要求时发送 | BOOKING_DOCUMENT scenario；当前消息 intent gate；非成功结果会被确定性渲染为“未发送”，私有 URL 从模型 evidence 移除 | booking ownership 重验；只有真实 delivery status 为 sent/sent_console 才算成功 |

相应回归测试必须包含“故意让模型传错参数/提前确认/在失败后声称成功”的
adversarial case；正常 happy-path 测试本身不能证明 hybrid constraint 生效。

## 仍然不能用普通外键表达的关联

- `messages.sender_id` 是多态字段：目标表取决于 `sender_type`，必须由应用层验证。
- `boarding_booking.room_type` 对应 `companies.settings_json.rooms`，目前不是独立 room table，因此由容量 RPC 验证。
- `chunks_bge_large.document_id` 是兼容旧向量数据的 text 字段，而 `company_documents.document_id` 是 UUID；删除/替换通过同时带 `company_id + document_id` 的 RPC 限定。
