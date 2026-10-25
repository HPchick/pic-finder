# 技术可行性调研

本文记录 PIC FINDER 涉及的各网站接口现状与法律风险，用于支撑 DD-03 与 DD-12 两项决策。

## 调研说明

本次调研未能执行实时网络探测。原因有两个：运行环境只读且无网络出口；部分域名的域名解析被本机网络环境拦截。

因此所有关于当前是否可用的结论，均由文档与二手证据推断，不是实测结果。凡未实测者，文中均已标注。

本文不构成法律意见。若将来要对外分发，建议就程序化访问与分发两个问题做法务确认。

## 结论速览

1. ArtStation 与 Pixiv 都没有面向第三方的官方公开接口。其可用接口都是网站或应用的私有端点，程序化访问均属明确违反各自服务条款。
2. 若必须接入第二类站点，先做 ArtStation 更划算。它是一个匿名可读的 GraphQL 端点，无需用户登录，也没有令牌生命周期管理。
3. 第一类五个站点全部可用，且无需逆向。这是唯一具备官方接口与明文速率规则的部分。
4. 分级过滤必须按新语法编写。Gelbooru 已废弃 `rating:safe`。仅靠分级参数无法做到严格安全。

## 站点分级

按接口合规性与稳定性，目标站点分为四级。

- **第一类**：有官方接口或官方接口文档，无需逆向，有明文速率规则。包括 Danbooru、Safebooru、Gelbooru、yande.re、Konachan。
- **第二类**：无官方接口，须使用网站私有端点，明确违反服务条款。包括 Pixiv、ArtStation。
- **第三类**：反爬激进，无稳定接口，维护成本极高。包括 Pinterest、花瓣、Google 图片、Bing 图片。
- **第四类**：基本不可行。包括 Twitter、Bilibili、微博、Instagram。

本项目的选择是：第一类做自动检索；第二类降级为一键跳转官网检索；第三类与第四类不做。

## 第一类站点

### Danbooru

Danbooru 是本项目的首选核心源。接口文档地址为 <https://danbooru.donmai.us/wiki_pages/help:api>。

**可用性。** 接口为 REST 风格，以 GET 请求返回 JSON 或 XML。读取不强制要求接口密钥，只有写入操作需要密钥。

**速率限制。** 全局读限为每秒 10 次请求，与账号和端点无关。官方明确要求长会话压到每秒约 1 次请求。响应头 `x-rate-limit` 可以查询当前状态，站点另设 `/rate_limits` 页面。速率限制在账号与 IP 之间共享。

**用户代理要求。** 必须携带唯一且可识别的用户代理标识，形如 `YourBotName/1.0 (user #id)`。官方原文为：不得冒充浏览器，也不得使用所用网络库的默认标识。官方同时警告，行为不良的机器人会被迅速封禁。

**批量取图。** 应使用标识范围查询，例如 `tags=id:1..100`，一次取 100 条，不要逐个编号扫描。

**服务条款。** 最后更新于 2022-10-23。条款禁止绕过速率、上传与访问限制，也禁止创建、使用或分发会损害站点或允许用户绕过限制的机器人与脚本。

**分页。** 支持 `page` 与 `limit` 参数，`/posts.json` 的 `limit` 上限为 200。也支持 `b<id>` 与 `a<id>` 两种游标。超出页数上限时返回 410 状态码。

**分级参数。** 取值为 `rating:general`、`rating:sensitive`、`rating:questionable`、`rating:explicit`，可简写为 g、s、q、e。注意 `rating:safe` 已改名，旧写法会失败。

对本项目的意义在于：Danbooru 是唯一同时满足官方支持、标签体系丰富、无需登录三个条件的源。把中文翻译成 Danbooru 标签，是本项目的核心竞争力所在。

### Gelbooru

**接口密钥。** 必须提供 `api_key` 与 `user_id` 两个参数。官方说明为：站点偶尔会要求认证并限制接口访问，目的是减少滥用行为。赞助者不受限流。

**分发摩擦。** 每个用户都需要自行注册并填写密钥。开发者内置共享密钥供全体用户使用是不可接受的，会被限流甚至封禁。

**其他参数。** 无公开的速率数值。默认 `limit` 为 100，使用 `pid` 分页，`json=1` 返回 JSON。

**分级。** 取值为 General、Sensitive、Questionable、Explicit 四档。`rating:safe` 已不存在，被 General 与 Sensitive 取代。

须注意 Gelbooru 的 General 档定义仍容忍 `large_breasts`、`cleavage`、`midriff`、`bare_legs` 等标签。因此仅使用 `rating:general` 不等于严格安全。

### Safebooru

接口文档为 <https://safebooru.org/index.php?page=help&topic=dapi>。

端点为 `index.php?page=dapi&s=post&q=index&json=1`。无需接口密钥，`limit` 硬上限为每次 1000 条，使用 `pid` 分页，支持全部站内标签组合。

文档极简，没有速率条款。调研未能找到其服务条款文本。需要说明的是，未找到明文禁令并不等于允许。

站点定位本身即安全向，是最省事的默认安全源。

### yande.re

接口文档为 <https://yande.re/wiki/show?title=api_v2>。

它是 Moebooru 站点，端点为 `https://yande.re/post.json?api_version=2&tags=...&limit=...`。`limit` 取值范围为 1 至 100，支持全部元标签，`include_tags` 参数可以获取标签类型。无需接口密钥。

无官方速率数值。社区讨论显示官方态度是要求控制负载。

需要注意，其分页只有 `page` 与 `limit`，不支持按编号便捷取单帖。这个限制会使增量同步的实现变得别扭。

### Konachan

接口文档为 <https://konachan.com/help/api>，标注版本为 API 1.13.0+update.3。

它是 Moebooru 站点，兼容 Danbooru 1.13.0。端点为 `/post.json`，`limit` 硬上限为 100，使用 `page` 分页，无需接口密钥。

其接口版本非常老，缺少复数路由、编号范围游标与速率响应头。因此不能与 Danbooru 的客户端代码共用一套逻辑。

## 第一类站点横向对比

| 站点 | 是否需要密钥 | limit 上限 | 速率是否明文 | 分页方式 | 备注 |
|---|---|---|---|---|---|
| Danbooru | 读取不需要 | 200 | 是，建议每秒 1 次 | page 与游标 | 规则最完整，合规样板 |
| Gelbooru | 需要 | 100 | 无，有限流 | `pid` | 密钥是分发摩擦点 |
| Safebooru | 不需要 | 1000 | 无 | `pid` | 最省事，默认安全 |
| yande.re | 不需要 | 100 | 无，要求控负载 | `page` | 无单帖查询 |
| Konachan | 不需要 | 100 | 无 | `page` | 接口版本老，须单独适配 |

共同底线有三条：使用声明式用户代理标识、遵守站方速率、不并发轰炸。

## 第二类站点

### ArtStation

ArtStation 没有公开的官方接口或开发者门户。网上流传的开发者页面是某艺术家公司的接单广告，不是官方接口文档。

**现存路径。** 唯一路径是网站前端的私有端点，即 `POST https://www.artstation.com/graphql` 及其同族端点。端点与查询哈希需要自行固化，实现前必须用浏览器开发者工具抓取真实请求。

**生态现状。** 开源封装库中，影响最大者已于 2019 年归档停止维护。其余为个人小项目，无正式维护的库。这意味着不存在事实标准的中间件，需要自行实现。

**能力。** 支持关键词与分类筛选，可按媒介、题材、软件、素材类型过滤。返回原图地址、作者、点赞数与永久链接。支持 `page` 分页，排序方式为相关度、点赞数与时间。

**反爬。** 站点 robots 文件目前相当宽松，只禁止少量路径。但 robots 允许不等于服务条款允许。没有出现 Cloudflare 级别的公开挑战报告，明显轻于 Pixiv。

**稳定性风险。** 端点与查询哈希会随前端发版静默变化。因此必须做成可远程下发的配置，损坏时不必发版即可修复。

**运营主体变更。** 站点运营主体已变更。页面提示自 2025-04-06 起更新条款，新条款文本本次未能读取，立项前务必复核。

**服务条款第 5 条原文。**

> You may only access the Services through the interface provided on the Site. Also, you may only use the Services for your own purposes, and you must not resell, transfer or otherwise provide any of the Services to any third party.

这一条有两层含义。第一，任何非网页界面的程序化访问都违反条款。第二，不得把服务提供给第三方，这意味着把软件分发出去也违反条款，即使免费。

### Pixiv

Pixiv 官方从未提供接口。现存的一切接口文档都是社区逆向的产物。

**库现状。** 使用最广的 Python 封装库最后版本发布于 2022-02-03，此后没有新版本。其文档明确说明账号密码登录方式已不存在，必须使用刷新令牌。异步版本最后发布于 2022-02-20，同样停止更新。

异步版本的文档中有两条关键事实。其一，访问该接口可能需要日本 IP，其他地区的 IP 会遇到 Cloudflare 验证。其二，该接口对访问频率有严格策略。

结论是：这些库并未失效，但已事实停止维护。它们是对非官方端点的薄封装，站点一旦更改签名就会失效，且无人修复。

**登录态。** 流程为 OAuth2 授权码模式加 PKCE。访问令牌有效期约 3600 秒。刷新令牌可以反复使用直到过期，但其绝对有效期本次未取得一手证据，因此必须按随时可能失效来设计，并提供重新登录入口。刷新令牌是账号级凭据，由第三方软件代持本身即服务条款风险点。

**检索能力。** 支持按标签检索，可选择部分匹配、精确匹配、标题说明文或关键词。排序受限制：时间正序、时间倒序可用，但按人气排序是该站会员的专属功能。免费账号只能按时间排序。

分页为游标式，不是页码式。

需要特别注意，应用接口返回的大图通常只有 1200 像素级别。真正的原图地址需要走网页接口获取。

**反爬。** 2021 年的 Cloudflare 问题根因是传输层指纹，该结论已过去四年多，不能当作现状。2022 年关于非日本 IP 会遇验证的结论，是目前最可信的可用性约束。频率限制没有官方数值，社区一致描述为严格策略，会触发 429 状态码。

**替代方案。** SauceNAO、IQDB、ascii2d 都是以图搜图服务，输入图片后输出 Pixiv 链接。它们不能替代按语义检索的入口，但非常适合做本地归档的去重与溯源辅助。

## 法律风险分级

### 明确违反服务条款

下列做法明确违反服务条款，不应以未被起诉为由合理化。

1. ArtStation 私有端点的程序化调用。这既是违约访问，也是违约分发，依据为服务条款第 5 条。
2. Pixiv 应用接口与网页接口的程序化调用，以及刷新令牌的代持。该站主条款将自动化访问列为禁止行为。本次调研未能读取该条款的逐条原文，日文版本优先，措辞需以日文版为准。
3. 绕过反爬机制，包括更换传输层指纹、伪装客户端标识、使用住宅代理池。这既违反服务条款，也可能落入规避技术保护措施的更敏感地带。
4. 把软件分发给第三方，且用户需要自带账号凭据。这会额外触碰账号共享与转让类条款。

### 灰区

下列做法可行，但需要自律。

1. 第一类五个站点的批量检索与下载。这属于预期用途，灰区只在速率、并发，以及归档原图的版权含义上。
2. 下载原图到本地归档。技术上不违约，但下载不等于获得授权。给画师做参考通常可归入个人合理使用，但软件不应提供再分发、上传或公开展示功能。应在界面写明图片来源与版权归属，并提供一键跳转原帖。
3. Gelbooru 的接口密钥由用户自备属于正常路径。开发者内置共享密钥接近滥用。
4. 用 SauceNAO 与 IQDB 做溯源去重。这些服务各自有自动化规则，需要查阅其条款并限速。本次未核实。
5. 公开分发并内置成人内容开关。技术上属灰区，但应用商店审核是硬风险。误标内容会把风险传导给开发者。

### 明确不能做

- 收集用户账号密码后代存代登录。
- 伪造用户代理标识冒充浏览器或客户端。
- 绕过速率限制或封禁，包括多账号轮换。
- 转售或再分发任何站点的数据或服务本体。

## 以图搜源服务

下表为初步判断，尚未做专项调研。开工前需补一次，见 OQ-05。

| 服务 | 可编程性 | 优点 | 缺点 |
|---|---|---|---|
| IQDB | 表单提交加 HTML 解析，简单 | 无账号要求，动漫图片效果好 | 非动漫图片效果差，无官方接口 |
| ascii2d | 表单提交加 HTML 解析 | 无账号要求，支持多种检索模式 | 需要解析 HTML，有速率礼仪要求 |
| SauceNAO | 有接口，但免费额度极低 | 识别效果最好，含画师链接 | 限速严格，需要密钥 |
| Google 识图 | 无公开接口 | 覆盖面最广 | 程序化调用属灰色地带，极易失效 |
| Bing 识图 | 曾有付费接口 | 有官方渠道 | 现状需核实，可能有成本 |

建议第二期先做 IQDB 与 ascii2d。两者无需账号且成本低。Google、Bing 与 SauceNAO 作为可选插件。第一期完全不做。

## 实现代价与稳定性排序

| 目标 | 官方接口 | 需登录态 | 需逆向 | 首版工作量 | 稳定性 | 合规风险 | 建议 |
|---|---|---|---|---|---|---|---|
| Danbooru | 有 | 否 | 否 | 极小 | 高 | 低 | 第一期核心 |
| Safebooru | 有 | 否 | 否 | 极小 | 高 | 低 | 默认安全源 |
| yande.re | 有 | 否 | 否 | 小 | 中高 | 低 | 可配置附加 |
| Konachan | 有 | 否 | 否 | 小 | 中 | 低 | 可配置附加 |
| Gelbooru | 有 | 是，需密钥 | 否 | 小 | 中高 | 低 | 需处理密钥与四级分级 |
| ArtStation | 无 | 否 | 是 | 小 | 中 | 明确违反条款 | 不做自动抓取 |
| Pixiv | 无 | 是 | 是 | 大 | 低 | 明确违反条款 | 不做自动抓取 |

## 实测记录

本节记录开发期的实际网络探测结果。**早前「本机无网络出口」的结论已被推翻**，原因是当时的失败来自 TLS 与代理处理，不是没有出口。Python 一侧可以正常访问外部接口。

探测方式：用 `httpx` 直接请求，携带可识别的用户代理标识。探测脚本与原始输出见 `work/`。

### Danbooru 已实测可用

请求 `GET https://danbooru.donmai.us/posts.json`，参数 `tags=standing rating:general&limit=1`。

| 项 | 结果 |
|---|---|
| 状态码 | 200，`application/json; charset=utf-8` |
| 顶层结构 | 数组，每项一个作品 |
| 分级语法 | `rating:general` 生效，返回项 `rating` 为 `g` |
| 用户代理 | 携带自定义标识即可，未被拒绝 |

**返回字段（实测，共 40 余项）**：`id`、`rating`、`tag_string`、`image_width`、`image_height`、`file_url`、`large_file_url`、`preview_file_url`、`file_size`、`file_ext`、`md5`、`source`、`created_at`、`fav_count`、`score`、`is_deleted`、`is_banned`、`is_pending`、`is_flagged`、`has_large`、`media_asset` 等。

三条对实现有直接影响的结论。

1. **`file_url` 是可直接下载的地址**，指向 `cdn.donmai.us`，不需要额外请求换签。
2. **`is_deleted`、`is_banned`、`is_pending`、`is_flagged` 必须检查**，否则会显示和下载已失效的作品。
3. **`tag_string` 是空格分隔的完整标签串**，可以直接存入 `library_file.tag_string`，与表结构设计一致。

### Danbooru 的标签接口（已实测，含一个坑）

构建词典需要按名字查标签，这里实测出两条事实。

**`GET /tags.json?search[name]=名字` 是正确写法**，返回数组，需要再确认数组内有同名项。参数 `limit` 有效。

**`GET /tags/<名字>.json` 这个端点不存在**，对该路径的任何请求都返回 404，正文为 `ActiveRecord::RecordNotFound`。用错误路径做批量核验会把全部标签误判为不存在，本项目已经踩过一次：263 个标签全部被报为不存在。

另外，名称查询有两种写法都可用：`search[name]` 为精确匹配，`search[name_matches]` 为包含匹配。**核验必须用前者**，因为包含匹配会让查询 `standing` 时连带返回 `standing_on_one_leg`，把不存在的情况掩盖掉。

**`GET /wiki_pages/<名字>.json` 可用**，返回该标签的英文释义，例如 `1girl` 的释义为「An image containing one female character」。这对撰写与校对中文词条很有价值，可作为词典构建的辅助输入。

### Danbooru 的分级过滤语法（已实测，写法只有一种）

DD-21 决定成人内容开关关闭时请求 `general` 与 `sensitive` 两档。这需要确认请求怎么写，实测四种写法。

| 请求片段 | HTTP | 返回条数 | 实际取值 |
|---|---|---|---|
| `rating:g,s` | 200 | 200 | g、s 都有 |
| `rating:general rating:sensitive` | 200 | **0** | 无 |
| `rating:g` | 200 | 200 | 只有 g |
| `rating:g,s,q,e` | 200 | 200 | g、s、q、e 都有 |

三条结论。

1. **必须用简写形式，即同一前缀加逗号分隔的取值。** 两项分开写会返回 0 条，因为同前缀的两次限定在站内是取交集而不是并集，交集为空。因此简写不是风格偏好，而是唯一正确的写法。
2. **只请求 `rating:g` 会静默丢掉 `s` 档。** 实测 200 条里没有一条 `s`。这不报错，只是结果变少，因此漏写 `sensitive` 是很难自己发现的错误。
3. **简写形式可用。** 实测 200 条全部落在请求的取值范围内，未出现越档。

### Danbooru 的检索接口（已实测）

检索用 `GET /posts.json`，参数 `tags` 与 `limit`。实测结果如下。

| 项 | 结果 |
|---|---|
| 状态码 | 200，`application/json` |
| 顶层结构 | 数组，每项一个作品 |
| 速率响应头 | 未返回 `x-rate-limit`。9 次连续请求未触发限流 |
| 匿名可用 | 是，读取不需要密钥 |

**返回字段（实测）**：`id`、`rating`、`tag_string`、`image_width`、`image_height`、`file_size`、`file_ext`、`preview_file_url`、`large_file_url`、`file_url`、`source`、`md5`、`created_at`、`score`、`fav_count`、`tag_count`、`is_deleted`、`is_banned`、`is_pending`、`is_flagged`、`has_large`。

**三条影响实现的实测结论。**

一、**`limit=1000` 会被静默截到 200**。请求 1000 条实际返回 200 条，不报错。因此分页循环必须按实际返回条数判断是否还有下一页，不能信自己请求的条数。

二、**`is_pending` 占比可观**。200 条样本里有 22 条待审核，另有 3 条已封禁、1 条已标记、4 条没有任何图片地址。因此待审核与已标记**不能整类丢弃**，只标注即可；已删除与已封禁必须丢弃，没有任何图片地址的也必须丢弃。

三、**分页从第 100 页起返回 500**。正文为 `ActiveRecord::QueryCanceled`，即服务端查询超时。因此分页要有上限，本项目定在第 20 页。需要更深的结果时应改用编号游标 `id:1..100` 分批取。

**图片地址三级的实际含义。**

| 字段 | 实际尺寸 | 用途 |
|---|---|---|
| `preview_file_url` | `cdn.donmai.us/180x180/...` | 图墙缩略图 |
| `large_file_url` | `cdn.donmai.us/sample/...` | 查看大图 |
| `file_url` | `cdn.donmai.us/original/...` | 下载原图 |

**三个地址都可匿名下载**，实测预览图 7.6 KB、原图 167 KB 均返回 200。因此第一期下载层不需要登录。

**匿名检索一次最多两个标签（2026-10-07 实测）。**

这是本项目踩过的最贵的一个坑。

| 标签数 | 状态码 | 返回 |
|---|---|---|
| 1 | 200 | 正常 |
| 2 | 200 | 正常 |
| 3 | **422** | `PostQuery::TagLimitError`，正文写 `You cannot search for more than 2 tags at a time.` |
| 4 | **422** | 同上 |

因此检索式的默认标签数必须是 2。**原来的默认值是 3，任何能翻出三个以上标签的中文输入都会当场失败**，而错误信息是一段 JSON，界面把它原样显示出来，用户只会以为软件坏了。这一条是用户实测反馈「第一版问题太多」的主要原因之一。

登录之后上限会放宽，但本期不支持登录。检索层已经把这段 422 翻成一句中文说明，见 `DanbooruClient._explain_error`。

### Openverse 已实测可用
请求 `GET https://api.openverse.org/v1/images/`，参数 `q=standing&page_size=1`，匿名不需令牌。

| 项 | 结果 |
|---|---|
| 状态码 | 200，`application/json` |
| 顶层结构 | 对象，含 `result_count`、`page`、`page_count`、`page_size`、`results` |
| 匿名可用 | 是，未携带任何令牌 |

**`results` 项的关键字段**：`id`、`title`、`creator`、`creator_url`、`license`、`license_url`、`license_version`、`width`、`height`、`url`、`thumbnail`、`foreign_landing_url`、`detail_url`、`provider`、`source`、`tags`、`mature`、`filesize`、`filetype`、`attribution`、`unstable__sensitivity`。

两条实现要点。

1. **`license`、`license_url`、`attribution` 三项必须落库**，与 `library_file` 的授权字段一一对应。实测取到的一条为 `license` 为 `by-nc-sa`，`license_url` 指向 Creative Commons 页面。
2. **`result_count` 会给出命中总数**，可用于界面提示「共约多少条」，同时提醒用户分页深度受限。

### 大都会艺术博物馆：检索端点已于 2026-10-01 更换

**实测日期 2026-10-07。** 原先记录的检索端点已经退役，必须改用新地址。

| 端点 | 状态 | 说明 |
|---|---|---|
| `GET /public/collection/v1/search` | **410 已退役** | 退役日期 2026-10-01，返回体给出替代地址 |
| `GET /public/collection/v1.1/search` | 200 可用 | Elastic 支撑，支持 `offset` 与 `limit` 分页 |
| `GET /public/collection/v1/objects/<id>` | 200 可用 | 单体详情未变 |

**410 的返回体是机器可读的**，含 `message`、`replacement` 与 `documentation` 三个字段。原文写明旧端点由 Solr 支撑，一次返回全部命中编号，新端点由 Elastic 支撑并支持分页。

**新端点的返回值与旧端点不同。** 实测 `q=ukiyo-e` 加 `hasImages=true` 加 `limit=3`，返回对象只含两个字段。

| 字段 | 含义 |
|---|---|
| `total` | 命中总数，实测 194 |
| `objectIDs` | 本页的对象编号数组 |

因此要拿图片信息仍须按编号逐个请求详情端点。

**必须带自定义 User-Agent，否则一律 403。** 实测三组请求的差别只在请求头。

| 请求头 | 状态码 |
|---|---|
| 不携带 | 403，返回一页写着 `NOINDEX` 的 HTML |
| 用 httpx 默认的 `python-httpx/0.28.1` | 403，同上 |
| 自定义 `PicFinder/0.11 (dev test)` | 200 |

这条印证了 `source_site.user_agent_required` 这一列的设计。**凡是要接的站点都应显式设置请求头**，不要用客户端默认值。

**详情端点未变，字段与此前记录一致**：`objectID`、`isPublicDomain`、`primaryImage`、`primaryImageSmall`、`additionalImages`、`title`、`artistDisplayName`、`objectDate`、`medium`、`classification`、`department`、`culture`、`country`、`GalleryNumber`、`accessionNumber` 等。

三条实现要点。

1. **`isPublicDomain` 必须检查**，只有为真时才有明确的公共领域高分辨率图。
2. **`primaryImage` 与 `primaryImageSmall` 分别是原图与缩略图**，可用于图墙与下载两级。
3. **检索要走两步**，先取编号再取详情。这与 Danbooru 一次返回完整记录不同，客户端不能照抄。

### Hugging Face 仍不可达

`huggingface.co` 在命令环境中解析为 `127.0.0.1`，请求报证书校验失败。这与代理的 PAC 规则一致，不是站点故障。

因此 WD14 模型的文件清单与精确体积仍需开发者用浏览器查证。国内镜像 `modelscope.cn` 可达，返回 200，可作为模型下载的首选来源。

### 尚未实测

| 项 | 说明 |
|---|---|
| 各站速率限制的真实表现 | 未做长时间压测。Danbooru 的每秒 1 次建议仍按文档遵守 |
| Safebooru、yande.re、Konachan 的端点现状 | 未实测 |
| Gelbooru 的密钥申请与无密钥行为 | 未实测 |
| ArtStation 与 Pixiv 的私有端点 | 按 DD-03 不做自动抓取，无需实测 |
| Openverse 匿名额度的实际数值与中文检索质量 | 未实测。额度接口为 `GET /v1/rate_limit/` |
| 以图搜源服务 IQDB 与 ascii2d | 未实测，属第二期 |
| WD14 各版本的文件体积与推理耗时 | 未实测，Hugging Face 不可达 |

## 早前记录的必须实测项目

以下清单写在无法联网的时期，保留作为核对底稿。其中 Danbooru 与 Openverse 两项已由上一节实测完成，大都会博物馆的检索端点已更换，见上一节。

1. Danbooru 的实际返回字段，以及分级语法是否可用。在每秒 1 次请求下连续请求 10 分钟，观察是否被限流。
2. Safebooru、yande.re、Konachan 的端点是否仍返回 JSON，`limit` 上限是否仍如文档所述。
3. Gelbooru 的密钥申请流程与耗时。无密钥时是否真的被拒绝。
4. ArtStation 的真实请求信息，包括端点、查询名、所需请求头、是否需要跨站请求伪造令牌、是否匿名可读。
5. Pixiv 的检索参数，以及在非日本 IP 下是否真的被拦截。刷新令牌的实际有效期。
6. ArtStation 自 2025-04-06 起的新条款，是否新增自动化或人工智慧使用限制。
7. 以图搜源服务 IQDB 与 ascii2d 的表单参数与反爬现状。

## 时效性提示

**可确信的时间点。** Danbooru 服务条款为 2022-10-23。Safebooru 接口文档无日期。yande.re 接口 wiki 约 13 年前更新。Konachan 接口版本为 1.13.0+update.3。Gelbooru 接口 wiki 为 2022-02-22，分级 wiki 为 2023-07-21。两个 Pixiv Python 库的最后版本分别为 2022-02-03 与 2022-02-20。ArtStation 服务条款现状页包含自 2025-04-06 起的新条款，本次未读取。

**已过时或需复核。** Pixiv 应用接口的逆向文档约出自 2018 年。Pixiv 库的 Cloudflare 结论出自 2021 年，性能数据出自 2021 年之前。ArtStation 的全部社区逆向知识，其中影响最大的库已于 2019 年归档。

**方法论缺口。** 本次调研无法执行实时网络探测。部分域名在本机被解析为保留地址而不可直连。检索额度在调研中途耗尽，导致 Pixiv 检索参数与 Pixiv 条款原文两项未完成。

因此，所有关于当前仍可用的结论，均为文档与二手证据推断，不是实测结果。

## 由此导出的设计决策

| 决策 | 内容 |
|---|---|
| DD-03 | Danbooru 系做自动检索核心；Pixiv 与 ArtStation 降级为一键跳转官网检索 |
| DD-12 | 分级过滤参数化，默认收紧；`rating:safe` 不可用，须改用 `rating:general` |
| N-04 | 每站独立限速 |
| N-05 | 使用可识别的自定义用户代理标识，禁止冒充浏览器 |
| DD-16 | 不绕过任何反爬机制，不代持账号，不分发任何站点数据 |
