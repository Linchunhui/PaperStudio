# 论文推荐：第一版实现与数据接入

推荐与搜索共用工作台首页；`/?view=recommendations` 兼容跳转到同一页。关注入口位于论文索引，管理页位于 `/?view=library&section=follows`。沿用 FastAPI、SQLite、登录会话和 CSRF 校验；不用单独部署推荐服务，也不在请求中运行 Embedding 或大语言模型。搜索框可选择论文索引、arXiv、HF 或全网。

第一版是可工作的内容推荐基线：关注方向/子方向、机构、作者、会议和关键词；多路召回；内容相关性、新鲜度和热度排序；方向多样性；不感兴趣；稳定分页；打开论文与加入研究项目。没有用模拟阅读书单训练模型。Gorse、RecBole、行为学习和学习排序尚未接入。

## 用户画像与方向

- 偏好按当前登录账号保存，不能通过请求参数指定其他用户。重复保存用 `expected_version` 防止旧页面覆盖新偏好。
- 复用 `config/directions.json` 的稳定方向 ID。选择子方向不会自动关注父领域的其他方向；父领域也可以单独关注。
- 机构/作者/会议关注与目录同步。初次使用可迁移该账号浏览器里已有的关键词、`institution:` 和 `conference:` 关注；已有服务端偏好优先。
- 机构标签仍沿用目录来源，不代表已核实每位作者的机构隶属关系。
- “不感兴趣”只对该账号生效，可以一键恢复。目前不会据此自动推断新的负向兴趣。

## 召回与排序

| 阶段 | 当前实现 | 后续扩展位置 |
| --- | --- | --- |
| 多路召回 | 每个方向分别取近期与热门论文；机构/作者/会议精确匹配；标题/摘要关键词及结构化研究问题/方法匹配；方向向量对论文向量的余弦检索 | `CandidateProvider.retrieve`，可加 Gorse 或其他候选源 |
| 候选过滤与合并 | 公共目录 ID 去重，重新核验候选、显式方向筛选和“不感兴趣”；无画像或无匹配时回退近期/热门 | 排除已读、曝光疲劳和内容质量门槛 |
| 排序 | 有界特征加权，最多保留 480 个候选 | 用离线评测校准权重，之后替换为训练的排序器 |
| 重排 | 同方向重复出现的软惩罚，每 24 篇重置 | 研究阶段覆盖、近期探索配额 |
| 返回 | 推荐理由、`request_id`、位置、策略版本和游标；快照保存 15 分钟，每账号最多 5 份 | 曝光/点击归因、实验分组 |

当前没有单独训练粗排和精排模型。推荐分数为：

```text
score = 0.55 × relevance + 0.10 × semantic + 0.28 × freshness + 0.07 × popularity
```

`relevance` 为子方向 1.0、父方向 0.72、关键词 0.9、作者 0.9、机构 0.85、会议 0.65、结构化匹配 0.8、语义相似度的最大值。语义项额外参与排序，以区分同方向论文；缺失时为 0。余弦值不是点击概率，模型更换后应重新评估相关性阈值和权重。

新鲜度按实际 `published_at` 做半衰期衰减 `2 ** (-age_days / freshness_days)`，默认 30 天。入库时间不冒充发表时间；缺失、未来和来源明确标为“可能是会议日期”的时间不获得新鲜度加分。热门项使用 HF 点赞的对数缩放，最多按 1000 计算。无点赞保持未知并按 0 计入该项。

这组参数只是第一版基线，不是已经验证的最优参数。测试验证链路和约束，不证明推荐效果。上线前应人工评估不同研究方向的 Top-K 相关性、日期准确性和来源覆盖。

## Embedding 输入约定

论文主向量使用原始标题和摘要，模板版本 `paper-title-abstract-v1`：

```text
Title: 原始论文标题

Abstract: 原始摘要
```

导出结果包含 `entity_kind`、`entity_id`、`role`、`text`、`input_hash` 和模板版本。只有标题时明确标记 `title_only`；`--require-abstract` 可跳过这些论文。摘要补齐后哈希会变化，旧向量与绑定旧文本的结构化结果自动失效，需要重算并重新导入。

方向查询输入使用方向名称和描述，模板版本 `research-interest-v1`。**既要计算论文向量，也要计算用户可选方向的查询向量**。两者必须来自同一个模型空间，遵循所选模型各自的 query/document 编码方式；导出的 `role` 用来选择编码方式。自定义关键词目前走规则匹配，不会在请求中临时调用 Embedding 服务。

不要把全文、整篇翻译、长解读和用户私有笔记拼成一个主向量。这些文本可继续保存为独立研究资产；当前主向量与私有阅读数据分开。结构化研究问题、任务、方法和贡献走独立特征通道；数据集、实验、局限性和收录依据也在推荐卡片与阅读侧栏展示。

### 向量空间与检索

一个 `space_id` 固定 `model`、`model_revision`、维度、文档模板和查询模板。不同模型、维度或输入模板不得写入同一空间；更新模型时创建新空间，分批导入、检查覆盖，再切换。向量须是非零、有限数值，输入哈希必须与当前目录内容一致。服务保存归一化 FP32 向量。

当前用 NumPy 缓存矩阵进行精确余弦检索，按每个关注方向独立召回，不把不相关兴趣简单平均。显式方向筛选和不感兴趣先作用于向量候选，再取 Top-K。缓存按数据库、向量空间、导入修订号和分类版本隔离。查询向量缺失、论文向量未完成或候选提供器异常时，规则路径仍可返回结果。

以 30,000 篇、1024 维为例，单份 FP32 论文矩阵约 117 MiB；实际进程还会有 SQLite 结果、索引构建、筛选矩阵等额外内存，不应按这个数直接设置容器上限。多进程各自持有缓存，缓存最多保留两个修订。规模或并发提高后可把 `EmbeddingProvider` 替换为向量数据库 / ANN；接口层不必跟着改。

## 数据补齐流程

先正常启动 PaperStudio，使服务创建目录和推荐表。以下命令在服务同一代码版本、同一数据目录执行；脚本读取项目 `.env`，显式环境变量优先。数据文件放在部署数据卷中，不提交包含用户数据或大批向量的文件到仓库。

1. 用现有目录导入接口或 `scripts/update_catalog.py --file papers.json` 补充元数据和摘要。保留 arXiv、DOI 等稳定标识，避免仅凭改写标题制造重复论文。
2. 导出模型输入：

   ```bash
   python scripts/recommendation_data.py export-inputs --output /data/recommendation-inputs.jsonl --require-abstract
   ```

   输出文件必须尚不存在，避免误覆盖已送入模型的批次。每行包含一个论文或研究方向输入。脚本不会下载或运行模型。

3. 用选定的 Embedding 模型编码。保留每行的 `entity_kind`、`entity_id`、`input_hash`，输出 `vectors.jsonl`。每行只包含这四个字段，其中第四个字段是 `vector`（实际浮点数组）；不要把文档原文等额外字段一起回传。
4. 创建 `space.json`，如以下格式。模型名称、修订号和维度必须填写实际使用值，示例标识不是已接入模型：

   ```json
   {
     "space_id": "paper-content-v1",
     "model": "your-embedding-model",
     "model_revision": "exact-model-revision",
     "dimensions": 1024,
     "document_version": "paper-title-abstract-v1",
     "query_version": "research-interest-v1"
   }
   ```

5. 导入并检查：

   ```bash
   python scripts/recommendation_data.py import-vectors /data/vectors.jsonl --space /data/space.json
   python scripts/recommendation_data.py status
   python scripts/recommendation_data.py activate paper-content-v1
   ```

   每批最多 100 条，批内原子校验；成功批次保留，可幂等重跑。导入不会自动切换当前空间。至少已有论文和方向向量才允许启用；这是最低完整性检查，仍需运营方检查目标方向和论文的覆盖率。切换回旧空间可使用相同 `activate` 命令，旧空间中已过时的论文向量也会在元数据更新时失效。

6. 结构化结果用 `content.jsonl` 导入，每行格式如下。`source_document_hash` 必须取最新导出论文的 `input_hash`：

   ```json
   {
     "catalog_paper_id": "目录中的真实ID",
     "source_version": "extractor-v1",
     "source_document_hash": "最新导出的64位SHA256",
     "research_problem": "研究要解决的问题",
     "tasks": ["document understanding"],
     "methods": ["contrastive learning"],
     "contributions": ["论文声称的贡献"],
     "datasets": [],
     "limitations": [],
     "evidence": []
   }
   ```

   ```bash
   python scripts/recommendation_data.py import-content /data/content.jsonl
   ```

这些本地命令面向能够访问服务数据库的部署操作员。远程数据流水线使用下列管理员 API，按现有登录流程取得会话 Cookie 与 CSRF Token；不需要向普通用户开放写入权限。

## API

| 方法与路径 | 用途 |
| --- | --- |
| `GET/PUT /api/me/recommendation-profile` | 当前账号画像；PUT 需 `expected_version` |
| `PATCH /api/me/recommendation-profile/follow` | 更新单个机构/会议关注，不覆盖其余偏好 |
| `GET /api/recommendations/options` | 方向树和机构/会议选项 |
| `GET /api/recommendations?limit=24&category_id=&topic_id=&cursor=` | 推荐流；游标绑定用户、画像版本和筛选条件 |
| `POST /api/recommendations/{catalog_paper_id}/dismiss` | 不感兴趣 |
| `DELETE /api/me/recommendation-dismissals` | 恢复当前账号隐藏论文 |
| `GET /api/admin/recommendations/embedding-inputs?after=&limit=100` | 论文输入，按 ID 游标导出 |
| `GET /api/admin/recommendations/interest-inputs` | 分类/子方向查询输入 |
| `POST /api/admin/recommendations/vectors` | 一个模型空间的一批向量，默认不激活 |
| `POST /api/admin/recommendations/spaces/{space_id}/activate` | 切换已导入的空间 |
| `PUT /api/admin/recommendations/content/{catalog_paper_id}` | 版本化结构化结果 |
| `GET /api/admin/recommendations/status` | 当前空间、各类向量数量和结构化数量 |

向量 POST 请求由 `space.json` 的字段加 `items` 数组组成；数组元素格式与 `vectors.jsonl` 一致。可设置 `activate: true` 在同一事务导入后启用。未来的候选适配器只返回公共目录 ID 和可选的归一化相关性；协调层仍会检查 ID、权限范围与过滤条件。适配器必须自行限制网络超时，不能让外部服务阻塞阅读请求。

## 身份关联与后续行为闭环

推荐使用公共 `catalog_paper_id`，阅读器使用个人库 `paper_id`。从目录或推荐页导入时，服务核对论文链接后保存 `paper_catalog_links`；不能直接相信客户端 ID 或同名标题。已有来源匹配也可建立关联；标题兜底复用不会生成未经核验的推荐身份。目录合并会迁移已建立的关联和不感兴趣记录，并使旧推荐快照失效。

本版不新增自动点击、收藏、翻译、解读或阅读页数埋点，也没有后台训练、A/B 实验或行为统计看板。后续建议依次推进：

1. 保存真实曝光和行为事件：账号、目录论文 ID、个人库论文 ID、事件类型、时间、`request_id`、位置、策略版本、项目和会话。用事件 ID 做幂等；客户端传参不代替服务端用户身份与访问校验。
2. 定义有效阅读：真实停留、可见页面覆盖、重读去重等；“最后翻到第几页”不等于读过多少页。翻译/解读任务开始和成功分开，不把重试次数算作兴趣强度。
3. 建立训练/评估样本：按时间切分，排除未来信息泄漏；曝光后跳过与未曝光论文分开。合成数据只能用于功能测试，不用于声称线上推荐效果。
4. 用 RecBole 做离线模型比较，或让 Gorse 提供行为候选；保留本文的内容冷启动通道、时效性和最终重排。先对比内容基线，再引入训练排序器。

## 验证

运行 `bash scripts/check.sh`。新增测试覆盖冷启动、方向/机构/结构化匹配、未来和不确定日期、空间隔离、向量输入校验与失效、候选失败回退、分页稳定性、账号隔离、CSRF、管理员权限、目录身份合并，以及前端偏好迁移、草稿保留、失败重试和过期响应。

2026-10-27 本机单进程抽查：完整 28,919 篇目录、三个文档研究子方向，规则推荐返回 24 篇、71 个候选，6 次调用为 154–419 ms。另在隔离数据库中使用 28,919 × 1024 维随机测试向量、单 BLAS 线程验证检索容量：首次构建缓存约 2.65 秒，后续 4 次为 475–611 ms，合并后 191 个候选。随机向量只用于性能测试，没有进入生产数据，也不代表相关性效果。

前端控制器测试使用 DOM 替身。本次运行环境的 Chromium 下载不可用，未完成真实浏览器截图与触控验证；上线前需检查桌面/移动端偏好表单、长标题、键盘操作和打开论文流程。完整目录耗时检查只能代表本机单进程测试，不是生产并发容量结论。


## 推荐卡片与阅读侧栏

推荐响应保留 `abstract_preview` 兼容字段，并增加完整 `abstract` 和可空的 `structured`。卡片默认展示摘要预览与研究问题，长摘要、方法组件、贡献、实验、数据集、局限及收录依据可展开；推荐理由仍来自实际排序信号。

`structured` 是管理员导入的公共目录材料，仅在 `source_document_hash` 与当前标题/摘要哈希一致时显示，包含 `source_version`、`updated_at` 和 `evidence_scope: catalog-material`。新增可选 `experiments` 字符串数组与其他字段一起经现有导入接口和 CLI 保存；旧数据无需迁移。收录依据为文本材料，不冒充已通过 PDF 原句验证的定位引用。

`GET /api/papers/{paper_id}/insights?project_id=...` 返回经过项目成员权限检查的目录摘要/结构化材料。只沿核验来源建立的 `paper_catalog_links` 查找，未关联时返回空内容，不通过相似标题猜测身份。用户的私人笔记或研究任务不会加入公共推荐响应。

阅读器另从现有 `/api/research/jobs` 读取当前论文和项目内可见的解读，默认显示已保存的结构化结果；生成按钮才提交模型任务。切换“解读/问答/笔记”保留当前选区问题和解读，右侧共用单列滚动区。已保存研究任务返回其选区上下文，重新打开问答时能核对原文。

“加入项目”使用原生模态弹窗，只列可编辑项目；提交已有 `/api/imports`，处理选项固定为解析开启、翻译/导读/摘要关闭。接受后台任务后展示进度、失败重试和已有 PDF 的阅读链接；关闭弹窗继续浏览不会取消任务。缺少论文链接时在同一弹窗补充，不把会议目录当 PDF 下载，也不凭用户粘贴的链接强行建立目录身份关联。

## 分类索引与搜索解释

`GET /api/catalog/entities` 按方向、子方向、机构、作者或会议分页返回已收录数量，支持交叉条件。作者索引从 `authors` 原始名单构建，不消歧同名者，也不声称身份已认证。

`PATCH /api/me/recommendation-profile/follow` 接受 `category/topic/institution/author/conference`，只更新一个关注项，版本冲突会有限重试，不覆盖其他偏好。推荐快照随画像更新失效。旧账号缺少 `authors` 时默认空列表。

索引搜索采用所有查询词的字面匹配；相关度按每词标题 4 分、摘要 2 分，同分按发表时间降序及稳定 ID。作者、机构、方向、会议或标识命中仍可召回；结果给出实际字段命中与排序规则。也可显式切换时间升/降序、点赞和 Stars。外部检索沿用提供器顺序，不假称计算了本地语义分数。若外部结果的 arXiv ID 与目录精确对应，复用已收录摘要和版本一致的结构化解读，绝不按相似标题拼接私人结果。
