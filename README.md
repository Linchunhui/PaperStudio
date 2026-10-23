# PaperStudio

个人论文研究工作台的第一版可运行实现。重点先打通 **真实 PDF → 左右对照 → 划词批注 → 当前项目笔记 → AI 读后总结**。

这是本地单用户 MVP，不是之前的静态交互 Demo。PDF、笔记、项目和任务保存在本机 SQLite 与文件目录中。默认仅监听 `127.0.0.1`。

## 快速启动

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/Linchunhui/PaperStudio.git
cd PaperStudio
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python run.py
```

打开 <http://127.0.0.1:8000>。首次启动包含一份自行编写的双语 PDF 验收样本，明确标为非论文；可直接试划词、批注、翻页和放大。传 `--no-demo` 可从空库开始。Windows 使用 `.venv\Scripts\python.exe`。

不需要模型也能上传 PDF、阅读、添加/编辑笔记、按项目管理并导出 Markdown。原文 PDF 文件始终保留；自编示例也是实际 PDF 文件，不是用 HTML 伪造的论文。

## 这一版已实现

- **独立阅读页面**：`/read/{paper_id}?project={project_id}`，不是弹窗。
- **三列布局**：左原文 PDF、中间译文 PDF、右侧 AI 与笔记；页码、缩放与滚动同步，滚动同步可关闭。
- **矢量阅读**：用 MuPDF 直接把原 PDF 页面渲染为 SVG，保留字形、公式和线条的原始矢量信息。译文 PDF 采用同一条渲染链路；原 PDF 中已有的扫描图/照片仍是位图，不会声称把它们变成矢量。
- **划词/句子批注**：可在原文或译文上选择文字，记录引用、个人笔记、颜色、页码和选区位置；刷新后恢复，可跳回选区、编辑、删除和导出。
- **项目隔离**：多级目录，同篇 PDF 通过 SHA256 去重后关联多个项目，每个项目有独立笔记、阅读状态和阅读页码。
- **PDF 版本绑定**：批注绑定原文/译文 SHA256。替换译文后保留旧笔记并标记旧版本，避免高亮落到新的错误位置。后台翻译提交时检查入队版本，遇到人工替换则保留人工版并报告冲突。
- **Skill 驱动 AI**：导读、摘要/粗读、选区摘要、读后笔记总结；服务端加载项目内 Skill 与 references，冻结输入与 Skill 版本。
- **真实异步任务**：SQLite 持久队列和单 worker，状态查询、去重、取消和重试。服务重启后正在执行的任务标为中断，需手动重试，不假装已恢复外部模型调用。
- **来源目录**：完整保留 144 条登记、五类分组和 P0/P1/P2。当前可浏览和筛选，尚未接通每日自动采集。

## 配置模型

支持 OpenAI 兼容的 `/chat/completions` 服务，包括本地 vLLM/SGLang 等服务。按部署的开源模型实际名称设置：

```bash
export AI_BASE_URL=http://127.0.0.1:8001/v1
export AI_MODEL=your-open-model
# 远程服务需要；本机 loopback 服务允许留空
export AI_API_KEY=your-server-side-key
.venv/bin/python run.py
```

也可复制 `.env.example`，修改后在终端运行 `set -a; source .env; set +a`。应用不会自动读取 `.env`。密钥只从服务端环境读取，不进入浏览器、任务快照或 Git。

在右侧选择导读、摘要模式或已保存批注的选区摘要。“读完并总结笔记”会标记当前项目已读，并基于**当前项目笔记快照**请求总结；笔记总结不冒充全文总结。无模型配置时明确显示未配置，笔记仍可保存/导出。

全文导读/粗读读取每一页可提取的文字，保留页码，并提示空文本页与图表未做视觉核验。本版没有视觉模型审图；不会声称已核验原图。超过 `AI_MAX_INPUT_CHARS` 时在模型调用前失败，不静默截断成“全文摘要”。扫描 PDF 需要先 OCR。

可选参数：`AI_TIMEOUT_SECONDS=180`、`AI_MAX_INPUT_CHARS=100000`、`AI_MAX_OUTPUT_TOKENS=8192`。输出达到 token 上限会报告截断，不保存为完整成功产物。

## 左右翻译

有两种真实入口：

1. **上传已有单语译文 PDF**：保持与原文相同页数，直接在中间栏矢量显示。不要上传左右合并版。首版按页一一对应，尚不支持重排后的跨页映射。
2. **可选 BabelDOC 后台翻译**：单独安装并配置 `BABELDOC_BIN` 指向其命令。应用传入原 PDF 和服务端模型配置，生成单语译文 PDF，再转换成 SVG 逐页展示。未安装或未配置时不会伪造译文。

```bash
# 在独立环境中安装你已验证的 BabelDOC 版本，再指向该命令：
export BABELDOC_BIN=/absolute/path/to/babeldoc
export BABELDOC_TIMEOUT_SECONDS=1800
```

适配器使用官方 CLI 的 TOML 配置与 `--no-dual`；BabelDOC 可能另需下载字体和版面模型。此次未配置真实模型、未运行实际翻译；安装版本与翻译质量需要用真实论文验收。现有译文上传路径可立即使用。

## 项目内角色

| Skill | 职责 |
| --- | --- |
| `skills/paper-guide` | 从问题、前作缺口到方法机制与证据，提供阅读路线 |
| `skills/paper-brief` | 用户提供规范的摘要、卡片粗读、连贯总结与 all 模式，附完整证据/精简/图表规则 |
| `skills/paper-translate` | 译文术语、公式、版式和来源版本约束 |
| `skills/reading-notes-summary` | 整理当前项目笔记，区分原文事实、个人理解、疑问和下一步 |

修改这些 `SKILL.md` 或 `references/*.md` 后，新任务自动使用新版本；旧任务保留快照。这些是仓库内应用角色，不会自动安装到个人全局 Skills。首版 UI 将粗读模式输出为 Markdown，尚未实现自包含 HTML、原图抽取/嵌入和公式视觉核验；规范保留供后续扩展。

## 开发与数据

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

- `app/core.py` / `core_routes.py`：项目、论文、批注与导出。
- `app/pdf.py`：原 PDF 矢量渲染和文字坐标。
- `app/ai.py` / `jobs.py`：模型、Skill、BabelDOC 与持久任务。
- `web/`：无构建依赖的原生 JavaScript 前端。
- `config/sources.json`：144 条来源原始登记。
- `data/`：SQLite、原 PDF、译文和缓存；已被 Git 忽略。

备份时停止服务后复制整个 `data/`；不要只复制数据库而遗漏文件。可通过 `PAPERSTUDIO_DATA_DIR` 修改路径。测试使用临时目录，不改真实阅读数据。

## 验证

当前 27 项自动化测试通过，覆盖 PDF/SVG、项目隔离、批注版本、持久队列、取消/重试、翻译并发冲突、Skill 加载和应用启动。详情见 [验收记录](docs/validation.md)。前端通过 JavaScript 语法检查；本次环境的浏览器预览无法启动 Python 解释器，因此实际拖选手势、视觉布局与滚动同步仍需本机验收。没有调用真实模型或运行 BabelDOC 翻译。

## 第一版边界与后续

本版不含正式多用户鉴权、联网论文搜索/推荐、研究雷达自动调度、URL 下载器、OCR 和系列长报告。保留这些路线，先验收阅读闭环；前一版完整产品交互稿见 [Demo](https://paperstudio-workbench-lch.honest-anole-5183.chatgpt.site)。

不要直接将本地单用户服务暴露到公网。后续接入登录与用户隔离后再做多用户部署。当前仅支持单进程 worker；取消会丢弃迟到结果，但已发送的模型请求不一定能中止或免除费用。正式升级应增加数据库迁移、独立 worker 租约和更细的可恢复阶段。
