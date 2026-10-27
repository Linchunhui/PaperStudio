# 两级论文方向

分类表来自用户提供的 `paperatlas-directions-v1.1`，保存在 `config/directions.json`。13 个一级方向、76 个二级方向均可选择；另有“未分类”兜底，不计入这 13 个正式方向。

| 一级方向 | 二级方向数 |
| --- | ---: |
| LLM / NLP | 7 |
| 多模态 | 5 |
| 计算机视觉 | 6 |
| OCR / 文档智能 | 5 |
| 生成模型 | 6 |
| 三维与图形学 | 6 |
| 强化学习 | 6 |
| 机器人 / VLA | 5 |
| 检索 / RAG / 推荐 | 5 |
| 学习理论与优化 | 7 |
| 训练与推理效率 | 5 |
| 安全与可信 | 6 |
| AI for Science | 7 |

`classification.primary` 保存主类别和主主题；`secondary` 保存最多两个关联主题。类别/主题用 `document`、`document.layout` 等稳定 ID 标识，名称可以独立展示。已有 `directions` 和 `direction_evidence` 字段继续保留原始平铺标签。

当前分类器只使用现有标题、摘要和旧标签，不发起下载或模型请求。关键词匹配记录字段、短语及相对排序分值；分值不是概率，所有结果均为 `heuristic-unverified`。没有明确子主题时保留一级方向、令 `topic_id=null`；一级也无法判断则使用 `unclassified`。不会为了达到配置里的 200/30 条覆盖目标强制归类。

目录初始化会在同一事务内补齐或升级分类及其 SQL 索引，失败则回滚。首次对完整旧目录执行时需处理全部记录，后续启动跳过当前版本。导入补充摘要时重新分类，删除旧的分类索引项，避免旧方向继续命中；不修改源分片，不改变来源明细、论文 ID 和个人阅读表。分类表变更会自动改变版本指纹；修改分类算法或补充代码中的别名时，应提升 `taxonomy.VERSION` 中的算法版本以触发已有记录更新。

接口支持：

- `/api/catalog?direction=document&topic=document.layout`：全目录过滤，可叠加单位、会议、年份与来源。
- `/api/discovery/search?q=OCR&scope=hf&direction=document&topic=document.ocr`：当前来源返回页过滤；无匹配时仍可翻下一页。
- `/api/discovery/feed?sources=hf&direction=document&topic=document.docqa`：来源返回集合过滤后排序。
- `/api/catalog/stats` 和 discovery 响应中的 `direction_taxonomy`：完整层级、版本和计数。`count` 按唯一主分类统计；`match_count` 包含主分类及关联分类。界面下拉显示匹配数，不能把关联方向的计数相加当作论文总数。

2026-10-27 全量元数据检查：28,919 篇、32,162 条来源记录保持不变；全部现有记录的摘要为空。标题规则为 11,828 篇给出二级主方向；17,091 篇的二级主方向待确认，其中 6,815 篇一级也未确定。这是关键词覆盖情况，不是人工准确率或论文质量评价。后续摘要导入能够提高覆盖率，但仍需核验。
