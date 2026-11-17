# 透视缓存刷新与切片器选择身份保留

The supplied starting materials are in `materials/517`. See that directory README for the ordinary entry point. Python 3.11; additional task requirements are in the separately supplied prompt.

## 交付物

- `pivotpack/` — 交付模块（仅标准库）：
  - `Cache`：`from_part` / `refresh` / `read_records` / `to_part`，可脱离 Workbook 独立读取，解析即校验字典身份与记录完整性。
  - `Workbook`：`open` / `refresh_cache` / `read_pivot` / `set_selection`（另有 `slicer_state`、`get_cache` 等），负责关系解析、字段位置迁移、选择状态与报表输出；刷新与改选都是原子写切换。
- `tests/` — `python3 -m unittest discover`（44 个用例）：演示场景、混合类型 `"1"/1/true`、成员消失重现、空选择/全选、空数据、不同轴共享刷新、列轴、filter 迁移、双缓存隔离、非法包、失败后的旧包保持、高精度精确求和等。
- `scripts/demo_refresh.py` — 演示与核验生成器：`python3 scripts/demo_refresh.py`。
- `deliverables/` — 演示产物：`workbook.refreshed.zip`（刷新后的新包）、`unpacked/`（解包部件）、`VERIFICATION.txt`（关系与记录核验、独立汇总对照、新进程重读）。
- `NOTES.md` — 字典排序与报表重算方式的选择，及其对身份保持、包体大小、重写范围的影响。
