# 输入、发布与复核口径

## 完整输入

每项是仅含 dn、attributes 和可选 department_path 的映射，前两项必需。source_id 是非空、区分大小写、不修剪的不透明字符串。records 仅遍历一次，其正常 StopIteration 承诺导出结束，其他来源异常原对象向上传播。失败不登记来源。没有外部数量清单，不能检测发生在完整记录边界的静默删尾。

JSONL 读取二进制逐行流，严格 UTF-8；每行须为 JSON 对象。重复 JSON 键、空行、NaN/Infinity、未知顶层字段、残缺 JSON 或 UTF-8 报 SourceFormatError 并带行号。完整末行可没有 LF。错误不会发布已读前缀。

属性名语法为 ASCII [A-Za-z][A-Za-z0-9-]*，规范为小写；同一映射规范后键名相同必须拒绝。值必须是字符串列表，普通值 NFC 后按码点排序去重，保留大小写和所有有效空格，未知合法属性完整保留。空列表与缺失属性不同。objectUUID 原列表必须恰有一项，以 uuid.UUID 解析后保存规范小写带连字符值。无效 Unicode 标量拒绝。

DN 是逗号分隔的单属性 RDN，每个为 attribute=value。属性语法同上并转小写，非空值 NFC、保留大小写；逗号、等号、加号、反斜杠、双引号、分号和尖括号在值内须反斜杠转义，输出统一转义。值首尾空白、起始 #、控制字符、十六进制转义、多属性 RDN 和其他名字语法不受支持，报 InvalidName。这是明确受限的名字口径，不是完整 LDAP 等价规则。例 CN=a\,b,OU=R\\D 规范为 cn=a\,b,ou=R\\D。

部门为非空字符串段列表（程序查询也接纳元组）。每段 NFC 并修剪首尾空白，保留内部空格、大小写及段内斜杠；空路径、空白段、非字符串段和控制字符拒绝。输入缺失或 null 为未分配，仍可按 DN 查询和全量导出，不属于任何部门。查询必须提供非空路径。

## 计算和交付

Candidate 包含不可变条目元组、条目数、规范内容摘要。单份成果内 DN、UUID 分别唯一，跨成果允许重复 UUID。投影为所有非空路径的前缀节点。直接集合取完整路径相同的 DN；递归集合为自身与子节点的并集，节点内不重复。全目录人数不能由各部门递归人数相加得到。

样例 complete.jsonl 有 5 人、5 个部门节点。A 递归为 CN=1、CN=2、CN=5；A/B 递归为 CN=1、CN=5，直接为 CN=1；A 直接为空；AB 为 CN=3；CN=4 未分配。A/BC 不属于 A/B。第二份样例移除 CN=2、修改 CN=1、将 CN=4 同 UUID 换名为 CN=renamed、添加 CN=6；旧成果不变。

规范输出字段为 dn、attributes、department_path（未分配输出 null）。JSON 使用 ensure_ascii=False、sort_keys=True、紧凑逗号冒号，每记录一个 LF；记录按规范 DN、UUID 排列。候选摘要及导出摘要均为这些 UTF-8 字节的 SHA-256，空目录是零字节摘要。无时间、随机数或来源元数据参与摘要。二进制 sink.write 必须返回实际写入的正整数；短写会补写，零进展/非法计数报 OSError，接收端异常原样传播。失败输出由调用方丢弃，归档不变。

## 原子性和恢复

发布复核完整证据、候选和投影，事务内裁决来源后保存所有数据。相同 source_id 同规范内容返回原 ID，摘要相同后仍比较完整内容，不改原来源证据、不回退当前；异内容报 SourceConflict；不同 source_id 可以保存相同内容。

SQLite 成果、条目、部门、成员、来源映射和单行当前指针共同提交，写入异常全部回滚。已发布成果不提供编辑接口。重复初始化仅创建缺失 V1 结构，保留用户成果与当前指针；未知 user_version 报 UnsupportedSchema。每次打开启用外键。API 是同步单调用方入口，不提供跨线程共享连接接口。close 幂等，关闭后数据库操作报 StoreClosed。

明确成果不存在报 UnknownSnapshot，不回退当前；空库绑定当前报 NoCurrentSnapshot。查询先确认成果存在，再检查 DN 或部门；合法未命中 DN 返回 None，不存在部门返回空列表。InvalidSource、SourceFormatError 位于 directory.source.model；InvalidAttributes 位于 quality.attributes；InvalidName、InvalidDepartmentPath 位于 quality.names；InvalidUUID、DuplicateDN、DuplicateUUID 位于 quality.identity；SourceConflict、UnknownSnapshot、NoCurrentSnapshot、InvalidPublication 位于 archive.snapshots；存储异常保留 sqlite3 类型。

## 回归核账

tests/test_import.py 覆盖完整属性、单遍来源、证据、空目录、完整性门及逐节点投影；test_quality.py 覆盖字段、转义和规范后双身份冲突；test_membership.py 覆盖分段边界、直接/递归名单、隐式祖先和无部门对象。test_archive.py 通过真实 SQLite TEMP TRIGGER 分别在成员写入及指针切换时中止，关闭重开核对目录、投影、来源和历史数，并以同一来源重试；同时核验等值和冲突重投。test_delivery.py 核对固定读取、独立手写规范记录字节、短写、输出异常；test_review.py 核对按 DN 的完整行变化及反复初始化重开。

directory/source/events.py 提供纯事件值及 ScriptedSource 离线端口：open(cookie) 记录传入 cookie，按脚本交付事件，支持指定位置正常停止或抛出原异常。该端口不执行采集裁决或持久化，V1 导入接口接收完整记录。
