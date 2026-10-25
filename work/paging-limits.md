# 分页能力上限实测

本文回答一个问题：一个标签下有几千甚至几万张图时，我们最多能翻到多少张。

实测日期为 2026-10-08。实测脚本为 [probe_paging.py](probe_paging.py)，另有六个补测脚本，见文末文件清单。

所有结论都有原始证据。原始证据写在 `work/paging-evidence*.jsonl` 与 `work/paging-raw*.txt` 里，本文只摘关键行。

## 一句话结论

Danbooru 系可以取到 **20 万张**量级，Openverse 匿名只能取 **240 张**。

分五种情况。

1. Danbooru 有固定页数上限，最多 1,000 页，乘上每页 200 条，即 200,000 张。
2. Safebooru 限制的是偏移量，上限 200,000，加最后一页约 201,000 张。
3. yande.re 与 Konachan 没有页数上限。实测把 `swimsuit` 标签的 138,479 张全部翻完。
4. Openverse 匿名的分页深度上限是 240，即最多 240 张。
5. Gelbooru 未测到。不带密钥一律返回 401。

用户指定的检索词 `oguri_cap_(umamusume)` 在 Danbooru 上有 6,893 张，全部可翻完。这个数字由计数端点与翻页末尾两路证据互相印证。

## 实测方法

探测方式为 `httpx` 直接请求，携带可识别的用户代理标识。

| 项 | 取值 |
|---|---|
| 用户代理 | `PicFinder/0.15 (paging limit probe; personal single-user test)` |
| 请求间隔 | 每站串行，Danbooru 系 1.05 秒一次，Openverse 与博物馆 0.6 秒一次 |
| 网络重试 | 异常重试 3 次，退避 2 秒、4 秒 |
| 状态码处理 | 站点返回的 4xx 与 5xx 原样记录，不重试、不掩盖 |
| 边界定位 | 先按页号倍增，再在可用页与失败页之间二分 |

边界必须真的发请求翻到。本文所有页数都来自实测响应，没有一处是推算。

## 汇总表

| 站点 | 接口 | 分页参数 | 每页上限 | 最大页数 | 单检索词上限 | 是否触发限流 |
|---|---|---|---|---|---|---|
| Danbooru | `GET /posts.json` | `page` | 200 | 1,000，第 1,001 页起 410 | 200,000 张 | 未触发，12 次连发全部 200 |
| Safebooru | `GET /index.php?page=dapi&s=post&q=index&json=1` | `pid`，从 0 起 | 1,000 | 无固定页数，`pid × limit ≤ 200,000` | 约 201,000 张 | 未触发，越界返回 XML 而非 429 |
| Gelbooru | 与 Safebooru 同族的 `index.php` | `pid` | 测不到 | 测不到 | 测不到 | 测不到，接口一律 401 |
| yande.re | `GET /post.json?api_version=2` | `page` | 1,000 | 无上限，翻到结果末尾为止 | 无上限，实测取完 138,479 张 | 未触发 |
| Konachan | `GET https://konachan.net/post.json` | `page` | 1,000 | 无上限 | 无上限，实测取完 11,958 张 | 未触发，`konachan.com` 被 Cloudflare 拦 |
| Openverse | `GET /v1/images/` | `page` 与 `page_size` | 20 | 深度上限 240，每页 20 时即 12 页 | 240 张 | 偶发 429，正文是 Cloudflare 挑战页 |
| 大都会艺术博物馆 | `GET /public/collection/v1.1/search` | `offset` | 500 | 未探到硬上限 | 未探到硬上限 | 未触发 |

表中的每页上限都用三个大于上限的请求值确认过。同一个数连续出现三次，说明是被服务端裁剪，而不是结果不够。

## 逐站证据

### Danbooru

**每页上限 200。** 请求 `limit=1000`、`2000`、`5000`，三次都返回 200 条。

```
[danbooru] 每页上限 标签=swimsuit limit=1000 page=1 -> HTTP 200 条数=200 字节=661619
[danbooru] 每页上限 标签=swimsuit limit=2000 page=1 -> HTTP 200 条数=200 字节=661619
[danbooru] 每页上限 标签=swimsuit limit=5000 page=1 -> HTTP 200 条数=200 字节=661619
```

**页码上限 1,000。** 倍增到第 1,024 页失败，二分定位到第 1,000 页可用、第 1,001 页返回 410。

```
[danbooru] 平台页上限 标签=1girl limit=200 page=1000 -> HTTP 200 条数=200
[danbooru] 平台页上限 标签=1girl limit=200 page=1001 -> HTTP 410
```

410 的正文原文如下。

```json
{"success":false,"error":"PaginationExtension::PaginationError","message":"You cannot go beyond page 1000.","backtrace":["app/logical/pagination_extension.rb:54:in 'PaginationExtension#paginate'", ...]}
```

**上限卡的是页号，不是偏移量。** 把每页压到 1 条再翻一遍，同样停在第 1,000 页。

```
[danbooru] limit=1 页码上限 标签=1girl limit=1 page=1000 -> HTTP 200 条数=1
[danbooru] limit=1 页码上限 标签=1girl limit=1 page=1001 -> HTTP 410
```

因此每页 1 条只能取 1,000 张，每页 200 条能取 200,000 张。**缩小每页对翻深没有帮助。**

**单检索词上限由此得出 1,000 × 200 = 200,000 张。**

**指定检索词的实测。** `oguri_cap_(umamusume)` 每页 200 条时，第 34 页满 200 条，第 35 页 93 条，第 36 页为空。总数即 34 × 200 + 93 = 6,893 张，与计数端点完全一致。

```
[danbooru] 总数 计数端点 https://danbooru.donmai.us/counts/posts.json 参数 {'tags': 'oguri_cap_(umamusume)'} -> HTTP 200 正文={"counts":{"posts":6893}}
[danbooru] 页码上限 标签=oguri_cap_(umamusume) limit=200 page=34 -> HTTP 200 条数=200
[danbooru] 页码上限 标签=oguri_cap_(umamusume) limit=200 page=35 -> HTTP 200 条数=93
[danbooru] 页码上限 标签=oguri_cap_(umamusume) limit=200 page=36 -> HTTP 200 条数=0
```

对比之下，`1girl` 有 8,513,594 张，页数上限只让我们取到 200,000 张，约占 2.3%。

```
{"counts":{"posts":8513594}}
```

**限流。** 12 次不留间隔的连发全部 200，未出现 429，也未返回 `x-rate-limit` 响应头。按官方每秒 1 次的建议执行即可。

### Safebooru

**每页上限 1,000。** 请求 `limit=1000`、`2000`、`5000`，三次都返回 1,000 条。

```
[safebooru] 每页上限 标签=swimsuit limit=1000 pid=1 -> HTTP 200 条数=1000 字节=1227335
[safebooru] 每页上限 标签=swimsuit limit=2000 pid=1 -> HTTP 200 条数=1000 字节=1227335
[safebooru] 每页上限 标签=swimsuit limit=5000 pid=1 -> HTTP 200 条数=1000 字节=1227335
```

**上限卡的是偏移量 `pid × limit`，上限 200,000。** 三组证据互相印证。

第一组，每页 100 条时 `pid=2000` 可用、`pid=2001` 越界。

```
[safebooru] 平台页上限 标签=1girl limit=100 pid=2000 -> HTTP 200 条数=100
[safebooru] 平台页上限 标签=1girl limit=100 pid=2001 -> HTTP 200 条数=0
```

第二组，每页 1 条时 `pid=200000` 可用、`pid=200001` 越界。注意这里的 `pid` 是 200,000，说明限制不是 `pid` 本身，而是它乘上 `limit`。

```
[safebooru] limit=1 pid=200000 -> HTTP 200 条数=1
[safebooru] limit=1 pid=200001 -> HTTP 200 条数=None
```

第三组，每页 1,000 条时 `pid=200`（偏移 200,000）可用、`pid=201` 越界。

```
[safebooru] limit=1000 pid=200 -> HTTP 200 条数=1000
[safebooru] limit=1000 pid=201 -> HTTP 200 条数=None
```

三组越界响应都取不到条数，原因是正文不是 JSON 数组，而是下面那段 XML。
**越界返回的不是 429，也不是空数组。** 它是 HTTP 200 加一段 XML。这条必须写明，否则很容易把越界误判成被限流。

```xml
<?xml version="1.0" encoding="UTF-8"?>
        <response success="false" reason="Search error: API limited due to abuse."/>
```

**`pid` 从 0 开始。** `pid=0` 与省略 `pid` 返回同一条记录，`pid=1` 是第二条。

**单检索词上限。** 每页 1,000 条时可用 `pid` 为 0 至 200，共 201 页，即 201,000 张。

**限流。** 12 次连发全部 200。因为越界信息里带 `abuse` 字样，这里特别说明：那段 XML 只在偏移量越界时出现，连续正常请求从未触发。

### Gelbooru

**接口一律返回 401，正文为空。** 这一轮没有真实密钥，因此分页能力测不到。

```
[gelbooru] 密钥要求 不带密钥：tags=oguri_cap_(umamusume) -> HTTP 401 字节=0
[gelbooru] 密钥要求 带格式合法的假 api_key 与 user_id：tags=oguri_cap_(umamusume) -> HTTP 401 字节=0
[gelbooru] 连通性 标签=1girl limit=1 pid=1 -> HTTP 401 字节=0
```

**这 401 来自站点，不是本机代理。** 判据是同一个域名的网页端正常返回 200。

```
[gelbooru] 首页 -> HTTP 200 字节=3383 content-type=text/html
[gelbooru] 网页检索页 page=post&s=list&tags=1girl -> HTTP 200 字节=94304
```

因此结论是：Gelbooru 的接口确实要求 `api_key` 与 `user_id`，本轮无密钥，分页参数、每页上限、页码上限三项全部**测不到**。

### yande.re

**每页上限 1,000，不是文档写的 100。** 请求 `limit=1000`、`2000`、`5000`，三次都返回 1,000 条。

```
[yandere] 每页上限 标签=swimsuit limit=1000 page=1 -> HTTP 200 条数=1000 字节=1679934
[yandere] 每页上限 标签=swimsuit limit=2000 page=1 -> HTTP 200 条数=1000 字节=1679934
[yandere] 每页上限 标签=swimsuit limit=5000 page=1 -> HTTP 200 条数=1000 字节=1679934
```

这一条推翻了 `docs/feasibility.md` 里「`limit` 取值范围为 1 至 100」的记录。按 100 取图会白白多花 10 倍请求。

**没有页码上限。** 每页 100 条时，第 1,385 页返回 79 条，第 1,386 页为空。1,384 × 100 + 79 = 138,479 张，全部可取。

```
[yandere] 平台页上限 标签=swimsuit limit=100 page=1024 -> HTTP 200 条数=100
[yandere] 平台页上限 标签=swimsuit limit=100 page=2048 -> HTTP 200 条数=0
[yandere] 平台页上限 标签=swimsuit limit=100 page=1385 -> HTTP 200 条数=79
[yandere] 平台页上限（边界确认） 标签=swimsuit limit=100 page=1385 -> HTTP 200 条数=79
[yandere] 平台页上限（边界确认） 标签=swimsuit limit=100 page=1386 -> HTTP 200 条数=0
```

每页压到 1 条时，正好停在 138,479，与总数吻合。这证明限制只来自结果总数，不来自页号。

```
[yandere] limit=1 页码上限 标签=swimsuit limit=1 page=131072 -> HTTP 200 条数=1
[yandere] limit=1 页码上限 标签=swimsuit limit=1 page=138479 -> HTTP 200 条数=1
```

越界的返回体是 `{"posts":[]}`，HTTP 200，不报错。

**限流。** 12 次连发全部 200。

#### yande.re 的一个异常，需要单独记一笔

`1girl`、`long_hair`、`blue_eyes`、`hair`、`girls`、`2girls` 这些标签在 yande.re 上返回空数组，而 `swimsuit`、`umamusume`、`thighhighs`、`cat`、`blue` 正常。

```
[yandere] 大标签候选 标签=1girl limit=1 page=1 -> HTTP 200 条数=0 正文={"posts":[]}
[yandere] 大标签候选 标签=long_hair limit=1 page=1 -> HTTP 200 条数=0
[yandere] 大标签候选 标签=hair limit=1 page=1 -> HTTP 200 条数=0
[yandere] 大标签候选 标签=swimsuit limit=1 page=1 -> HTTP 200 条数=1
[yandere] 大标签候选 标签=umamusume limit=1 page=1 -> HTTP 200 条数=1
```

追查脚本 `probe_yandere_note.py` 与 `probe_yandere_note2.py` 排除了三种解释：不是本地缓存，不是限流，也不是瞬时故障。同一轮里紧挨着的两个标签，一个空一个有结果。`tag.json` 同步查证，`name=girl` 能列出大量含 `girl` 的标签，`name=1girl` 却返回空数组。

**本轮的探测预算不足以定论原因，因此这里只报告现象。** 工程上的含义很明确：yande.re 存在「标签检索不到结果」的风险，代码不能把空结果当成「该站没有这张图」。

### Konachan

**`konachan.com` 一律 403。** 正文是 Cloudflare 的挑战页，重试无效。

```
[konachan] 连通性 标签=oguri_cap_(umamusume) limit=100 page=1 -> HTTP 403 server=cloudflare
    正文：<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>...
```

**`konachan.net` 同一个接口正常可用**，返回结构与 Moebooru 一致。

```
[konachan_net] 连通性 标签=oguri_cap_(umamusume) limit=100 page=1 -> HTTP 200 条数=4 字节=8762
```

本项目 `source_site` 表里配的是 `https://konachan.com`。按本轮实测，这个地址在本机不可用，换 `konachan.net` 即可，接口路径与参数完全相同。

**每页上限 1,000。** 三个大于上限的请求值都返回 1,000 条。

```
[konachan_net] 每页上限 标签=swimsuit limit=1000 page=1 -> HTTP 200 条数=1000 字节=2003631
[konachan_net] 每页上限 标签=swimsuit limit=2000 page=1 -> HTTP 200 条数=1000 字节=2003631
[konachan_net] 每页上限 标签=swimsuit limit=5000 page=1 -> HTTP 200 条数=1000 字节=2003631
```

**没有页码上限。** 每页 100 条时第 120 页返回 58 条，第 121 页为空。119 × 100 + 58 = 11,958 张。

```
[konachan_net] 平台页上限 标签=swimsuit limit=100 page=120 -> HTTP 200 条数=58
[konachan_net] 平台页上限 标签=swimsuit limit=100 page=121 -> HTTP 200 条数=0
[konachan_net] limit=1 页码上限 标签=swimsuit limit=1 page=11958 -> HTTP 200 条数=1
[konachan_net] limit=1 页码上限 标签=swimsuit limit=1 page=11959 -> HTTP 200 条数=0
```

两个路数算出的总数一致，证明限制只来自结果总数。

**限流。** 12 次连发全部 200。

### Openverse

**`page_size` 匿名的上限是 20。** 请求 100 与 500 都返回 401。

```
[openverse] 每页上限 标签=flower limit=100 page=1 -> HTTP 401 正文={"detail":"page_size may not exceed 20 for anonymous requests"}
[openverse] 每页上限 标签=flower limit=500 page=1 -> HTTP 401 正文={"detail":"page_size may not exceed 20 for anonymous requests"}
```

**分页深度上限 240。** 每页 20 条时第 12 页可用，第 13 页返回 401。

```
[openverse] 页码上限 标签=flower limit=20 page=12 -> HTTP 200 条数=20
[openverse] 页码上限 标签=flower limit=20 page=13 -> HTTP 401
    正文：{"detail":"pagination depth may not exceed 240 for anonymous requests"}
[openverse] 页码上限（边界确认） 标签=flower limit=20 page=12 -> HTTP 200 条数=20
[openverse] 页码上限（边界确认） 标签=flower limit=20 page=13 -> HTTP 401
```

**上限卡的是深度，也就是 `page × page_size`。** 每页压到 1 条时，可用页号一路涨到 240。

```
[openverse] limit=1 页码上限 标签=flower limit=1 page=240 -> HTTP 200 条数=1
[openverse] limit=1 页码上限 标签=flower limit=1 page=241 -> HTTP 401
    正文：{"detail":"pagination depth may not exceed 240 for anonymous requests"}
```

因此 Openverse 匿名的单检索词上限就是 **240 张**，与每页取多少条无关。

**检索词。** `oguri_cap_(umamusume)` 返回 0 条。Openverse 的 `q` 是自由文本检索，不是标签体系，因此改用 `flower`。

```
[openverse] 连通性 标签=oguri_cap_(umamusume) limit=20 page=1 -> HTTP 200 条数=0
[openverse] 大标签候选 标签=flower limit=1 page=1 -> HTTP 200 条数=1
```

**限流。** 声明额度写在响应头里。

```
x-ratelimit-limit-anon_burst: 20/min
x-ratelimit-limit-anon_sustained: 200/day
```

额度端点本身不给数字。

```json
{"requests_this_minute":null,"requests_today":null,"rate_limit_model":"standard","verified":false}
```

**429 的表现与恢复时间。** 主轮实测中出现过 HTTP 429，但没有 `Retry-After` 头，正文是 Cloudflare 的挑战页。

```
[openverse] limit=1 页码上限 标签=flower limit=1 page=241 -> HTTP 429 正文=<!DOCTYPE html>...<title>Just a moment...</title>...
```

恢复时间：脚本退避 30 秒后重试，同一条请求随即成功。同一页号在前后两次测量中先 200、后 429、重试又 200，说明它是**偶发**，不是稳定的额度耗尽。

补测脚本 `probe_openverse_429.py` 连发 30 次都没能复现 429。

```
连发第 30 次 HTTP 200 剩余额度=19 额度上限=20/min Retry-After=None
30 次连发未触发 429，无法测恢复时间
```

注意「剩余额度」始终显示 19，没有随请求递减。因此这个头不能当作实时余额使用。

**工程结论：** Openverse 的真实瓶颈是深度 240，不是 429。按 20 次每分钟的声明额度，把间隔放到 3 秒以上即可。

### 大都会艺术博物馆

这个站没有标签体系，检索词是自由文本。`oguri_cap_(umamusume)` 返回 0 条，改用 `cat`。

**每页上限 500。** 请求 1,000 返回 500 条，请求 200 返回 200 条。

```
[metmuseum] 每页上限 标签=cat limit=200 offset=0 -> HTTP 200 条数=200
[metmuseum] 每页上限 标签=cat limit=1000 offset=0 -> HTTP 200 条数=500
```

**分页参数是 `offset`，按条偏移而不是页码。** 每页 100 条时第 16 页可用，第 17 页为空，即该检索词约 1,500 条。每页 1 条时探到 `offset=256` 仍可用，未探到硬上限。

**返回体只含编号。** 每页 100 条约 722 字节，平均每条 7 字节，说明正文只有 `objectIDs`。要取图片信息必须逐个再请求详情端点，抓取代价与其它站不可比。

## 6,000 张量级的抓取代价

以 6,000 张为目标，每站的实际代价如下。

| 站点 | 每页条数 | 请求数 | 单页耗时 | 总耗时 | 响应数据量 |
|---|---|---|---|---|---|
| Danbooru | 200 | 30 | 约 2.98 秒 | 约 1 分 29 秒 | 约 21.5 MB |
| Safebooru | 1,000 | 6 | 约 2.48 秒 | 约 15 秒 | 约 7.4 MB |
| yande.re | 1,000 | 6 | 约 1.82 秒 | 约 11 秒 | 约 10.1 MB |
| Konachan | 1,000 | 6 | 约 2.40 秒 | 约 15 秒 | 约 12.0 MB |
| Openverse | 20 | 12 | 约 2.15 秒 | 约 26 秒 | 约 0.3 MB |
| Gelbooru | 未测到 | 未测到 | 未测到 | 未测到 | 未测到 |

计算口径如下。

1. 单页耗时 = 实测的服务端响应时间加限速等待。限速按每秒 1 次计，即每页多等 1 秒。
2. 实测的平均服务端响应时间：Danbooru 每页 200 条约 1,980 毫秒，Safebooru 每页 100 条约 855 毫秒，yande.re 每页 100 条约 957 毫秒，Konachan 每页 100 条约 692 毫秒，Openverse 每页 20 条约 1,549 毫秒。
3. 数据量 = 请求数乘每页实测字节数。Danbooru 每页 200 条为 716,602 字节，Safebooru 每页 1,000 条为 1,227,335 字节，yande.re 为 1,679,934 字节，Konachan 为 2,003,631 字节，Openverse 每页 20 条为 25,069 字节。
4. 这 6,000 张只是元数据。真正的图片要另外下载，体积大两到三个数量级。

三条要点。

1. **每页条数决定一切。** Safebooru 与 yande.re 从文档记的 100 提到实测的 1,000，请求数直接少 90%。Danbooru 只能 200，所以请求数最多。
2. **耗时主要花在限速上，不是网络上。** 四站的响应时间大多在 0.7 至 2.0 秒，限速每秒 1 次是硬成本。想再快只能提高并发，但那会碰站点规则。
3. **Openverse 取不到 6,000 张。** 上限 240，12 页就到底了。

以 Danbooru 的 `oguri_cap_(umamusume)` 为例，6,893 张需要 35 页，与上表口径一致。

## 按可行性排序的建议

### 一、可以真的全量取

**yande.re 与 Konachan（用 `konachan.net`）。**

两站没有页数上限，实测把一个 13.8 万张的标签完整翻到底。每页 1,000 条，抓 6,000 张只要 6 次请求。

建议把上限设在「结果总数」而不是页数。仍需保留一个保险值，例如单次检索最多 50,000 张，防止用户误触超大标签。

**Danbooru。**

上限硬性存在。单检索词最多 200,000 张，即 1,000 页乘 200 条。

建议把 `client.py` 的 `MAX_PAGES` 由 20 提到 1,000，并把 410 单独翻译成一句中文：「该检索词结果过多，只能取到前 20 万张」。当前代码把第 20 页当上限，只能取 4,000 张，丢掉了 98% 的可用结果。

### 二、能取，但必须设上限

**Safebooru。**

偏移量上限 200,000。每页 1,000 条时可用 201 页，约 201,000 张。

建议每页按 1,000 取，上限设 201 页。越界时要认得那段 XML，把它翻译成「已到本站可取的上限」，不要显示成 `abuse`，否则用户会以为被封了。

**大都会艺术博物馆。**

每页上限 500，分页按偏移量。它的检索响应只有编号，取一批图要发两轮请求，远比其它站贵。

建议只作为道具与服装类的补充源，上限设在几百条，不做全量。

### 三、只能取很少

**Openverse。**

匿名上限 240 张，与每页条数无关。这是一个授权底座，不是一个图库。

建议把它定位成「补充一小批开放授权图」，上限就设 240。界面上要明说这个数字，避免用户以为软件坏了。若将来注册账号，需要重新实测深度上限。

### 四、当前不可用

**Gelbooru。**

接口必须带 `api_key` 与 `user_id`，本轮没有密钥。注册后必须补测三项：`pid` 上限、`limit` 上限、是否与 Safebooru 同族规则。

**Konachan 的 `konachan.com` 域名。**

被 Cloudflare 挑战页拦住，换 `konachan.net` 即可。接口路径、参数、返回结构完全相同。本轮未改动 `src/`，建议由开发者决定是改配置还是做双域名回退。

### 五、对实现的直接影响

1. 现有 `DanbooruClient` 只支持 `page` 与 `limit`。Safebooru、Gelbooru、yande.re、Konachan 四站尚无客户端，`create_client` 会抛错。
2. Moebooru 系（yande.re 与 Konachan）的返回结构是 `{"posts": [...]}`，不是数组。写客户端时不能照抄 Danbooru 的解析。
3. 各站的「到底了」表现各不相同：Danbooru 是 410，Openverse 是 401，Safebooru 越界是 XML，yande.re 与 Konachan 是空数组。四类都要各自处理。
4. 「空数组」与「出错」必须分开。空数组是正常的翻页终点，出错要提示用户。

## 测不到的部分与原因

| 项 | 状态 | 卡在哪里 |
|---|---|---|
| Gelbooru 的分页参数、每页上限、页码上限 | 测不到 | 接口一律 401，正文为空，需要真实 `api_key` 与 `user_id` |
| yande.re 部分标签返回空的原因 | 未定论 | 已排除缓存、限流、瞬时故障三种解释，探测预算不足以定论 |
| Openverse 429 的稳定触发条件 | 未复现 | 主轮偶发数次，补测连发 30 次未复现，响应头里的剩余额度不递减 |
| Openverse 登录后的深度上限 | 未测 | 本轮只测匿名 |
| 各站的长时间压测 | 未做 | 只做了 12 次连发。Danbooru 的每秒 1 次建议仍按文档遵守 |
| Danbooru 第 100 页返回 500 的旧记录 | 未复现 | 本轮同一标签翻到第 1,000 页都正常，旧记录可能是特定标签的查询超时 |
| Konachan 的 `konachan.com` 是否长期不可用 | 未定论 | 本轮每次都被 Cloudflare 挑战，但可能是本机链路问题 |
| 大都会艺术博物馆的偏移量硬上限 | 未探到 | 探到 `offset=256` 仍可用，该参数的实际上限未继续追 |

关于旧记录，`docs/feasibility.md` 写「分页从第 100 页起返回 500」，`client.py` 据此把上限定在 20 页。本轮实测同一站点可以正常翻到第 1,000 页。旧结论很可能是当时的网络环境造成的，建议以本轮实测为准，并保留一条兜底：遇到 5xx 时按可重试处理。

## 文件清单

实测脚本，都在 `work/` 下。

| 文件 | 用途 |
|---|---|
| [probe_paging.py](probe_paging.py) | 主脚本。连通性、每页上限、页码边界、限流连发 |
| [probe_edges.py](probe_edges.py) | 边界补测。`pid` 起始值、偏移量规则、Gelbooru 归属判定、410 原文 |
| [probe_page_bytes.py](probe_page_bytes.py) | 每页响应的实际字节数 |
| [probe_openverse_429.py](probe_openverse_429.py) | Openverse 的 429 触发与恢复 |
| [probe_yandere_note.py](probe_yandere_note.py) | yande.re 空结果追查，第一轮 |
| [probe_yandere_note2.py](probe_yandere_note2.py) | yande.re 空结果追查，第二轮，含响应头 |
| [probe_konachan_alt.py](probe_konachan_alt.py) | Konachan 替代入口追查 |

原始证据。

| 文件 | 内容 |
|---|---|
| `paging-evidence-main.jsonl` | 主轮，299 条请求 |
| `paging-raw-main.txt` | 主轮的可读版本 |
| `paging-summary-main.json` | 主轮的汇总结论 |
| `paging-evidence-deep.jsonl` | 深页补测，含 yande.re 与 Konachan 的完整末页 |
| `paging-raw-deep.txt`、`paging-summary-deep.json` | 深页补测的可读版本与结论 |
| `paging-raw-ceiling.txt`、`paging-summary-ceiling.json` | 每页上限复测的可读版本与结论 |
| `paging-raw-counts.txt`、`paging-summary-counts.json` | 总数端点的可读版本与结论 |
| `paging-evidence-ceiling.jsonl` | 每页上限复测，改正了第一轮的误判 |
| `paging-evidence-counts.jsonl` | 总数端点 |
| `paging-evidence.jsonl`、`paging-raw.txt` | 最后一轮的副本 |
| `paging-edges.txt` | 边界补测原始输出 |
| `paging-page-bytes.txt`、`paging-page-bytes.json` | 每页字节数 |
| `paging-openverse-429.txt` | 429 触发与恢复 |
| `paging-yandere-note.txt`、`paging-yandere-note2.txt` | yande.re 空结果追查 |
| `paging-konachan-note.txt` | Konachan 替代入口 |

复现主轮实测的命令如下。

```powershell
cd D:\WORK\DSHWorkplace\pic-finder
C:\PYthon313\python.exe work\probe_paging.py --run-name main
C:\PYthon313\python.exe work\probe_paging.py --only safebooru,yandere,konachan_net `
    --run-name deep --limit-probes 1000,5000 --max-page 400000
C:\PYthon313\python.exe work\probe_paging.py --only danbooru,openverse,metmuseum `
    --counts-only --run-name counts
```

第一轮的每页上限探测用过小的标签，把标签自己的结果数误判成上限。脚本已改为固定用大标签，并加了一条提示。表中数字取自改正后的复测结果。
