# API 与数据约定

当前实现为 FastAPI + SQLite + 原生 JavaScript，面向单服务实例。每个实例拥有导入 worker、文字解读/全文翻译/段落翻译三个独立 worker，以及研究问答 worker。数据库队列持久化，进程内调度不支持通过增加 Uvicorn worker 横向扩容。

公共目录推荐在同一服务内运行，使用账号画像、结构化特征和可选的离线 Embedding；不新增模型运行 worker。推荐 API、数据输入、向量版本及目录/个人库身份关联见 [推荐系统约定](recommendations.md)。

## 身份与访问范围

`/api` 默认需要会话。公开接口仅包括健康检查、当前会话状态、首次初始化、登录和邀请注册；匿名请求不能以历史 `local` 身份读取业务数据。

登录使用 HttpOnly、SameSite=Lax cookie；HTTPS 或 `PAPERSTUDIO_COOKIE_SECURE=1` 时使用 Secure。写请求需要当前会话的 `X-CSRF-Token`，并检查 Origin/跨站请求。会话保存在数据库中，cookie 中是随机令牌。首次管理员通过服务端初始化口令创建，旧 `local` 数据随首次管理员迁移。

站点角色 `admin/user` 决定服务配置、Skill 和目录维护接口权限；小组角色 `admin/editor/viewer` 决定共享项目的操作范围。站点管理员身份不用于绕过他人私有项目和私有笔记的正常访问检查。

共享精确到所选项目，不递归共享子项目。用户的阅读状态独立保存。个人笔记默认 private；shared 笔记对当前项目成员可见，编辑权限由作者/项目角色决定，只有作者可改变笔记可见范围。版本接口过滤调用者不可见的历史 private 版本。

用户把一个可访问论文主动加入个人项目后，其个人关联可以保留。原组成员关系或项目共享撤回后，原组项目的笔记、解读和上下文权限即时撤回；个人关联不授予替换原论文公共译文的权限。

## 数据关系

| 对象 | 身份与约束 |
| --- | --- |
| `users/auth_sessions` | 独立账号、密码哈希、会话及 CSRF 随机令牌 |
| `groups/group_members/group_invitations` | 小组成员角色，一次使用且有到期时间的邀请 |
| `projects/project_shares` | 项目目录树、显式共享对象；移动拒绝环路 |
| `papers/project_papers` | 原 PDF 按 SHA256 内容寻址；项目关联独立于目录元数据 |
| `reading_progress` | 按用户、项目、论文保存状态、页码和页内偏移 |
| `annotations/annotation_versions` | 作者、可见范围、版本、原文摘录、页码和规范化矩形 |
| `imports/import_steps/paper_sources` | 导入计划、步骤依赖、来源和实际下载版本 |
| `parse_artifacts` 及 `cache/parsed` | 与原文 SHA256 和解析器版本绑定的解析缓存 |
| `jobs` | 原有解读/翻译任务，输入与模型/提示词快照、作者及共享范围 |
| `translation_runs/translation_pages` | 逐页译文、翻译批次及不可变页产物 |
| `research_jobs` 及证据快照 | 问答、图表公式、脉络、知识结构；引用随来源版本和权限校验 |
| `catalog_*` | 论文目录、来源观测、会议/方向/单位维度和导入历史 |

原文批注绑定 `paper_id/project_id/source_sha/page/rects`。译文另绑定 `translation_run_id` 与对应译文页身份。译文替换保留历史批注，但不允许把旧高亮移到新版本同坐标。

正文下载、缓存、生成任务和读取结果都检查调用者权限。后台 worker 使用任务记录的发起人身份，不依赖一个可变全局用户。缓存解析可以复用同字节 PDF，但个人笔记和生成材料不因缓存共享而扩大访问范围。

## HTTP 接口

下表路径统一带 `/api` 前缀；完整参数以运行服务的 OpenAPI `/docs` 为准。普通业务调用须先取得登录 cookie，写操作携带会话响应中的 `csrf_token`。

| 路径 | 用途 |
| --- | --- |
| `GET /health` | 进程健康状态，不披露业务材料 |
| `GET /auth/session` | 当前用户、初始化状态、CSRF token、必要能力布尔值 |
| `POST /auth/setup` | `username/password/display_name/setup_token`，仅首次管理员 |
| `POST /auth/login`、`POST /auth/logout` | 会话登录/退出 |
| `POST /auth/register` | `username/password/display_name/invite_token`，邀请注册 |
| `GET/POST /groups`、`GET /groups/{id}` | 小组列表、创建和成员详情 |
| `POST /groups/{id}/invitations` | 小组管理员发放邀请，`role`、`expires_days` |
| `POST /groups/join` | 已登录账号使用 `{token}` 加入小组 |
| `PATCH/DELETE /groups/{id}/members/{user_id}` | 小组管理员调整角色或移除成员 |
| `GET/POST /projects/{id}/shares` | 查询/添加显式共享对象；新增 `{group_id}` |
| `DELETE /projects/{id}/shares/{group_id}` | 撤回共享 |
| `GET/POST /projects`、`PATCH /projects/{id}` | 可见项目、创建、重命名和移动 |
| `GET/POST /papers`、`GET /papers/{id}` | 可见已入库论文、上传和详情 |
| `POST /papers/{id}/projects` | 关联已有可访问论文到项目 |
| `PATCH /papers/{id}/reading` | 当前用户的阅读状态与位置 |
| `POST /papers/{id}/translation` | 导入页数一致的单语译文，须资源编辑权限 |
| `GET /papers/{id}/file` | `side=original/translation`，保留原文件身份 |
| `GET /papers/{id}/page-manifest` | 全文页面尺寸，用于长文档连续占位 |
| `GET /papers/{id}/translation-pages` | 翻译批次、逐页状态与产物身份 |
| `GET /papers/{id}/pages/{n}`、`.../{n}.svg` | 页面文字坐标与矢量展示 |
| `GET /papers/{id}/pages/{n}/reading` | 原文段落、可复用段落译文及已有页级译文 |
| `GET /papers/{id}/pages/{n}/alignment` | 经过唯一定位校验的原文/译文段落对应 |
| `POST /papers/{id}/reading/translate` | 显式提交指定原文段落的翻译 |
| `GET/POST /papers/{id}/annotations` | 按项目和可见范围读写笔记 |
| `PATCH/DELETE /annotations/{id}` | 修改/删除；更新支持 `expected_version` |
| `GET /annotations/{id}/versions` | 当前调用者可见的修改记录 |
| `GET /papers/{id}/notes.md` | 导出该项目中自己可见的笔记 |
| `GET/POST /jobs`、`GET /jobs/{id}` | 解读、总结和翻译任务 |
| `POST /jobs/{id}/cancel`、`.../retry` | 发起者取消/重试 |
| `PATCH /jobs/{id}/visibility` | 已完成成果的 private/shared 可见范围 |
| `GET/POST /imports`、`GET /imports/{id}` | 持久导入计划 |
| `POST /imports/{id}/cancel`、`.../retry` | 取消或重试失败步骤 |
| `GET /catalog`、`GET /catalog/stats` | 目录搜索、筛选和统计 |
| `POST /catalog/import`、`POST /catalog/refresh-hf` | 仅站点管理员更新目录 |
| `GET /skills`、`/capabilities`、`/sources`、`/discovery/config` | 仅站点管理员读取配置详情 |
| `GET /discovery/search`、`/discovery/feed` | 搜索与推荐，保留来源状态和方向标签 |

## 研究问答与引用

只有显式 `POST` 才产生研究工作；读取历史、展开已有引用和浏览知识页面不自动重新调用模型。

```json
{
  "project_id": "当前可访问项目",
  "paper_id": "单篇论文ID；项目问答省略此字段",
  "kind": "qa",
  "question": "这项方法在哪些实验中体现优势？",
  "selection": {
    "source_sha": "原文PDF的64位SHA256",
    "page": 3,
    "block_id": "可选：reading接口返回的块ID",
    "quote": "可选：原文摘录",
    "rects": [{"x": 0.1, "y": 0.2, "width": 0.6, "height": 0.3}]
  }
}
```

请求发送至 `POST /research/jobs`；无选区问答省略 `selection`。`kind` 为 `qa/explain/summary/figure/formula/lineage/knowledge`。返回 HTTP 202 和持久任务；通过 `GET /research/jobs/{id}` 轮询。任务列表支持项目/论文范围；取消和共享接口分别为 `POST .../{id}/cancel` 与 `PATCH .../{id}/visibility`。

矩形坐标相对于实际原文页归一化，要求在页内且为有限数。图表/公式图像由后端从原 PDF 裁剪；不接受客户端把任意截图冒充该论文证据。译文选区不能直接冒充原文引用。

结果包括 `claims[].text/citations`、`unanswered`、`knowledge`、`lineage` 和 `coverage`。引用包含来源类型、论文/项目身份、原文版本、页码、块 ID、原句和坐标；图像证据提供权限受控的裁图入口。原句必须在服务端保存的来源块中匹配，只规范化 PDF 空白，不容许模型补词或更换大小写。支持关系另经独立检查，未通过的陈述/引用不作为有证据结论展示。

项目材料扫描和模型实际看到的检索片段是不同统计，分别在覆盖信息中记录；缺全文、无文字页和预算限制不能被写成已经完整阅读。笔记与生成解读只能支持关于这些材料自身的说法，不能替代论文实验原文。每次读取/共享结果重新检查来源可见性和版本，撤回共享后旧结果不能绕过权限。

研究脉络来自本文真实参考条目及引述。`POST /research/jobs/{id}/references/{index}/expand` 只接受已有、服务端核验过的脉络条目，按需取得前序论文；`POST /research/reference-assets/{id}/import` 才主动加入项目。目录缺少可解析全文入口时返回明确缺口，不把会议日程册下载成论文。

`GET /research/knowledge?project_id=...&category=...&q=...&label=...` 检索已生成且当前可见的知识项，不预生成全项目论文。分类为研究问题、方法组件、实验、数据集、局限，每项保留引用和关联标签。

## 解析、译文和显示

优先复用与原文哈希绑定的解析与译文；缺结果时执行 BabelDOC 解析。解析器中间格式不可用时回退 PyMuPDF 并记录原因。文本段落翻译与 PDF 版式翻译保存不同产物，不能互相冒充；具体参数见 [模型与解析说明](model-and-parsing.md)。

`reading` 中的 `existing_translation` 返回 `text/page/source_sha/original_sha/translation_run_id/alignment/status`。人工旧译文通常只有 `alignment=page-only`；阅读器核验原文/译文版本后按页展示，不生成假的段落配对。没有可靠坐标的引用只定位到页，过期版本不跳到新版同坐标。

BabelDOC 新译文使用无宣传水印输出；历史展示只过滤完整匹配宣传文案，正常正文对 BabelDOC 的讨论保持不变。原始上传字节、SHA256 和已有批注身份不覆盖。

## 目录口径与升级

`catalog` 与已下载 `papers` 独立。完整快照为 28,919 条保守独立条目、32,162 条来源明细；维度筛选不会删除记录。`GET /catalog` 支持 `q/year/source/institution/conference/conference_year/presentation/verification_status/direction/sort/page/page_size`。`year` 是发表年；会议年和 HF 周次独立。

目录按精确原始 arXiv ID 或规范化完整标题关联；共同机构目录和会议日程网址不充当论文身份。后续补充的 HF 元数据不能擅自消除有争议的改名条目。未知作者、单位、Star 等保留缺值；指标各有观察日期。

manifest 中逐片校验字节长度和 SHA256。旧种子迁移先归档其完整对象，只移出可识别的旧转换器来源，保留手工来源；不修改个人论文、文件、项目或笔记。启动重复载入使用指纹避免新增重复来源。保留旧目录归档不等于自动完成整库回滚，部署回滚仍需代码与完整数据备份配对。

