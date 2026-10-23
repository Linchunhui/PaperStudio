# API 与数据约定

应用为本地单用户服务：FastAPI、SQLite、PyMuPDF 与原生 JavaScript。默认监听 localhost；当前没有账户登录与多用户权限层。

## 数据关系

- `projects` 以 `parent_id` 形成目录树，移动节点拒绝自身和后代循环。
- `papers` 按原文 PDF 的 SHA256 去重，文件按内容寻址且不可变。
- `project_papers` 把同一论文关联到不同项目，分别保存阅读状态和页码。
- `annotations` 绑定论文、项目、原文/译文、页码、PDF SHA256 和规范化选区矩形。替换译文保留旧批注，但不把旧高亮显示在新版本上。
- `jobs` 保存任务输入、笔记与 Skill 快照；只有成功且未取消的结果才提交。

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
| `GET /sources` | 144 条来源登记与发现渠道 |
| `GET /health` | 服务健康状态 |

批注矩形使用 `x,y,width,height`，相对于对应页面宽高归一化到 0–1。页码从 1 开始，原文与译文分别保存自己的版本哈希。前端透明文字层只用于选区，视觉页面来自原 PDF 的 SVG。

## 异步任务

支持 `guide`、`brief`、`selection-summary`、`notes-summary`、`translate`。`brief` 支持 `abstract/cards/summary/all`。任务状态为 `queued/running/succeeded/failed/cancelled/interrupted`。

全文导读/摘要提取全部页面文字并保留页码；没有图像视觉核验。超出输入限制时明确失败，不静默截断。选区摘要仅使用对应批注；读后总结仅使用该项目笔记快照。用户笔记与 PDF 内容被视为证据数据，不作为覆盖角色规则的指令。

服务重启将正在执行的任务标为中断，等待用户重试；排队任务保留。当前只有单进程 worker，SQLite 队列不声称具备分布式租约或外部服务调用的无重复恢复。
