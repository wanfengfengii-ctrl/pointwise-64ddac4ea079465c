# 目录来源刷新与持续采集

本文件是本次新增能力的公开契约。README.md、RUNBOOK.md 中的完整快照接口、规范值、成果与部门计算继续适用。已有完整快照导入器不是本题待修复的故障实现。这里新增的是串行来源事件采集，不包含网络、BER、认证、跨来源融合、并行采集者或自动重连。允许全量复制、全量重算和内存工作集合，没有增量性能或正文内存指标。

## 调用与所有权

实现 `directory.source.sync.SyncCollector(db, source_id)`。db 为已打开的 `directory.archive.schema.ArchiveDatabase`，例如 `api.runtime.database`；source_id 沿用 V1 的非空不透明字符串规则。一个库只运行一个固定搜索范围、同一个 source_id，V1 导入仅用作开始采集前的可选种子；不要求和其他导入或采集并发。collector 不关闭调用方拥有的 db。

公开方法：
- `accept(event) -> None`：串行接纳下述 Event。
- `read() -> tuple[DirectoryEntry, ...]`：返回已发布完整目录，按规范 DN、UUID 排序；无成果时返回空元组。返回不可变规范值，输入可变属性随后被调用方修改不得改变已接纳内容。
- `resume_cookie() -> bytes | None`：返回本来源最后提交的 cookie；尚无检查点时为 None，b"" 是有效 cookie。
- `disconnect() -> None`：幂等结束连接，丢弃未完成刷新及失败会话；随后可再次 begin。关闭 db 后原 V1 存储关闭规则继续适用。

构造或重开后处于未连接状态，每次连接必须先 begin。调用方用 `ScriptedSource.open(collector.resume_cookie())` 取得事件，逐个 accept；来源正常停止或抛异常都由调用方在 finally 调用 disconnect，来源异常仍原样向上传播。无需新增自动驱动接口。正常停止本身不等于 refresh_done。

V1 当前成果在首次刷新前用作旧目录；空库视为空目录，但不得在构造或 begin 时制造发布。新增成果须通过原 DirectoryAPI 的绑定、DN 查询、部门查询、确定性导出和历史接口读取；不得只存在于 collector 私有内存。V1 固定成果不变，原按 DN 的历史差异口径不变。新增内部来源标识及存储结构由实现决定，不规定生成的成果 ID 或来源标识文本。

## 事件输入

使用 `directory.source.events` 提供的 Event 和工厂函数：
`begin(mode)`、`entry(uuid,dn,attributes,department_path=None)`、
`present(uuid)`、`delete(uuid)`、`checkpoint(cookie)`、
`refresh_done(cookie)`。Event 仅是数据，来源夹具没有阶段裁决或持久化实现。

mode 只能为 "present" 或 "delete"。UUID 沿用 V1 的字符串解析和规范化规则。entry 提供完整属性，必须含唯一 objectUUID，规范后须和事件 UUID 相同；DN、属性和部门路径沿用 RUNBOOK.md。entry 是完整替换，包括部门路径；None 表示未分配，不表示保留旧部门。present、delete 只携带 UUID，checkpoint、refresh_done 的 cookie 必须为 bytes，其他事件不携带 cookie。事件不相关字段须为 None；未知 kind、非 Event 或非法字段均是格式错误。

## 刷新与对象身份

begin 从此前已发布目录开始刷新，刷新中的 entry 按 UUID 替换该对象完整内容，也可新增 UUID；重复的 entry 按输入顺序取最后内容，不把相同 UUID 当重复行拒绝。present 只证明该 UUID 存在，保留它在本轮此前的最新内容及部门，未知或已删除 UUID 的 present 报错。present 仅允许在 present 模式的刷新阶段出现。delete 按 UUID 删除，未知 UUID 无效果；删除后再次 entry 可重新定义，delete 之后不能因先前的存在标记复活对象。

present 模式结束时仅保留本轮经 entry 或 present 证明存在、且随后未被删除的对象；delete 模式保留未出现且未被 delete 移除的旧对象。两模式均可接纳 entry 和 delete。

UUID 为对象身份，DN 为可变名称；刷新期间允许暂时重名及名称交换，仅在 refresh_done 时核对最终目录的规范 DN 唯一性。entry 的单条字段仍须立即校验。refresh_done(cookie) 完成刷新并转入持续阶段。刷新期间 read 和独立 DirectoryAPI 继续读取旧完整成果；refresh_done 成功后目录、部门投影与该 cookie 一起生效。

## 持续阶段、检查点与失败

持续阶段只接受 entry、delete、checkpoint。每个合法 entry 或 delete 返回前，其完整结果须已持久发布并能被独立读取者看见；未知删除或等值替换可复用成果，不规定历史条数。每个 entry 后必须满足规范 DN 唯一性。此阶段不允许 present 或第二次 refresh_done；重新 begin 前须 disconnect。

刷新阶段 checkpoint 不能发布工作集，也不能改变 resume_cookie；最终使用 refresh_done 自带的 cookie。刷新结束与持续阶段 checkpoint 必须在同一 SQLite 提交中绑定完整成果及对应 cookie；提交失败保留调用前的已发布目录和 cookie，不得留下开放事务。checkpoint 无需创建内容相同的新成果。持续 entry/delete 不携带新 cookie，因此 resume_cookie 可落后于最近变更；它只能在成功的检查点或刷新结束后推进，不能先于其对应目录持久化。重开使用已提交目录和 cookie，新连接仍先 begin；不要求恢复半轮刷新或提供跨重放事件的额外去重协议。

格式、阶段、未知 present、UUID 不匹配或最终 DN 冲突须抛 ValueError 或其子类，拒绝当前事件且不改变已发布状态。存储错误保留 sqlite3 异常类型。任一 accept 失败后只允许 read、resume_cookie 及 disconnect；再次 accept 抛 ValueError，直至 disconnect。未失败的旧事件不撤回；未完成刷新在 disconnect 或重开后全部丢弃。正常 disconnect 不清除已发布目录或 cookie。

## 手算样例与真实核验

`seed_records()` 每次返回旧 U:CN=a、x=["1"]、部门 A，以及旧 V:CN=b、y=["3"]、部门 B。两 UUID 常量及下列输入均由 events.py 提供；各样例从独立的种子库开始。
- `sample_scripts()["presence"]`：结束只剩 U，x 仍为 ["1"]，部门 A，cookie 为 b"present-done"，中途 b"ignored" 不可见。
- `sample_scripts()["delete-mode"]`：结束 U 的 x 为 ["2"]，V 与部门 B 保留，cookie 为 b"delete-done"。
- `sample_scripts()["swap"]`：结束 U:cn=b、x=["2"]、部门 B，V:cn=a、y=["4"]、部门 A，cookie 为 b"swap-done"；两条 entry 之间仍读到旧目录。
- `sample_scripts()["incomplete"]`：来源停止后 disconnect、关闭重开，种子目录仍完整，cookie 仍为此前已提交值。
- `sample_scripts()["bad-final-name"]`：末事件拒绝，种子目录及原 cookie 不变；disconnect 后可重新刷新。
持续阶段还须核对显式删除、未知删除、等值重投、checkpoint 后重开、阶段外 present、未知 present、非法字段、空目录与空 cookie。 These are not separate capabilities.

`reject_commit(connection, ordinal=1)` 是提交失败夹具，使用 SQLite authorizer 阻止指定次真实 COMMIT，放行 ROLLBACK；无其他 authorizer 时在空闲连接安装，退出后移除，不代替被测代码回滚。在 refresh_done 和持续 checkpoint 处分别使用夹具，核对失败后目录、成员、历史及 cookie 保持调用前状态，再关闭重开核账。可以观察不同提交序号以发现把一次共同提交拆成两次的实现；不要求新增故障注入生产 API，也不要求杀进程证据。

现有 `.pointwise-runtime.json` 的格式见其内容：保留 version=1、kind=cli、setup 为 argv 数组列表、verify/demo 为各自 argv 数组。实现后更新 verify，使它保留完整原 unittest discover 并执行新增联合核验，业务失败须非零退出；demo 保留原真实导入展示并增加属性保留、名称交换及断开后从已提交 cookie 重新开源的真实结果。可用现有脚本组织入口，不新增独立产品。补简短状态归属及共同提交不变量说明，可放在本文件。

原回归命令为 `python3.11 -B -m unittest discover -s tests -v`，原展示为 `python3.11 -B -m tools.verify_demo`；新增纯夹具自检为 `python3.11 -B -m directory.source.events --self-check`。夹具自检只核对 V1 回滚与样例数据，不表示 SyncCollector 已实现或通过。

## 实现状态与共同提交不变量

- 状态归属：串行裁决、UUID 合并、present/delete 语义、阶段机与失败毒化归 `directory/source/sync.py`；不透明 cookie、内部来源标签、发布序号及重开装载归 `directory-sync` 表（`directory/source/sync_store.py`，`sync_sources`）；部门投影、规范条目、固定读取和按 DN 历史仍由 V1 模块计算与持有。联合核验归 `tests/test_sync.py` 与 `tools/run_verify.py`，演示扩展归 `tools/verify_demo.py`。
- 共同提交不变量：`refresh_done` 与持续阶段 `checkpoint` 各自只开启一个 SQLite 事务，完整成果（snapshots/entries/departments/members、当前指针）与该 cookie 在同一次 COMMIT 生效；内容无变化的 checkpoint 仅在同一事务更新 `sync_sources.cookie`，不新建成果。任一回滚后无开放事务，目录、成员、历史与 cookie 回到调用前状态，可经 `reject_commit` 与关闭重开核验。
- 边界内不实现：网络协议、BER/认证、并行采集者、跨来源融合、自动重连与故障注入生产 API；断开后的半轮刷新不恢复，重开只基于已提交目录和 cookie。

