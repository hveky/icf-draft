# Design

## 1. 设计目标

系统采用验收文件金标准化设计：  
以标准模板为固定骨架，把带修订痕迹的 ICF 文档转换成交付模型，再以验收文件作为离线金标准执行严格交付校验。

## 2. 总体架构

系统分为两层：

1. 确定性业务内核：`src/icf_parser/`
2. skill 与产品壳层：
   - `skills/icf-amendment-history/`
   - `src/icf_parser/web_app.py`

整体链路如下：

`source docx -> revision facts -> delivery rows -> OOXML template render -> integrity validation -> strict delivery validation -> diagnostic alignment -> report/progress artifacts`

## 3. 核心模块

### 3.1 `revision_extractor`

职责：

1. 直接读取 Word XML 和文档结构
2. 抽取插入、删除、替换等修订段
3. 保留页脚、页眉和表格来源信息
4. 为复杂表格输出稳定的表格路径

### 3.2 `rule_engine`

职责：

1. 将底层修订事实归并成业务提案行
2. 输出版本首行、中心信息合并、表格修订吸收等稳定规则结果
3. 为后续交付模型提供可解释的确定性输入

### 3.3 `delivery_model`

职责：

1. `RevisionFact`：承载源文档中的客观修订事实
2. `DeliveryRow`：承载正式交付需要的业务字段、关键样式标记和富内容块
3. `ContentBlock`：承载文本块或 `table_xml` 表格块
4. 明确区分草稿提案与正式交付模型

### 3.4 `template_writer`

职责：

1. 在模板上定位封面信息表和修订记录表
2. 直接编辑模板复制件的 `word/document.xml`
3. 只写题头版本值和修订记录数据行
4. 文本块按 run 写入；表格块按 OOXML 嵌入单元格
5. 非 `word/document.xml` 的 package part 必须字节级保持不变

### 3.5 `acceptance`

职责：

1. 从生成结果和验收文件中抽取 `DeliveryRow`
2. 执行严格交付校验：
   - 行数
   - 顺序
   - 字段
   - 关键样式
   - 缺失/额外行
3. 保留历史结构化 alignment 作为诊断信息，而不是正式 verdict

### 3.6 `service`

职责：

1. 提供统一服务接口：
   - `generate_amendment_history_safe(...)`
   - `validate_template_integrity(...)`
   - `validate_generated_output(...)`
2. 统一生成 `.docx`、`report.json`、`acceptance_diff.json` 和 `progress.md`
3. 统一维护三种状态：
   - `draft_generated`
   - `release_passed`
   - `release_blocked`

### 3.7 `web_app` / `skill`

职责：

1. 提供 `draft/release` 模式入口
2. 展示或输出：
   - `delivery_status`
   - `delivery_passed`
   - `draft_only`
   - `blocking_issues`
   - `diagnostic_notes`
3. 明确禁止把 `draft` 输出包装成正式交付

## 4. 模板保护设计

模板保护是系统硬约束。

原则：

1. 只支持标准模板
2. 先复制模板，再写复制件
3. 只允许修改白名单区域
4. 通过完整性校验确认模板骨架未被误伤

白名单修改区域包括：

1. 首页原版本号/日期
2. 首页修订后版本号/日期
3. 修订记录表的数据行

## 5. 数据流设计

### 5.1 草稿路径

`source -> RevisionFact -> DeliveryRow(draft) -> template render -> integrity`

用途：

1. 本地预览
2. 规则调试
3. 非正式输出

### 5.2 正式交付路径

`source -> RevisionFact -> DeliveryRow(proposed) -> OOXML template render -> strict delivery validation -> diagnostic alignment`

约束：

1. 必须存在验收文件
2. `release` 不允许回放验收文件行作为输出内容
3. 严格交付校验和模板完整性同时通过才可宣称正式交付

## 6. 验收门禁设计

### 6.1 正式交付门禁

正式 verdict 只看严格交付校验：

1. 模板完整性是否通过
2. 规范化交付行数是否一致
3. 行顺序是否一致
4. 关键字段是否一致
5. 关键样式是否一致
6. 是否存在 `missing_rows`
7. 是否存在 `unexpected_rows`

### 6.2 诊断对齐

以下项降级为诊断信息：

1. `topic_alignment`
2. `section_page_alignment`
3. `reason_alignment`
4. `table_trace_alignment`
5. `style_alignment`

这些分数只用于根因定位，不再直接控制正式交付 verdict。

## 7. 当前设计边界

1. 结果仍以确定性规则链路驱动，不使用黑盒生成替代正式交付主路径
2. 标准模板是唯一支持模板
3. 无验收文件时只能运行 `draft`
4. 项目当前优先解决“达到验收文件可交付级一致性”，不把多模板适配作为默认任务
