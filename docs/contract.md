# API 与数据约定

应用为本地单用户服务：FastAPI、SQLite、PyMuPDF 与原生 JavaScript。默认监听 localhost；当前没有账户登录与多用户权限层。

## 数据关系

- `projects` 以 `parent_id` 形成目录树，移动节点拒绝自身和后代循环。
- `papers` 按原文 PDF 的 SHA256 去重，文件按内容寻址且不可变。
- `project_papers` 把同一论文关联到不同项目，分别保存阅读状态、页码与页内偏移 `page_offset`（0–1）。
- `annotations` 绑定论文、项目、原文/译文、页码、PDF SHA256 和规范化选区矩形。逐页译文还绑定 `translation_run_id`，后续页面生成不改变已有页的批注身份。替换译文保留旧批注，但不把旧高亮显示在新版本上。
- `jobs` 保存任务输入、笔记与 Skill 快照；只有成功且未取消的结果才提交。
- `imports/import_steps` 保存导入处理计划和依赖，`paper_sources` 保存来源与下载版本，`parse_artifacts` 按原文哈希缓存全文文字。
- `translation_runs/translation_pages` 保存逐页译文。单页 PDF 发布后不可变，整本合并仅作为下载产物。
- `discovery_cache` 保存真实来源的最后成功响应；来源失效时返回显式旧缓存状态。

## HTTP 接口

所有下表路径带 `/api` 前缀。交互式 API 说明见运行服务的 `/docs`。

| 路径 | 用途 |
| --- | --- |
| `GET/POST /projects` | 列出或创建项目 |
| `PATCH /projects/{id}` | 重命名或移动项目 |
| `GET/POST /papers` | 搜索已入库论文或上传 PDF |
| `GET /papers/{id}` | 论文信息与项目关联 |
| `POST /papers/{id}/projects` | 关联已有论文到项目 |
| `PATCH /papers/{id}/reading` | 更新当前项目的阅读状态与位置 |
| `POST /papers/{id}/translation` | 导入页数一致的单语译文 PDF |
| `GET /papers/{id}/file` | 原始 PDF 字节；`side=original/translation` |
| `GET /papers/{id}/page-manifest` | 全文页面尺寸，用于连续滚动占位 |
| `GET /papers/{id}/translation-pages` | 翻译批次与逐页状态；未完成时也可读取就绪页 |
| `GET /papers/{id}/pages/{n}` | 页面尺寸、文字坐标、版本与 SVG URL |
| `GET /papers/{id}/pages/{n}.svg` | PDF 页面直接生成的 SVG |
| `GET/POST /papers/{id}/annotations` | 当前项目批注列表或创建批注 |
| `PATCH/DELETE /annotations/{id}` | 编辑或删除批注 |
| `GET /papers/{id}/notes.md` | 导出当前项目的 Markdown 笔记 |
| `GET /capabilities` | 模型与翻译能力是否已配置 |
| `GET /skills` | 项目角色与输出模式 |
| `GET/POST /jobs` | 查询任务或按需入队 |
| `GET /jobs/{id}` | 任务状态与结果 |
| `POST /jobs/{id}/cancel` | 取消任务，拒绝提交迟到结果 |
| `POST /jobs/{id}/retry` | 显式重试失败或中断任务 |
| `POST /imports` | 从 arXiv/PDF 链接或已有 `paper_id` 创建处理计划 |
| `POST /imports/file` | 上传 PDF，同时选择项目与处理选项 |
| `GET /imports` | 查询导入进度，可按项目或论文过滤 |
| `GET /imports/{id}` | 导入计划、步骤依赖、错误与任务 ID |
| `POST /imports/{id}/cancel` | 取消未完成步骤，保留已入库原文 |
| `POST /imports/{id}/retry` | 重试受阻或失败步骤，保留成功产物 |
| `GET /papers/{id}/parsed` | 已缓存的全文文字、页码与覆盖信息 |
| `GET /discovery/config` | 搜索 provider 是否配置及来源适配方式 |
| `GET /discovery/search` | `q`、`scope=web/arxiv/hf`、`page`；不同搜索范围明确分离 |
| `GET /discovery/feed` | HF、量子位、机器之心的公开内容、来源状态与推荐理由 |
| `GET /sources` | 144 条来源登记与发现渠道 |
| `GET /health` | 服务健康状态 |

批注矩形使用 `x,y,width,height`，相对于对应页面宽高归一化到 0–1。页码从 1 开始，原文与译文分别保存自己的版本哈希。前端透明文字层只用于选区，视觉页面来自原 PDF 的 SVG。

## 异步任务

支持 `guide`、`brief`、`selection-summary`、`notes-summary`、`translate`。`brief` 支持 `abstract/cards/summary/all`。任务状态为 `queued/running/succeeded/failed/cancelled/interrupted`。

全文导读/摘要提取全部页面文字并保留页码；没有图像视觉核验。超出输入限制时明确失败，不静默截断。选区摘要仅使用对应批注；读后总结仅使用该项目笔记快照。用户笔记与 PDF 内容被视为证据数据，不作为覆盖角色规则的指令。

服务重启将正在执行的任务标为中断，等待用户重试；排队任务保留。当前只有单进程 worker，SQLite 队列不声称具备分布式租约或外部服务调用的无重复恢复。

## 导入处理计划

```json
{
  "url": "https://arxiv.org/abs/2501.12948v2",
  "project_id": "inbox",
  "processing": {
    "parse": true,
    "translate": true,
    "guide": false,
    "brief_modes": ["abstract", "cards", "summary"]
  }
}
```

`url` 和 `paper_id` 二选一。文件入口使用 multipart 的 `file/project_id/title/processing`，其中 `processing` 是 JSON 字符串。成功受理返回 HTTP 202。全文导读与粗读自动依赖解析；翻译只依赖原 PDF。下载与解析由独立 worker 调度，不等待模型任务完成。文本任务先入模型队列，翻译排在同一处理计划的文本任务之后。

下载成功立即返回可读 `paper_id`。无模型配置时相应步骤为 `blocked`；原文仍可阅读。取消保留成功步骤，重试不会重复下载已保存的 PDF。用户需要新的模型或输入快照时，应重新提交处理计划。

## 逐页译文与版本

`translation-pages` 的 `mode` 为 `none/manual/progressive`；逐页状态为 `queued/running/ready/failed/cancelled`。页面成为 `ready` 后可通过原有页面接口取得 SVG 和透明文字层。未就绪页返回 HTTP 409。全部完成之前，整本译文下载返回 409，而就绪页一直可读。

译文批注需携带页面元数据中的 `source_sha` 和 `translation_run_id`。人工上传译文后，旧后台批次失活；服务会拒绝基于旧页面新建批注，历史笔记保留。失败或中断任务重试沿用原批次与已成功页面，只处理其余页面。轮询清单只更新对应页，不重新载入整本文档。

## 发现结果

搜索与推荐返回 `items` 以及每个来源的 `statuses`。条目包含 `kind=paper/article`、原始 URL、发布时间（缺失则为空）、发现来源、摘要片段和推荐理由。来源状态包括 `ok/cached/stale/error/unconfigured`；`fetched_at` 是抓取时间，不能替代文章发布时间。全网服务未配置时搜索接口返回 409；单个推荐来源失败不会清空其他来源。

首版媒体入口为公开网站内容：量子位 RSS，机器之心经搜索 provider 的站内查询。没有声称已接入微信公众号、X 或完整历史消息流。配置搜索 provider 可查 README；本机 PDF、笔记与模型密钥不会作为推荐查询自动发送。
