# 模型、解析和译文复用

PaperStudio 默认模型标识为 `gpt-6-luna`。这只是服务器配置的默认值，模型是否可用取决于服务账号及实际服务端权限；未安装程序、未登录或模型不可用时返回真实错误，不生成替代答案。

## Codex 与兼容 API

`AI_PROVIDER=codex` 使用服务器已安装并登录的 `CODEX_BIN`，调用 `codex exec`。提示词从标准输入文件传入，支持长材料；最终回答从 `--output-last-message` 读取。每次执行使用临时目录、`--ephemeral`、`--ignore-user-config`、只读沙箱，并关闭命令执行、App、Hook、子代理、远端插件和 Web 搜索能力。普通用户不能提交 CLI 参数或服务器路径。必须使用支持这些官方选项的当前 CLI；旧版本会明确失败。

`AI_PROVIDER=openai` 使用 `AI_BASE_URL`、`AI_API_KEY` 与 `AI_MODEL`，请求兼容的 `/chat/completions` 接口。非本机服务必须配置密钥。图表问答使用内嵌图像消息；模型不支持视觉时保留服务错误，不把纯文本结果标成看过图像。

`AI_TIMEOUT_SECONDS` 控制调用时限；`AI_MAX_INPUT_CHARS` 是应用输入保护阈值，超限报错而不悄悄截断。`AI_MAX_OUTPUT_TOKENS` 传给兼容 API；Codex 的实际输出容量受 CLI/模型限制。取消会停止 Codex/BabelDOC 子进程组，或关闭应用的 HTTP 请求连接，并且不发布晚到回答。已经发出的远端请求可能继续计费。

Codex 下的 BabelDOC 翻译通过任务专用的随机令牌、随机本机端口桥接到同一个模型。桥接仅监听 `127.0.0.1`，仅接受受限大小的 `/v1/chat/completions`，任务结束即关闭；令牌不会进入仓库或浏览器。此桥接不提供公网模型服务。

## 先解析再翻译

1. 优先复用与原 PDF SHA256 绑定的解析缓存和既有译文。
2. 缺少解析结果时，优先执行 BabelDOC 的 `--skip-translation --no-auto-extract-glossary --debug`。读取翻译前产生的 `styles_and_formulas.json` 或 `paragraph_finder.json`，标准化为有页码和坐标的原文段落。
3. 此解析步骤不给真实模型凭据，所需占位翻译配置指向不提供服务的 loopback 端口。调试 PDF 和完整中间文件只在临时目录存在，不作为阅读成果。
4. BabelDOC 未安装、解析失败或中间格式不兼容时，回退到 PyMuPDF 文字层，并将原因写入覆盖信息。扫描页无可用文本时保持空白证据状态，不编造 OCR。
5. 只有显式请求翻译才调用模型；打开 PDF、切换重排或选中文字本身不会启动全文翻译。

解析缓存位置为数据目录下 `cache/parsed/<原文SHA256>.json`。缓存包含解析器名称、格式版本、完整页数、无文本页、逐页段落及原文身份。它可跨同字节 PDF 复用，其中不保存个人笔记。旧版 `pymupdf-text-v1` 页文本缓存也可按同一原文 SHA256 复用，标记 `legacy-parser`；缺少段落坐标时只跳转到对应页，不猜测高亮框。按需获取的前序论文使用用户与项目命名空间隔离解析缓存。全量 PDF 版式翻译仍使用 BabelDOC 自身的解析→翻译→排版流水线；重排阅读和段落翻译使用缓存段落。

## 水印和旧译文

新 BabelDOC 调用明确设置 `--watermark-output-mode no_watermark`。历史 PDF 的阅读展示和抽取文本仅过滤完整匹配的 BabelDOC 宣传文案；一般论文中对 BabelDOC 的提及保持不变。过滤发生在内存展示层，原始上传文件和既有批注版本不被覆盖。

旧译文可直接提取为重排文字。没有可靠段落对应关系时，界面展示页级译文并标明 `alignment=page-only`；不会通过段落序号猜测对齐。原文 SHA256、译文 SHA256 和翻译任务版本均返回给阅读器校验。历史原 PDF 下载保持原始字节，因而已烙入原始历史文件的文案仍可能存在于那个原文件中；重新生成的译文没有该水印。

## 队列与共享

文字解读、全文 PDF 翻译、段落翻译各有独立工作线程，避免一本长文档阻塞交互。队列持久化；重启将执行中的任务标为中断，不自动重复付费调用。后台使用任务发起者的身份并持续检查原文及权限。

生成结果默认私有。作者可以共享解读与总结；包含私人笔记或已修改的旧笔记快照时不能共享。已经共享的笔记撤回或变化后，由其产生的旧结果也不会继续向其他成员开放。更改公共译文必须同时具备论文资源和所在项目的编辑权限；把共享论文加入个人项目不会提高该论文的修改权限。

## 上游接口依据

- [Codex 非交互执行](https://developers.openai.com/codex/noninteractive/)
- [Codex CLI 参数](https://developers.openai.com/codex/cli/reference/)
- [Codex 配置与功能开关](https://learn.chatgpt.com/docs/config-file/config-basic)
- [BabelDOC CLI 源码](https://github.com/funstory-ai/BabelDOC/blob/main/babeldoc/main.py)
- [BabelDOC 解析和翻译阶段](https://github.com/funstory-ai/BabelDOC/blob/main/babeldoc/format/pdf/high_level.py)
- [BabelDOC 中间层数据结构](https://github.com/funstory-ai/BabelDOC/blob/main/babeldoc/format/pdf/document_il/il_version_1.py)

本次代码验证覆盖真实 PDF 读取、缓存、版本校验、受控进程/API 契约、取消和权限；开发环境没有安装 Codex/BabelDOC，也没有配置模型凭据。部署后仍需使用实际依赖、服务账号和一份论文完成模型/排版端到端验收。
