# Nasuchan

Nasuchan 是一个面向自托管服务的前端仓库，当前主前端是 Telegram Bot，同时提供一个轻量的本地 HTTP API 代理层。项目的目标不是把所有业务都堆进 Bot，而是把交互层、编排层和后端接入层分开，方便后续继续接入更多后端服务或更多后端能力。

当前代码已经落地的主要能力有：

- Telegram 命令与交互流程
- Fav backend 的健康检查、任务触发、任务轮询
- Hanime1 相关列表与种子管理
- 一个受 Bearer Token 保护的本地 HTTP API
- 通过 webhook 把 MarkdownV2 通知转发到 Telegram 管理员会话

## 运行环境

- Python `3.13.x`
- 包管理与命令执行统一使用 `uv`
- Telegram 框架使用 `aiogram`
- HTTP 客户端使用 `httpx`
- 配置通过 `config.toml` 加载，并由 `pydantic` 校验

## 快速开始

安装依赖：

```bash
uv sync
```

准备配置：

```bash
cp config.toml.example config.toml
```

然后填写：

- `telegram.bot_token`
- `telegram.admin_chat_id`
- `backend.fav.base_url`
- `backend.fav.token`
- `public_api.token`（如果要启用本地 HTTP API）

运行 Telegram Bot：

```bash
uv run python -m nasuchan.bot
```

运行本地 HTTP API：

```bash
uv run python -m nasuchan.api
```

通知 webhook 需要命中 `nasuchan.api` 进程，所以部署时需要同时运行这两个进程。

运行测试：

```bash
uv run pytest
```

代码检查：

```bash
uv run ruff check .
uv run ruff format .
```

## 当前目录结构

```text
src/nasuchan/
  api/         aiohttp 对外暴露的本地 HTTP API
  bot/         Telegram 启动、router、handler、middleware
  clients/     后端 API 客户端、异常与数据模型
  config/      配置加载与校验
  services/    业务编排、轮询、文本渲染
tests/         配置、client、handler、middleware、API 测试
```

## 架构约束

默认的数据流保持为：

`Telegram handler -> service -> backend client -> backend API`

如果能力通过本地 HTTP API 暴露，则保持为：

`aiohttp handler -> service -> backend client -> backend API`

约束如下：

- `bot/` 只处理 Telegram 交互、状态机、按钮、消息格式。
- `api/` 只处理 HTTP 鉴权、请求/响应映射。
- `services/` 负责轮询、编排和跨接口组合。
- `clients/` 负责协议细节、认证头、URL、错误映射、响应解析。
- `config/` 负责所有配置定义与校验，避免在别处读环境或拼配置。
- `clients/` 与 `services/` 的边界尽量使用类型明确的 Pydantic 模型。

## 当前代码里的参考实现

可以直接把下面这些文件当成后续扩展时的模板：

- `src/nasuchan/clients/api.py`
  当前 `FavBackendClient` 的实现，包含统一请求、鉴权、状态码到异常的映射。
- `src/nasuchan/clients/models.py`
  当前后端响应模型定义。
- `src/nasuchan/services/control.py`
  任务轮询的 service 示例。
- `src/nasuchan/bot/handlers/commands.py`
  命令处理、回调按钮和用户提示文案的示例。
- `src/nasuchan/bot/delivery.py`
  Telegram MarkdownV2 消息投递的基础 helper。
- `src/nasuchan/bot/handlers/hanime1.py`
  一个较完整的 feature handler 示例，包含 FSM、分页和删除流程。
- `src/nasuchan/api/app.py`
  本地 HTTP API 的鉴权、代理和 webhook 投递示例。

## 后续接入更多后端的实现原则

### 1. 先判断是“扩展现有 Fav backend”还是“接入新的后端服务”

如果只是 Fav backend 新增了一组接口：

- 继续在现有 `FavBackendClient` 里增加领域方法
- 在 `clients/models.py` 增加对应请求/响应模型
- 在 `services/` 增加编排逻辑
- 在 `bot/` 或 `api/` 暴露入口

如果是新增一个完全不同的后端服务：

- 不要继续把它塞进 `FavBackendClient`
- 在 `config/settings.py` 新增独立配置段
- 在 `clients/` 新增独立 client
- 在 `services/` 里组合多个 client

当第二个后端真正出现时，建议顺手把当前 `src/nasuchan/clients/api.py` 拆成更明确的后端级文件，例如：

```text
src/nasuchan/clients/
  fav.py
  foo_backend.py
  models.py
  exceptions.py
```

如果模型规模明显继续增长，再把 `models.py` 按后端或业务域拆分。

### 2. 配置先行，不要把连接参数写死在实现里

新增后端时，先补配置模型和样例配置：

- 在 `src/nasuchan/config/settings.py` 增加新的 Settings 类
- 把它挂到 `AppConfig` 里
- 为 URL、token、timeout、端口等字段加校验
- 同步更新 `config.toml.example`

推荐保持这种配置风格：

```toml
[backend.foo]
base_url = "https://foo.example.com"
token = "replace-me"
request_timeout_seconds = 15
```

如果未来某个后端不走 Token，而是 Basic Auth、Cookie、mTLS 或签名认证，也要把认证材料仍然放在配置层，不要在 handler 或 service 里拼接。

### 3. Client 层只暴露“领域方法”，不要泄漏 Telegram 或 aiohttp 细节

客户端方法应该像现在这样：

- `health()`
- `list_jobs()`
- `create_job_request(target)`

而不是：

- `send_health_message_to_telegram()`
- `build_inline_keyboard_for_backend_result()`
- `return_aiohttp_response()`

保持 client 纯粹之后，Bot、HTTP API、未来 Web UI 都能复用同一套接入层。

### 4. 统一处理错误映射

新增接口时，优先复用现在的模式：

- client 内部统一发请求
- 根据 HTTP 状态码抛出领域异常
- service 或 handler 只决定怎么降级、怎么提示用户

这样可以避免：

- 每个 handler 各自解析 401/403/404
- 文案、日志、错误恢复策略到处重复

如果新后端有自己独特的错误码语义，优先扩展 `src/nasuchan/clients/exceptions.py`，不要在 handler 里写一堆字符串判断。

### 5. Service 层负责“业务流程”，不是简单转发

以下逻辑应该放在 `services/`：

- 轮询直到任务结束
- 多接口拼装一个用户可读结果
- 未来跨多个 backend 的协调调用

以下逻辑不要放在 `services/`：

- Telegram 文案细节
- InlineKeyboard 构造
- aiohttp 的认证头校验

一个简单判断标准是：如果这段逻辑未来会被 Bot 和 HTTP API 共同复用，它更应该在 `services/`。

### 6. Handler 和 API 层保持薄

新增一个能力时，Bot handler 或 HTTP handler 最好只做这几件事：

1. 收集输入
2. 调 service 或 client
3. 把结果转换成 Telegram 消息或 JSON 响应
4. 处理用户可见错误

如果一个 handler 开始直接组装 URL、处理分页游标、拼接认证头、轮询状态，这通常说明边界已经漏了。

### 7. 优先按“能力”补测试

新增后端接入后，至少覆盖下面几类测试：

- 配置测试：字段缺失、URL 无效、timeout 非法
- client 测试：请求路径、方法、鉴权头、响应解析、异常映射
- service 测试：轮询结束条件、批处理逻辑、失败分支
- handler 测试：用户输入到调用链的行为是否正确
- public API 测试：鉴权、JSON 响应、错误回包

当前测试文件已经给了基本分层：

- `tests/test_config.py`
- `tests/test_backend_client.py`
- `tests/test_handlers.py`
- `tests/test_public_api.py`

新增能力时，优先沿着这些文件继续扩展；只有当测试规模明显变大时，再拆更细的测试模块。

## 推荐的增量接入流程

以后每次接一个新能力，建议按这个顺序提交：

1. 先补 `config` 和 `config.toml.example`
2. 再补 `clients` 的模型、请求和错误映射
3. 再补 `services` 的编排逻辑
4. 再补 `bot` 或 `api` 的入口
5. 最后补测试、文档和必要日志

这样做的好处是：

- 边界最清楚

## Notification Webhook

通知改为 push 模式，Nasuchan 不再主动轮询后端通知列表。上游直接调用本地 HTTP API：

```bash
curl -X POST http://127.0.0.1:8092/api/v2/notifications/webhook \
  -H 'Authorization: Bearer public-runtime-api-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "markdown": "*任务完成*\\n[查看详情](https://example.com/task/123)",
    "disable_web_page_preview": true,
    "disable_notification": false
  }'
```

请求规则：

- `markdown` 必填，作为 Telegram `MarkdownV2` 原文直接转发
- `disable_web_page_preview` 可选，默认 `true`
- `disable_notification` 可选，默认 `false`

注意事项：

- 上游必须自己负责 Telegram `MarkdownV2` 转义和格式正确性
- webhook 固定投递到 `telegram.admin_chat_id`
- 如果 Telegram 投递失败，接口返回 `502 {"error":"telegram_delivery_failed"}`
- 更容易单测
- 不会一上来把 Telegram 交互和后端协议耦合死

## 一个新后端能力落地时的检查清单

- 是否在 `config/settings.py` 定义了新配置并校验？
- 是否更新了 `config.toml.example`？
- client 方法是否是 async 且面向领域，而不是面向 Telegram？
- 是否把状态码和错误码映射成了稳定异常？
- 是否在 `services/` 里承接了轮询、组合、重试或 webhook 转发等流程？
- handler/API 是否只做输入输出转换？
- 是否补了至少一条成功路径和一条失败路径测试？
- 是否避免把真实 token、URL、`.env` 内容写进仓库？

## 安全与提交前检查

- 不要提交真实 bot token、后端 token、DSN、密码、私钥或 `.env`
- 样例配置一律使用占位符
- 推送到公开远端前，按仓库约定执行：

```bash
gitleaks detect --source . --no-git
gitleaks git .
```

## 当前适合的下一步演进

如果接下来真的会持续接入多个后端，最值得优先做的不是加抽象工厂，而是保持以下两点：

- 每个后端一个独立 client
- 由 `services/` 负责跨后端编排

先把边界守住，再根据真实需求决定是否要引入更强的依赖注入或模块拆分。当前阶段不建议为了“未来可能会有很多前端或很多后端”而提前搭建复杂框架。
