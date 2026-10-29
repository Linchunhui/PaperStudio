# 安装、升级与恢复

## 选一个入口

| 方式 | 适合 | 包含 |
| --- | --- | --- |
| `compose.release.yaml`，镜像 `0.5.0` | 先开始阅读，无需构建 | 服务、阅读器、笔记、导出、自编示例；完整历史数据另载 |
| `0.5.0-full` | 需要离线保留完整历史目录 | 核心服务 + 全部原始目录、关系、阅读卡片分片 |
| `0.5.0-codex` | 使用 Codex CLI 的操作者 | full + 固定版本 CLI；认证另行完成 |
| 仓库内 `compose.yaml` | 已有源码部署 | 保留原数据卷和只读 `.env`；本地构建 Codex 版本 |
| Python 源码 | 开发、其他架构或自定义工具 | Python 3.12；模型和 BabelDOC 按需安装 |

发布镜像目前验证 Linux amd64。`edge` 跟进主分支，正式使用固定版本或 `sha-<完整提交号>`。已有版本标签不自动重写。镜像的源码提交记录在 OCI 标签中。

首次 GHCR 发布后，维护者需要确认包可见性为 public，匿名拉取才能使用；Release 以实际成功的 Actions 为准。镜像拉取失败时查看 [发布记录](https://github.com/Linchunhui/PaperStudio/actions/workflows/ci.yml)，也可以先源码启动。

## 首次进入

1. 按 README 启动，服务只映射 `127.0.0.1:8000`。
2. 运行 `docker compose exec paperstudio cat /data/bootstrap-token`，在网页创建管理员。创建后令牌文件删除；没有预置账户。
3. 打开自编示例，或上传 PDF。选择文字后右键记笔记，不需要模型。
4. 到“Skill 与配置”保存模型设置，分别测试连接和实际调用。选择本地模型时必须填写实际支持的模型 ID。

所有镜像以 UID 10001 运行。使用宿主机目录代替命名卷时，需让该用户可写。不要使用多个服务副本或多个 Uvicorn worker 同时打开本实例的 SQLite 数据库。

Docker 中的 `localhost` 指容器本身。访问宿主机 Ollama 等服务可用 `http://host.docker.internal:11434/v1`，Compose 已添加 Linux 的 host-gateway 映射；宿主机模型服务还需监听容器可达接口。

## 完整数据包

新轻量镜像在“Skill 与配置 → 完整论文目录”显式加载。下载只来自本仓库固定版本的 Release，校验压缩包 SHA256、内部每个文件大小与 SHA256，再原子激活。中断可重试；不会覆盖个人 PDF、项目或笔记。

GitHub 下载受限时可用 full 镜像，或在完整源码环境加载已有分片。`PAPERSTUDIO_CATALOG_MODE=light` 只跳过初次目录载入，不清空现有目录。加载成功后记录启用标记，后续启动继续使用完整数据。

维护者打包和校验命令：

```bash
python scripts/package_data.py --output dist
python scripts/verify_data_package.py dist
```

## 可选 BabelDOC

段落翻译只需要文字层与模型；保持 PDF 版式的双语翻译还需要 BabelDOC。源码 Compose 默认不安装它。需要时设置 `INSTALL_BABELDOC: "1"` 再构建，工具安装在独立环境，避免覆盖 Web 服务依赖。

当前构建参数固定 `BabelDOC==0.6.4`、`@openai/codex@0.165.0`。可在维护时显式调整。CI 检查 Codex 安装和启动，**不执行付费模型调用，也不声明 BabelDOC 排版已通过质量验收**。BabelDOC 的字体、版面模型资源仍需预缓存或联网下载。

## 已有部署升级

不要覆盖原 `.env`，不要删除数据卷，也不要直接换成一个全新的 Compose 项目名。

1. 记录旧提交、镜像标签和当前卷名。`docker compose config --volumes` 显示逻辑卷；`docker inspect` 的 Mounts 显示实际卷名，检查时不要公开整个容器环境。
2. 停止服务，备份整个 `/data`、`.env` 和独立 Codex 认证卷。完整数据目录含 SQLite/WAL、PDF、译文、批注、解析缓存、产物以及私有模型配置。
3. 更新代码或固定镜像版本，继续挂载同一份数据。
4. 验证登录、已有 PDF、笔记、项目访问权限和新导出，再恢复使用。

源码 Compose 的示例备份：

```bash
docker compose stop
mkdir -p backups/before-0.5.0
docker compose cp paperstudio:/data backups/before-0.5.0/data
cp .env backups/before-0.5.0/env.private
docker compose build
docker compose up -d
```

如果从旧 Compose 切换到下载版，设置 `PAPERSTUDIO_DATA_VOLUME`、`PAPERSTUDIO_CODEX_VOLUME`、`PAPERSTUDIO_CACHE_VOLUME` 为原实际卷名。默认分别为 `paperstudio_paperstudio-data`、`paperstudio_codex-auth`、`paperstudio_babeldoc-cache`；原项目名不同则必须调整。继续原 Compose 也可以，无须切换。

网页保存的模型配置优先于环境变量；“使用部署环境配置”可移除覆盖。若部署端使用 `PAPERSTUDIO_MODEL_CONFIG=environment`，网页不能修改模型配置。

回滚应停止服务，先保留当前数据快照，再将**旧代码/镜像与对应的完整备份一起恢复**。不要让旧代码直接打开已迁移的数据。项目成果 ZIP 不是系统备份，不能恢复账号、PDF 和队列。

## 对外提供服务

在 HTTPS 反向代理后运行，保留 Host，设置实际 `PAPERSTUDIO_ALLOWED_HOSTS`、`PAPERSTUDIO_COOKIE_SECURE=1` 和可信代理地址。源码是完整服务；静态演示本身不能替代后端。数据卷必须持久化，临时云环境回收会丢失未持久保存的数据。

## 常见问题

- **初始化页面没有令牌？** 在运行服务的机器或容器内读取 bootstrap-token；已初始化则直接登录，令牌文件不再存在。
- **配置显示已填写，但调用失败？** 分别检查组件、连接和实际调用。部分 API 不支持 `/models`，实际调用测试更能确认模型是否可用。
- **没有推荐目录？** 轻量模式尚未加载目录。管理员加载数据后刷新索引；HF/arXiv 在线查询仍依赖网络。
- **导入清单后没有 PDF？** 文献迁入只保存元数据。点击“选择 PDF 链接并入库”，确认链接与处理步骤。
- **扫描 PDF 无文字？** 当前文字层回退不能制造 OCR；使用适当解析器，或保留原页阅读。
- **公开演示打不开？** 在仓库 Settings → Pages 将 Source 设为 GitHub Actions，再运行 Public read-only demo 工作流。离线演示 ZIP 和服务 `/demo/` 不依赖 Pages。
