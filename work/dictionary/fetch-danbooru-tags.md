# PIC FINDER Danbooru 标签抓取工具

本脚本从 Danbooru 抓取标签列表，用于构建本地词典。它对应 `docs/dictionary-format.md` 冷启动流程的第一步。

抓取结果写入 `work/dictionary/tags-danbooru.json`，再由人工或接口补上中文说法。

## 为什么要单独一个脚本

开发机的命令执行环境没有网络出口，无法在开发阶段调用外部接口。因此这一步交给开发者在本机执行，结果作为词典构建的输入。

## 用法

在 `pic-finder` 目录下执行。

```powershell
# 先探测接口返回结构。这一步只取一条，用于确认字段名。
python work\dictionary\fetch_danbooru_tags.py --probe

# 抓取标签。按顺序抓元标签、作品、角色、通用、画师五类。
python work\dictionary\fetch_danbooru_tags.py --limit 5000

# 只抓通用标签（画师参考最常用的那一类）
python work\dictionary\fetch_danbooru_tags.py --category 0 --limit 2000

# 指定输出文件
python work\dictionary\fetch_danbooru_tags.py --output work\dictionary\tags-pose.json
```

## 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--limit` | 2000 | 抓取条数上限 |
| `--category` | 不限制 | 标签类别。0 为通用，1 为画师，3 为作品，4 为角色，5 为元标签 |
| `--order` | `count` | 排序字段，即按使用次数降序 |
| `--output` | `work/dictionary/tags-danbooru.json` | 输出文件 |
| `--probe` | 关闭 | 只取一条并打印返回结构，不写文件 |
| `--page-size` | 1000 | 每次请求的条数，接口上限为 1000 |
| `--delay` | 1.0 | 每次请求之间的间隔秒数。官方要求长会话控制在每秒 1 次以内，不要调小 |

## 关于礼节

Danbooru 官方对自动化访问有明确要求，本脚本遵守三条。

1. 携带可识别的用户代理标识，不冒充浏览器。标识形如 `PicFinder/0.1 (personal reference tool)`。
2. 每次请求间隔默认 1 秒，符合官方对长会话的建议。
3. 不并发请求，不绕过任何限制。

接口文档地址为 <https://danbooru.donmai.us/wiki_pages/help:api>。

## 输出格式

```json
{
  "fetched_at": "2026-10-07T12:00:00Z",
  "source": "danbooru",
  "order": "count",
  "limit": 2000,
  "count": 2000,
  "tags": [
    { "name": "1girl", "category": 0, "post_count": 5432100 },
    { "name": "solo", "category": 0, "post_count": 4321000 }
  ]
}
```

抓取之后，用 `work/dictionary/build_dictionary_scaffold.py` 把它转换成待校对的词典骨架。
