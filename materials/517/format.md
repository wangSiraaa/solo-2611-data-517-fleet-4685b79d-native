# 受限工作簿包格式

这是本题定义的ZIP/XML工作簿子集；不是完整OOXML文件，也不要求Excel打开。所有字符串为UTF-8 XML属性，XML解析时禁止外部实体。ZIP条目名是相对POSIX路径，无重复项或路径穿越。未知部件是opaque，刷新不得改写其字节。

`_rels/package.xml`根为`relationships`，`rel`项有唯一`id`、`kind`和`target`。kind为cache/pivot/slicer；target是相对包根路径。cache部件根`cache`有`id`和`source_sha256`；`fields/field`按缓存字段位置排列，每项`name`唯一，子元素`sharedItems`包含m(缺失)、b(v=0/1)、n(v=有限十进制)、s(v=文本)。`records/r`的i属性是以空格分隔的非负成员索引，数量等于字段数。每个field内等价typed身份只能出现一次。

pivot部件根`pivot`属性为id、cache_rel、row、column、measure、aggregate。cache_rel是package关系id；row/column/measure都是缓存的零起字段位置，column可以为空。aggregate只支持sum/count。`filters/filter`有field及selected(空格分隔的成员索引)，没有filter表示不筛选；显式空selected是零匹配。输出`results/cell`属性为row_key/column_key/value，轴key用JSON编码的typed成员数组，value用十进制字符串；空组合不产生cell。

slicer部件根`slicer`属性为id、cache_rel、field、mode；mode为all/explicit。`selection/item`存kind(m/b/n/s)、v和missing(0/1)，不存缓存索引。`targets/pivot`用rel属性引用目标pivot关系id。显式空selection是零匹配。mode=all不因新成员出现而退化为某次固定成员清单。

`source.json`为columns(唯一字段名数组)和rows(等长JSON数组)；仅本题列集必须保留，字段可换序，不要求增删字段或重命名。刷新后pivot所有field位置均重绑。source_sha256是该source原始文件字节摘要。

所有有关cache的变更完成后，输出新ZIP再切换；失败不覆盖旧ZIP。`workbook.zip`为完整小样例；source_fixture.make_package只用于作者的一字段边界探针，不是完整目标包。
