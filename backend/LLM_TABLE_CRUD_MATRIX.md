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

## 仍然不能用普通外键表达的关联

- `messages.sender_id` 是多态字段：目标表取决于 `sender_type`，必须由应用层验证。
- `boarding_booking.room_type` 对应 `companies.settings_json.rooms`，目前不是独立 room table，因此由容量 RPC 验证。
- `chunks_bge_large.document_id` 是兼容旧向量数据的 text 字段，而 `company_documents.document_id` 是 UUID；删除/替换通过同时带 `company_id + document_id` 的 RPC 限定。
