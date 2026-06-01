# ICF Amendment History Generator

本项目用于从带 Word 修订痕迹的 ICF/知情同意书 `.docx` 中提取修订内容，并在标准模板上生成《文件修订记录》交付文档。

项目当前采用验收文件金标准化设计：

1. 标准模板作为固定骨架
2. `draft` 只产出草稿
3. `release` 必须以源文档推断结果生成，不回放验收文件行
4. 验收文件只作为离线金标准；正式交付必须同时通过模板完整性和严格交付校验

## 当前能力

1. 读取正文、表格、页眉、页脚和运行级样式
2. 提取插入、删除、替换等 Word 修订内容
3. 自动归类主题、章节/页码和更改原因
4. 通过 OOXML 原生定点写入标准模板，避免重建 docx 骨架
5. 生成严格交付报告、交付差异报告和复盘文档
6. 支持文本块和表格 XML 块写入修订记录单元格
7. 提供本地 Web 入口和 skill 脚本入口

## 技术栈

1. Python 3.12+
2. FastAPI
3. Jinja2
4. `python-docx`
5. `lxml`
6. `pytest`

依赖声明位于 [pyproject.toml](/C:/Users/SmithHwo/Desktop/ICF-Project/pyproject.toml:1)。

## 安装

建议在项目根目录创建虚拟环境后安装本项目依赖：

```bash
pip install -e .
```

若只需要最小运行环境，也可直接安装：

```bash
pip install fastapi jinja2 lxml python-docx pytest
```

## 运行方式

### 1. Web 入口

```bash
uvicorn src.icf_parser.web_app:app --host 127.0.0.1 --port 8000
```

启动后访问：

```text
http://127.0.0.1:8000
```

Web 页支持：

1. 上传本地源文件
2. 指定模板路径
3. 指定验收文件路径
4. 选择 `draft` 或 `release`
5. 查看执行日志、交付状态和结果下载

### 2. Skill 入口

`draft` 生成：

```bash
python skills/icf-amendment-history/scripts/generate.py --mode draft --source 训练集/训练文件/training-1.docx --output output/R29-training1-draft.docx
```

`release` 生成：

```bash
python skills/icf-amendment-history/scripts/generate.py --mode release --source 训练集/训练文件/training-1.docx --acceptance 训练集/训练文件验收文件/training-1-验收.docx --output output/R29-training1-release.docx
```

校验：

```bash
python skills/icf-amendment-history/scripts/validate.py --mode release --template TG-ICF模板/标准模板.docx --output output/R29-training1-release.docx --acceptance 训练集/训练文件验收文件/training-1-验收.docx
```

比对：

```bash
python skills/icf-amendment-history/scripts/compare.py --generated output/R29-training1-release.docx --acceptance 训练集/训练文件验收文件/training-1-验收.docx --output output/R29-training1-release.acceptance_diff.json
```

## 目录结构

```text
src/icf_parser/                         确定性业务内核
skills/icf-amendment-history/          Codex skill
templates/                             Web 模板
tests/                                 自动化测试
TG-ICF模板/                            标准模板
训练集/                                 训练文件与验收文件
测试集/                                 测试文件与验收文件
output/                                各轮次交付物和复盘结果
```

## 关键文档

1. [agent.md](/C:/Users/SmithHwo/Desktop/ICF-Project/agent.md:1)
2. [Requirement.md](/C:/Users/SmithHwo/Desktop/ICF-Project/Requirement.md:1)
3. [Design.md](/C:/Users/SmithHwo/Desktop/ICF-Project/Design.md:1)
4. [Task.md](/C:/Users/SmithHwo/Desktop/ICF-Project/Task.md:1)

## 测试与验收

当前仓库回归命令：

```bash
pytest -q
```

正式交付定义：

1. `release` 模式
2. 模板完整性校验通过
3. 严格交付校验通过
4. `delivery_status == release_passed`

若源文档推断结果与验收文件不一致，输出仍会生成，但状态必须是 `release_blocked`，并通过 `blocking_issues` 与 `*.acceptance_diff.json` 标明差异。

`alignment score` 仅保留为诊断信息，不再代表正式交付 verdict。

## 交付物

每次正式运行至少产出：

1. `*.docx`
2. `*.report.json`
3. `*.acceptance_diff.json`
4. `output/progress/*.md`

skill 安装包输出位于：

```text
dist/icf-amendment-history.skill
```
