# 青岚目录快照

Python 3.11 标准库与 SQLite 实现的离线目录采集系统，供青岚研究所接入员导入完整目录、复核员核对历史名单、下游程序领取固定成果的部门成员。无需安装依赖。

在仓库根目录运行：

```sh
python3.11 -B -m unittest discover -s tests -v
python3.11 -B -m tools.verify_demo
```

运行清单为 .pointwise-runtime.json。demo 在自身临时目录中真实导入两份样例，计算部门名单、导出旧成果、比较差异并关闭重开核账；不修改样例。测试只写测试临时目录或内存数据库。

## 一天的工作

1. 接入员取得上游完整导出，使用原样 source_id 建立一次来源会话。
2. 单遍读取 records 或二进制 JSONL，正常 EOF 才得到完整证据。
3. 规范 DN、UUID、完整多值属性和部门段，在本批内分别核对 DN 与 UUID 唯一。
4. 封存有序候选并计算 SHA-256；空导出形成合法空成果。
5. 从部门段建立祖先树，计算直接和递归名单，检查引用及闭包。
6. 把目录、投影、来源证据和当前指针在一次事务中发布；失败仍提供旧成果。
7. 下游先绑定明确成果，再按 DN、部门查询或导出 JSONL。
8. 复核员指定两份成果，按 DN 比较完整行并重开核对历史。

来源游标归 directory/source；规范候选归 directory/quality；树及闭包归 directory/membership；持久成果归 directory/archive；固定读取归 directory/delivery；directory/workflow 编排入口及关闭。具体数据与错误契约见 RUNBOOK.md。

## 公共 API

```python
from directory.workflow.api import DirectoryAPI

with DirectoryAPI("directory.db") as api:
    with open("examples/complete.jsonl", "rb") as source:
        result = api.import_jsonl("2026-11-17/export-1", source)
    snapshot_id = api.bind_snapshot()
    people = api.members(snapshot_id, ["A", "B"], recursive=True)
    person = api.get_entry(snapshot_id, "CN=1,OU=People")
    with open("department-source.jsonl", "wb") as sink:
        exported = api.export_snapshot(snapshot_id, sink)
    evidence = api.source_info(snapshot_id)
    history = api.history()
```

import_snapshot(source_id, records) 接纳单遍完整记录迭代器；import_jsonl(source_id, binary_stream) 为输入适配器。调用方拥有输入和输出流，API 不关闭它们。ImportResult 有 snapshot_id、record_count、department_count；ExportResult 有 record_count、digest。

get_entry(snapshot_id,dn) 返回不可变 DirectoryEntry 或 None；members(snapshot_id,path,recursive=True) 返回按 DN、UUID 排列的完整条目列表。条目 attributes 是 (小写属性名, 值元组) 的有序元组，department_path 为元组或 None。export_snapshot(snapshot_id,sink) 输出全成果，不是仅部门名单。diff_snapshots(before,after) 返回按 DN 排序的 DiffItem(kind,dn,before,after)，kind 为 added、removed 或 modified。两个成果须先全部存在。source_info 返回不可变 SnapshotInfo，history 按成果 ID 排列；bind_snapshot(None) 一次绑定当前成果。

完整快照内 UUID 唯一；历史差异使用 DN 行身份。同 UUID 更换 DN 是删除加新增。此版本不连接远端目录，不实施身份权限管理。
