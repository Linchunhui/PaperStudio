# PaperStudio

PaperStudio 是可自部署的论文研究工作台：从论文目录挑选论文，按项目阅读 PDF、翻译、做笔记和带原文引用的问答，并与小组共享研究材料。

界面采用统一的阅读字号与语义色。目录高级筛选、来源说明和研究内容按需展开；窄屏导航和阅读工具支持键盘开关。设计规则及 UI UX Pro Max / Impeccable 的适配范围见 [DESIGN.md](DESIGN.md)。

本仓库提供前端、FastAPI 服务、账号与项目权限、SQLite 数据库及持久后台任务。默认单实例运行；已有 Sites Demo 是独立原型，本次没有部署或更新那个站点。应用不接入个人电脑的 SSH 成果库。

## 启动

需要 Python 3.12；开发检查另需 Node.js。安装核心依赖后，PDF 阅读、目录浏览、账号和笔记即可运行；模型、BabelDOC 和联网搜索分别配置。

```bash
git clone https://github.com/Linchunhui/PaperStudio.git
cd PaperStudio
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
./scripts/start.sh --no-demo
```

打开 <http://127.0.0.1:8000>。`scripts/start.sh` 优先使用 `.venv/bin/python`，也可通过 `PAPERSTUDIO_PYTHON` 指定解释器。Windows 可直接运行 `.venv\Scripts\python.exe run.py --no-demo`。

首次启动后，从服务数据目录的 `bootstrap-token` 文件读取一次性初始化口令，在网页创建管理员账号。默认目录为 `data/`；使用默认目录时可在服务所在机器上执行：

```bash
cat data/bootstrap-token
```

口令只用于首次初始化，创建管理员后文件删除；没有默认账号或密码。已有单用户版本的数据归首次创建的管理员继承。后续用户通过小组邀请注册或加入，不开放无邀请注册。初始化口令和模型凭据都不要粘贴进仓库、前端或公共日志。

`run.py` 自动读取仓库根目录 `.env`，采用字面量 `KEY=value`，支持单行引号和注释，不执行 shell、不展开 `$变量`、反引号或命令替换。已设置的进程环境优先于文件。可使用 `--env-file settings.env`、`--no-env`、`--host`、`--port`；相对数据目录始终以仓库根目录为基准。

```bash
./scripts/start.sh --check-config
```

该命令只检查配置语法，不打印密钥、不启动服务，也不代表模型已连通。不要使用多个 Uvicorn worker 或多个服务副本同时访问同一数据库。

## 论文目录与数据范围

首次启动只载入元数据，不批量下载论文或运行模型。索引与关注合并在“论文目录”，支持单位、会议、会议年份、展示标签、确认状态、研究方向及发表年份筛选；搜索和推荐中的方向标签也可点击筛选。

**当前仓库已内置 28,919 条论文记录及 32,162 条来源明细。** 首次启动校验并载入完整目录；已有部署在完整分片校验通过后，将旧种子来源归档并迁移到新目录，保留个人论文、项目和笔记。页面数量取自实际数据库，会议、方向等筛选只显示实际已导入的数据。

当前仓库内置目录的范围如下：

| 来源 | 保留范围 | 统计边界 |
| --- | ---: | --- |
| HF 周榜 | 4,748 篇 | 2025-W01 至 2026-W43，共 95 周；2026 年尚未覆盖全年 |
| 两批重点单位清单 | 6,134 条论文记录 | 两份清单全部保留 8,820 条来源记录，含 2,686 条重合来源 |
| 会议清单标为已确认 | 14,570 条展示记录 | 21 个会议系列；保留 Oral、Highlight、Spotlight、奖项和常规展示的区别 |
| 会议待核验 | 4,024 条候选 | 保留原标签与待核验状态，部分只有分组目录来源 |
| 合并目录 | **28,919 条独立条目 / 32,162 条来源明细** | 采用精确 ID / 规范化完整标题关联，未做模糊合并 |

**按方向移除 0 条，按年份移除 0 条。** 机构原清单中的 2023–2024 年记录保留；发表年份、会议年份和 HF 上榜周次分别保存。两份机构清单不会被误计为 8,820 篇不同论文。

完整数据包包含 `config/catalog/manifest.json` 与 79 个分片，约 70 MB，逐片记录 SHA256。没有 manifest 时继续载入旧种子；只有完整新包校验通过才归档替换旧种子。HF 指标为 2026-10-23 快照，机构点赞/Star 为 2026-10-24 快照；缺值保持未知。会议“已确认”沿用附件标注，未在本次逐篇重新核验官方展示等级。方向使用明确标注的关键词启发式分类，不作为删文依据。

共同机构目录、会议日程和 PDF 日程册只保留为来源，不作为同一论文的唯一标识，也不直接当全文下载。缺逐篇链接的条目通过标题检索定位。改名或版本关系有疑问时保留条目和来源，详见 manifest 的 `known_identity_review`。

管理员可上传 JSON/CSV 元数据或显式更新 HF 某周。维护命令：

```bash
.venv/bin/python scripts/update_catalog.py --seed-only
.venv/bin/python scripts/update_catalog.py --file index.json
.venv/bin/python scripts/update_catalog.py --from-week 2025-W01 --to-week 2026-W43 --resume
```

上传上限为单文件 20 MiB、25,000 行，大批数据应分片。HF 历史周页面返回的是抓取时快照，不是冻结的当周点赞。

## 论文方向

论文方向支持两级分类，使用 [config/directions.json](config/directions.json) 中的 **13 个一级方向、76 个二级方向**。例如“OCR / 文档智能”下分文字识别、版面分析与文档解析、文档问答、表格公式图表识别、文档检索五类。

论文索引和搜索/推荐页均支持一级、二级联动筛选，点击论文上的方向路径也可筛选；切换一级方向清除旧二级条件。每篇记录一个主方向和最多两个关联方向，筛选包含两者；覆盖统计的 `count` 按主方向独立计数，`match_count` 包含关联方向。

现有记录在升级时自动补分类，后续导入标题/摘要更新会重新分类。旧标签、论文 ID、原始来源和个人阅读资料保留。分类是标题/摘要关键词初标，不是全文精读或人工确认；缺少依据的二级方向保持待确认。分类细节与接口见 [docs/directions.md](docs/directions.md)。

## 阅读与翻译

阅读器默认显示原 PDF，不自动翻译。右侧使用“重排阅读”“对照翻译”按钮显示开关状态，缩放、项目和翻译操作也在右侧；中间保留连续阅读区。

- 拖动阅读区与侧栏之间的分隔线调整占比，浏览器记住宽度比例；分隔线聚焦后可用左右方向键微调，双击复位。窄屏使用可收起的工具面板。
- 在原 PDF 或重排文字中选中句子、段落后右键，或按 `Shift+F10`，再选择翻译、问答、总结、解释或笔记。翻译先校验所选原文，再只翻译选区；选择本身不打开笔记编辑器，也不启动模型。
- 原 PDF 使用原生页面渲染和透明可选文字层，避开 SVG 转换中字体描边失真；原始下载文件保持不变。重排将标题、段落与原页图表、表格和公式裁剪组合展示，图片可点击放大。
- 优先复用已有解析和译文。缺解析时优先 BabelDOC 结构化解析，再由 `gpt-6-luna` 翻译；解析失败时明确标注 PyMuPDF 文字层回退。
- 旧译文没有可靠逐段对应关系时按页展示，并明确标记“按页对应”；不通过段落序号猜配。
- 新 BabelDOC 译文关闭宣传水印。历史阅读展示和抽取文本过滤完整匹配的宣传文案，原始上传文件及版本不覆盖；原始历史文件下载仍保持原始字节。
- PDF、译文和批注按 SHA256 与翻译批次绑定；替换译文保留历史笔记，不把旧高亮套到新版本。

图表、公式可在原 PDF 框选或点击重排图表的讲解按钮后分析；服务端裁剪原图，视觉输入不可用时返回实际错误。复杂表格、数学版式和扫描件以图像保留，不宣称转成了可编辑表格或 OCR。图形布局识别失败时显示完整原页；每页另有“核对本页完整版式”入口。

## 有依据的问答和研究材料

单篇问答读取该论文；项目问答检索当前项目可访问的全部论文、笔记以及解读/总结。检索先覆盖项目材料，再选取相关原文放入模型上下文，返回扫描量、检索范围和缺失材料；不声称每次把所有 PDF 全文一次塞入模型。

回答按陈述附引用。原文摘录经服务端核对，另做支持关系检查；引用默认折叠，可展开看原句、页码和来源，再跳回匹配版本的原文位置。笔记、生成解读、前序论文与论文原文分开标记。没有足够依据时保留“缺少证据”，引用核验不等于对所有科学结论的人工审定。

研究脉络、图表公式和结构化知识都在点击后触发。脉络根据真实参考文献和本文引述建立关系，继续展开时按需获取前序论文；不会自动加入项目。结构化知识按研究问题、方法组件、实验、数据集、局限筛选和关联，保留原文证据。

## 小组与权限

每人独立账号，可创建小组并发放带角色的邀请。小组角色为管理员、可编辑成员和只读成员；站点管理员的服务配置权限与小组角色分开。

共享按**选中的项目**生效，不自动共享子项目。个人笔记默认私有，主动共享后才供项目成员查看；共享笔记保留作者和修改版本。解读与总结可显式共享；依赖私人笔记或已失效笔记快照的结果不能泄露给其他成员。阅读状态、页码和位置按用户独立保存。

成员主动把论文加入自己的项目后保留个人论文引用；退出原小组会立即失去原组项目、笔记和解读的访问权，但不删除已经保存到个人项目的论文引用，也不提高修改原论文公共译文的权限。

Skill 和服务配置只对站点管理员展示，后端同时校验权限。普通用户使用直接的翻译、解读、问答和笔记入口。邀请需要由小组管理员复制并分享，应用不自动发送邮件。

## 模型、解析与搜索配置

默认 `AI_PROVIDER=codex`、`AI_MODEL=gpt-6-luna`。在运行服务的同一系统账号下安装并完成 Codex 认证：

```bash
npm install -g @openai/codex@latest
codex login --device-auth
codex login status
```

无浏览器服务器的设备码登录须由操作者按终端提示完成，参见 [官方认证说明](https://developers.openai.com/codex/auth)。模型可用性取决于服务账号；没有权限时返回真实错误。Codex 凭据只保存在服务账号的认证目录，不能写入镜像或仓库。部署时建议将已验收的 CLI 版本固定下来。

也支持 `AI_PROVIDER=openai` 连接兼容 `/chat/completions` 的服务，设置 `AI_BASE_URL`、`AI_MODEL`、`AI_API_KEY`。BabelDOC 是可选依赖，可在独立工具环境安装后把 `BABELDOC_BIN` 指向可执行文件；其版面模型和字体资源需要部署机器能够下载或已预缓存。

```bash
python3.12 -m venv .babeldoc-venv
.babeldoc-venv/bin/python -m pip install BabelDOC
# 在 .env 中设置 BABELDOC_BIN 为该环境内 babeldoc 的绝对路径
```

具体解析顺序、CLI 隔离、取消行为和水印策略见 [模型与解析说明](docs/model-and-parsing.md)。文字解读、全文 PDF 翻译、段落翻译使用独立工作线程；项目研究任务另有队列。任务持久化，重启保留成功产物并将运行中任务标为中断，避免自动重复模型调用。

全网搜索可配置 SearXNG 或 Brave。arXiv/HF 学术检索独立；媒体来源失败保留最近成功缓存并标记状态。目录中的机构登记不等于已完成该机构全部自动采集。

工作台新增“为你推荐”：按账号保存方向/子方向、机构、会议与关键词偏好，提供公共目录推荐、不感兴趣、稳定分页和加入项目。第一版采用多路召回、内容相关性、时效和热度排序；支持导入真实 Embedding 后启用语义召回，未补齐数据时走规则路径。结构化数据和向量支持输入哈希校验、版本隔离及更新失效，不在请求中运行模型。接入步骤、CLI、API、算法边界和后续行为闭环见 [推荐系统文档](docs/recommendations.md)。

## Docker Compose

Dockerfile 使用 Python 3.12 核心环境和 Codex CLI；BabelDOC 默认不装。复制并修改 `.env` 后运行：

```bash
docker compose build
docker compose up -d
docker compose logs -f paperstudio
```

服务只映射宿主机 `127.0.0.1:8000`。数据、Codex 认证和工具缓存分别使用命名卷；`.env` 只读挂载，凭据不进入镜像。

```bash
docker compose exec paperstudio cat /data/bootstrap-token
docker compose exec paperstudio codex login --device-auth
```

需要 BabelDOC 时把 `compose.yaml` 的构建参数 `INSTALL_BABELDOC` 改成 `"1"` 后重新构建。`CODEX_PACKAGE` 和 `BABELDOC_PACKAGE` 可固定为已验收版本。构建依赖包以及首次模型资源需要联网；当前开发环境未执行 Docker 构建或真实模型联调。

对外服务由 HTTPS 反向代理转发到此端口，保留 `Host`。在 `.env` 配置实际域名 `PAPERSTUDIO_ALLOWED_HOSTS`、`PAPERSTUDIO_COOKIE_SECURE=1` 和可信代理地址 `PAPERSTUDIO_TRUSTED_PROXIES`；不要把任意转发来源设为可信。当前 SQLite 和进程内 worker 面向单服务实例，不提供横向扩容保证。

## 升级、备份和回滚

升级前停止服务，复制**整个数据目录**，包括 SQLite 数据库及 WAL 文件、原 PDF、译文、解析缓存和任务产物。单独复制数据库不能恢复正文和批注对应资产。另备份 `.env`，记录旧代码提交和镜像版本。

本机运行示例（数据目录使用默认 `data/` 时）：

```bash
# 先正常停止服务，再执行
paperstudio_backup="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$paperstudio_backup"
cp -a data "$paperstudio_backup/data"
cp .env "$paperstudio_backup/settings.env"
chmod 600 "$paperstudio_backup/settings.env"
git rev-parse HEAD > "$paperstudio_backup/commit.txt"
```

容器运行可在 `docker compose stop` 后使用 `docker compose cp paperstudio:/data ./backup-data` 复制完整数据。`docker compose down` 保留命名卷；不要使用会删除数据卷的 `down -v` 作为升级步骤。

本次身份和笔记迁移以新增表/列为主。旧 6,465 篇目录的已识别种子来源先写入 `catalog_legacy_archive`，再由新 manifest 替换；手工新增来源及个人论文、项目、笔记不随目录迁移删除。迁移前校验全部分片，失败会保留现有目录并记录错误。

升级后检查登录、目录统计、一个既有 PDF/批注和一个共享项目，再恢复正常使用。回滚时停止服务，切回旧提交/镜像，并恢复与该版本成对保存的完整数据备份；不要把旧代码直接接到已迁移数据库上。备份后的新增修改需要单独迁移，恢复前先保留当前数据快照。

## 开发与验证

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
./scripts/check.sh
```

检查脚本使用临时数据目录，执行 Python 编译、全部 pytest、所有前端模块语法检查和 `tests/*.mjs` 回归。实现契约见 [API 与数据约定](docs/contract.md)，已验证范围与仍需现场验收的项目见 [验证记录](docs/validation.md)，版本变化见 [CHANGELOG](CHANGELOG.md)。

本次自动验证覆盖身份隔离、引用与版本核验、真实 PDF/SQLite 处理、受控模型进程和前端交互控制器。尚未完成真实账号下的模型/BabelDOC 质量联调；浏览器连接返回 HTTP 401，未完成实际页面的视觉与鼠标手势验收。

