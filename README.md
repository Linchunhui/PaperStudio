# PaperStudio

个人论文研究工作台：**搜索与推荐 → 链接/文件入库 → 按需后台处理 → PDF 连续对照阅读 → 划词笔记与总结**。

本仓库包含实际前端、Python API、SQLite 数据与后台 worker。旧交互 Demo 是独立原型，不是这个服务的线上部署。当前为本地单用户版本，默认监听 `127.0.0.1`。

## 启动

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/Linchunhui/PaperStudio.git
cd PaperStudio
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python run.py
```

打开 <http://127.0.0.1:8000>。Windows 对应解释器路径为 `.venv\Scripts\python.exe`。首次启动包含自行编写的两页双语 PDF 样本，明确标为非论文；传 `--no-demo` 可从空库开始。

配置使用服务端环境变量。可复制 `.env.example` 后修改，运行 `set -a; source .env; set +a`，再启动程序；应用不自动读取 `.env`。密钥不要提交到 Git 或填入前端代码。

## 搜索与推荐

首页为发现页，搜索已入库内容的功能保留在论文库。

| 能力 | 数据与配置 |
| --- | --- |
| 全网搜索 | SearXNG 或 Brave Search API；覆盖其索引到的论文、官网、媒体文章和项目页面 |
| 学术检索 | arXiv 与 Hugging Face 独立查询；不会拿库内结果冒充全网结果 |
| HF 推荐 | 真实 Daily Papers 接口，保留发布日期、热度与原论文链接 |
| 量子位 | 公开 RSS 内容，失败时显示来源错误与可用缓存 |
| 机器之心 | 通过已配置搜索服务检索公开官网内容；不假设旧 RSS 仍可用 |
| 推荐排序 | 基于兴趣词、时效与来源热度的规则排序，提供理由；不是训练好的个性化推荐模型 |

每个来源独立显示更新状态。失败不覆盖最后成功缓存，也不把抓取时间伪装成文章发布日期。同篇论文合并发现线索，媒体文章与论文原文保留区别。

全网搜索需配置其中一种服务：

```bash
# 自建 SearXNG：需在 settings.yml 的 search.formats 中启用 json
export SEARCH_PROVIDER=searxng
export SEARXNG_BASE_URL=http://127.0.0.1:8080

# 或者使用 Brave Search API：
export SEARCH_PROVIDER=brave
export BRAVE_API_KEY=your-server-side-subscription-token
```

没有搜索服务时，全网检索明确提示配置；arXiv/HF 学术检索和可用内容源仍可独立工作。144 条机构来源登记可浏览、筛选和关注，不代表 144 个采集器已经全部接通。公众号、X 等平台原帖以及更多大 V 可通过后续来源适配扩展；首版媒体范围为公开网站内容。

## 链接导入与后台处理

支持 arXiv `/abs/`、`/pdf/`、明确的 `vN` 版本链接，以及公网 PDF 直链。DOI 暂不支持自动解析，需提供公开 PDF；其他 URL 会尝试下载并校验为 PDF，普通 HTML 页面会提示改用 PDF 链接。付费或登录后的全文不会自动获取。

文件上传与链接导入共用项目和处理选项：

- 全文文字解析；
- 逐页中文翻译；
- 论文导读；
- AI 精简摘要；
- 卡片粗读；
- 全文总结式粗读。

下载保存后即可打开原 PDF，其余步骤继续在后台执行。需要全文的任务自动依赖解析。步骤分别记录状态、错误、产物与任务 ID；缺少模型配置时保留已入库原文，将相关步骤显示为阻塞，配置后可重试。取消与失败不会删除已经入库的原文或已经完成的产物。

导入时的“全文总结”根据论文材料生成；读完后的“笔记总结”只使用当前项目的个人笔记，两者分开。

下载器逐次检查重定向与目标地址，拒绝内网/本机地址，并限制实际文件大小、超时和重定向次数。原 PDF 以 SHA256 内容寻址，最大 50 MB、1000 页，不支持加密文件。明确的 arXiv 版本保留在来源记录中，新版不覆盖旧原文。

## 连续三栏阅读

阅读器使用独立页面 `/read/{paper_id}?project={project_id}`。左右原文/译文按页成对连续滚动，右侧 AI 和笔记固定；页码用于定位，不要求逐页点击。只挂载附近页面的 SVG 与文字层，保留占位以减少长论文的内存占用。

页面视觉内容直接由原 PDF 经 MuPDF 生成 SVG，文字、公式与线条保留原有矢量信息；透明文字层负责选择词句。原 PDF 中的扫描图、照片仍是位图，不会自动变为矢量。扫描件划词和全文理解需要先 OCR。

批注支持原文/译文选词、句子、跨行选区、个人理解与摘要、颜色、跳回引用、编辑、删除和 Markdown 导出。每个项目分别保存笔记、阅读状态和位置；切换编辑会保护未保存草稿。

### 双向段落联动

阅读器默认开启段落联动。点击原文或译文中的段落，对应一侧会高亮，必要时滚动到可见位置。划选词句时，个人笔记仍保存精确选区；另一侧只高亮对应段落，不强行逐词匹配。跨多个段落的选区会联动多个对应段落。

若译文尚未就绪，先保留原文选择，页面完成后再关联。用户已经滚离原段落时，不会被后台结果突然拉回。两侧联动高亮是独立视觉层，不覆盖浏览器文本选区、笔记高亮或未保存草稿；可通过“段落联动”开关关闭。

段落关系来自 BabelDOC 的实际 `translate_tracking.json`，再到两侧 PDF 文字层进行唯一定位，坐标按旋转/裁剪后的页面归一化。不会按位置或段落序号猜配，不额外调用模型。无法定位、重复文本歧义、复杂公式等情况会提示未对齐；能对齐的其他段落仍可联动。

此能力适用于升级后新生成且含有效翻译记录的译文。旧译文、人工上传译文或未输出记录的 BabelDOC 版本会提示缺少段落对应信息；可重新发起逐页翻译生成新记录。升级后的任务不会直接复用旧版已完成的翻译任务。人工替换译文或切换翻译批次时，旧联动映射失效，历史笔记保留。

缺少对应记录时可点击“重新翻译”显式生成新译文，这会重新调用翻译模型。已有同配置翻译任务正在进行时会复用该任务，避免重复提交。

## 逐页翻译

可直接导入与原文页数一致的单语译文 PDF，或者配置 BabelDOC 运行真实后台翻译。

逐页任务为每页保存独立译文 PDF、状态和内容哈希。一页完成后立即供阅读器获取，不等待整本完成；前端轮询页清单并更新就绪页。失败页单独记录，重试保留成功页面；全部完成后提供完整译文下载。

译文批注绑定翻译批次、页码和该页产物，不因后面的页面完成而失效。人工替换译文会使旧批次失活，旧任务不能覆盖新版本。服务重启保留已经完成的页面，未完成工作需按任务状态重试。

配置示例：

```bash
# 在单独工具环境中安装已验证版本，再设置可执行文件路径
uv tool install --python 3.12 BabelDOC
export BABELDOC_BIN=/absolute/path/to/babeldoc
export BABELDOC_TIMEOUT_SECONDS=1800
```

BabelDOC 还需要字体/版面模型资源和下述模型配置。当前实现采用应用层按页处理，首屏有实际模型调用与排版开销，不承诺即时完成。跨页段落和术语一致性可能弱于整本上下文翻译，复杂论文需要人工核对；没有用逐字打字效果替代真实的逐页 PDF 产物。

适配参数已核对 BabelDOC 官方 `main.py`（源码版本 0.6.4），并用受控子进程测试单页输入与 PDF 产物协议；尚未完成真实模型与复杂论文的翻译质量联调。

## 模型与 Skills

支持 OpenAI 兼容的 `/chat/completions` 服务，可使用 vLLM、SGLang 等部署的开源模型：

```bash
export AI_BASE_URL=http://127.0.0.1:8001/v1
export AI_MODEL=your-open-model
# 远程服务需要密钥，本机 loopback 服务允许留空
export AI_API_KEY=your-server-side-key
```

| 项目内角色 | 职责 |
| --- | --- |
| `skills/paper-guide` | 阅读路线、问题、前作缺口、方法机制和实验关注点 |
| `skills/paper-brief` | 精简摘要、卡片粗读、连贯总结、all 模式；保留用户要求的全文证据与精简规则 |
| `skills/paper-translate` | 翻译术语、公式、版式和版本约束 |
| `skills/reading-notes-summary` | 当前项目笔记归纳，区分原文事实、个人判断、疑问与后续行动 |

新任务加载角色及 references，旧任务保留输入、Skill 与模型配置快照。全文任务保留页码，输入超限时明确失败，不静默截断成“全文总结”。首版没有视觉模型核验原图，粗读在阅读器内输出 Markdown，不声称完成自包含 HTML/图片抽取的完整交付流程。

可调参数：`AI_TIMEOUT_SECONDS=180`、`AI_MAX_INPUT_CHARS=100000`、`AI_MAX_OUTPUT_TOKENS=8192`。模型输出达到 token 上限时报告截断。

## 开发、验证与数据

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
node --check web/app.js
node --check web/reader.js
node tests/test_reader.mjs
node tests/test_paragraph_reader.mjs
node tests/test_workspace.mjs
```

代码按 `app/discovery*`（发现）、`app/import*`（导入）、`app/core*`（项目/论文/批注）、`app/jobs.py`（AI任务）、`app/translations.py`（逐页译文）及 `web/`（前端）组织。API 文档运行后位于 `/docs`；验收范围与未完成的现场联调见 [验证记录](docs/validation.md)。

`app/alignment.py` 负责从实际翻译记录生成段落映射。BabelDOC 工作目录与记录在单页处理结束后清理，只随该页保存精简映射，不保留记录中的完整模型 prompt；追踪文件缺失或损坏不阻止有效译文 PDF 阅读。

`data/` 保存 SQLite、PDF、解析缓存与任务产物，已被 Git 忽略。备份时停止服务后复制整个目录。`PAPERSTUDIO_DATA_DIR` 可修改路径。升级会执行兼容旧数据的数据库初始化/列迁移，升级前仍建议备份。

当前为本地单用户、单进程服务；下载处理与模型任务分别调度。不要在没有鉴权的情况下直接暴露公网。取消会阻止提交迟到结果，但不能保证外部模型请求停止计费。正式多用户、OCR、系列报告和完整机构自动采集仍待扩展。
