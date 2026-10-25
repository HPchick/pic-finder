"""图库索引数据库的连接、建库与读取。

表结构以字符串常量嵌在本模块里，不从磁盘读 SQL 文件。理由有二。

一、打包成单目录程序后，额外的 SQL 文件容易被漏掉，而表结构缺失会导致启动失败。
二、程序在运行期不应依赖开发目录的文件布局。

表结构的设计理由见 docs/database-design.md。这里是它的运行时副本，两者必须一致。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import logging_setup

SCHEMA_VERSION = 1

logger = logging_setup.get("database")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS content_category (
    id         INTEGER PRIMARY KEY,
    slug       TEXT NOT NULL UNIQUE,
    name_zh    TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
) STRICT;

CREATE TABLE IF NOT EXISTS source_site (
    id                   INTEGER PRIMARY KEY,
    slug                 TEXT NOT NULL UNIQUE,
    name                 TEXT NOT NULL,
    base_url             TEXT NOT NULL,
    api_kind             TEXT NOT NULL CHECK (api_kind IN
                             ('danbooru', 'moebooru', 'gelbooru', 'openverse', 'museum')),
    enabled              INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    requires_key         INTEGER NOT NULL DEFAULT 0 CHECK (requires_key IN (0, 1)),
    rate_limit_per_sec   REAL,
    rate_limit_note      TEXT,
    user_agent_required  INTEGER NOT NULL DEFAULT 0 CHECK (user_agent_required IN (0, 1)),
    license_kind         TEXT NOT NULL DEFAULT 'unknown'
                             CHECK (license_kind IN ('open', 'site', 'unknown')),
    notes                TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS artist (
    id                INTEGER PRIMARY KEY,
    source_site_id    INTEGER NOT NULL REFERENCES source_site (id),
    name              TEXT NOT NULL,
    name_normalized   TEXT NOT NULL,
    profile_url       TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    UNIQUE (source_site_id, name_normalized)
) STRICT;

CREATE TABLE IF NOT EXISTS library_file (
    id                 INTEGER PRIMARY KEY,
    source_site_id     INTEGER NOT NULL REFERENCES source_site (id),
    post_id            TEXT NOT NULL,
    post_key           TEXT NOT NULL UNIQUE,
    post_url           TEXT,
    artist_id          INTEGER REFERENCES artist (id),
    artist_name_raw    TEXT,
    content_category_id INTEGER REFERENCES content_category (id),
    title              TEXT,
    rating             TEXT CHECK (rating IS NULL OR rating IN ('g', 's', 'q', 'e', 'unknown')),
    is_mature          INTEGER NOT NULL DEFAULT 0 CHECK (is_mature IN (0, 1)),
    width              INTEGER,
    height             INTEGER,
    pixel_count        INTEGER GENERATED ALWAYS AS (
                           CASE WHEN width IS NULL OR height IS NULL THEN NULL
                                ELSE width * height END
                       ) STORED,
    file_size          INTEGER,
    file_ext           TEXT,
    file_hash          TEXT,
    file_hash_algo     TEXT CHECK (file_hash_algo IS NULL OR file_hash_algo IN ('sha256', 'md5')),
    orig_url           TEXT,
    rel_path           TEXT NOT NULL,
    abs_path_hint      TEXT,
    tag_string         TEXT NOT NULL DEFAULT '',
    tag_count          INTEGER NOT NULL DEFAULT 0,
    license            TEXT,
    license_url        TEXT,
    attribution        TEXT,
    source_created_at  TEXT,
    downloaded_at      TEXT NOT NULL,
    archived_at        TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    missing_at         TEXT,
    UNIQUE (rel_path)
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_library_file_hash
    ON library_file (file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_library_file_site_post ON library_file (source_site_id, post_id);
CREATE INDEX IF NOT EXISTS idx_library_file_artist ON library_file (artist_id);
CREATE INDEX IF NOT EXISTS idx_library_file_category ON library_file (content_category_id);
CREATE INDEX IF NOT EXISTS idx_library_file_rating ON library_file (rating);
CREATE INDEX IF NOT EXISTS idx_library_file_downloaded ON library_file (downloaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_library_file_pixels ON library_file (pixel_count DESC);
CREATE INDEX IF NOT EXISTS idx_library_file_orig_url
    ON library_file (orig_url) WHERE orig_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_library_file_missing
    ON library_file (missing_at) WHERE missing_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS tag (
    id             INTEGER PRIMARY KEY,
    source_site_id INTEGER NOT NULL REFERENCES source_site (id),
    name           TEXT NOT NULL,
    kind           INTEGER NOT NULL DEFAULT 0 CHECK (kind IN (0, 1, 3, 4, 5)),
    post_count     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source_site_id, name)
) STRICT;

CREATE TABLE IF NOT EXISTS image_tag (
    library_file_id INTEGER NOT NULL REFERENCES library_file (id) ON DELETE CASCADE,
    tag_id          INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
    weight          REAL,
    PRIMARY KEY (library_file_id, tag_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_image_tag_tag ON image_tag (tag_id);

CREATE TABLE IF NOT EXISTS image_category (
    library_file_id     INTEGER NOT NULL REFERENCES library_file (id) ON DELETE CASCADE,
    content_category_id INTEGER NOT NULL REFERENCES content_category (id),
    PRIMARY KEY (library_file_id, content_category_id)
) STRICT;

CREATE TABLE IF NOT EXISTS search_session (
    id                   INTEGER PRIMARY KEY,
    content_category_id  INTEGER NOT NULL REFERENCES content_category (id),
    query_zh             TEXT NOT NULL DEFAULT '',
    tags_json            TEXT NOT NULL DEFAULT '[]',
    sites_json           TEXT NOT NULL DEFAULT '[]',
    sort_key             TEXT NOT NULL DEFAULT 'mixed'
                             CHECK (sort_key IN ('mixed', 'match', 'popularity', 'resolution', 'time')),
    include_r18          INTEGER NOT NULL DEFAULT 0 CHECK (include_r18 IN (0, 1)),
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS search_result (
    id                INTEGER PRIMARY KEY,
    search_session_id INTEGER NOT NULL REFERENCES search_session (id) ON DELETE CASCADE,
    source_site_id    INTEGER NOT NULL REFERENCES source_site (id),
    post_id           TEXT NOT NULL,
    post_key          TEXT NOT NULL,
    thumbnail_url     TEXT,
    preview_url       TEXT,
    file_url          TEXT,
    post_url          TEXT,
    artist_name       TEXT,
    width             INTEGER,
    height            INTEGER,
    rating            TEXT,
    score_popularity  REAL,
    score_match       REAL,
    score_final       REAL,
    already_owned     INTEGER NOT NULL DEFAULT 0 CHECK (already_owned IN (0, 1)),
    fetched_at        TEXT NOT NULL,
    UNIQUE (search_session_id, post_key)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_search_result_session
    ON search_result (search_session_id, score_final DESC);

CREATE TABLE IF NOT EXISTS download_task (
    id                INTEGER PRIMARY KEY,
    library_file_id   INTEGER REFERENCES library_file (id) ON DELETE SET NULL,
    search_result_id  INTEGER REFERENCES search_result (id) ON DELETE SET NULL,
    source_site_id    INTEGER NOT NULL REFERENCES source_site (id),
    post_key          TEXT NOT NULL,
    content_category_id INTEGER REFERENCES content_category (id),
    file_url          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued', 'running', 'done', 'archived',
                                            'in_library', 'failed', 'skipped')),
    priority          INTEGER NOT NULL DEFAULT 100,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    bytes_total       INTEGER,
    bytes_done        INTEGER,
    target_rel_path   TEXT,
    error_kind        TEXT CHECK (error_kind IS NULL OR error_kind IN
                          ('deleted', 'auth_required', 'timeout', 'no_space',
                           'bad_filename', 'unknown')),
    error_detail      TEXT,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    UNIQUE (source_site_id, post_key)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_download_task_status ON download_task (status, priority);

CREATE TABLE IF NOT EXISTS translation_cache (
    id             INTEGER PRIMARY KEY,
    input_text     TEXT NOT NULL,
    input_hash     TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_hash    TEXT NOT NULL,
    output_json    TEXT NOT NULL,
    token_usage    INTEGER,
    created_at     TEXT NOT NULL,
    expires_at     TEXT,
    UNIQUE (input_hash, provider, model, prompt_hash)
) STRICT;

CREATE TABLE IF NOT EXISTS dictionary_entry (
    id                INTEGER PRIMARY KEY,
    content_category_id INTEGER REFERENCES content_category (id),
    term_zh           TEXT NOT NULL,
    tag               TEXT NOT NULL,
    weight            REAL NOT NULL DEFAULT 1.0,
    source            TEXT NOT NULL DEFAULT 'builtin'
                          CHECK (source IN ('builtin', 'generated', 'manual', 'user')),
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (content_category_id, term_zh, tag)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_dictionary_entry_term ON dictionary_entry (term_zh);
CREATE INDEX IF NOT EXISTS idx_dictionary_entry_tag ON dictionary_entry (tag);

CREATE TABLE IF NOT EXISTS dictionary_override (
    id            INTEGER PRIMARY KEY,
    category_slug TEXT NOT NULL,
    term_zh       TEXT NOT NULL,
    action        TEXT NOT NULL CHECK (action IN ('add', 'remove', 'replace')),
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (category_slug, term_zh, action)
) STRICT;

CREATE TABLE IF NOT EXISTS tag_alias (
    id     INTEGER PRIMARY KEY,
    tag_id INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
    alias  TEXT NOT NULL,
    lang   TEXT NOT NULL DEFAULT 'zh',
    source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'dictionary')),
    UNIQUE (tag_id, alias)
) STRICT;

CREATE TABLE IF NOT EXISTS runtime_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE VIRTUAL TABLE IF NOT EXISTS library_search USING fts5 (
    artist_name,
    title,
    tag_string,
    content = 'library_file',
    content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS library_search_zh USING fts5 (
    file_id UNINDEXED,
    artist_name,
    title,
    content = 'library_file',
    content_rowid = 'id',
    tokenize = 'trigram'
);

CREATE TRIGGER IF NOT EXISTS library_file_fts_insert AFTER INSERT ON library_file BEGIN
    INSERT INTO library_search (rowid, artist_name, title, tag_string)
    VALUES (new.id, new.artist_name_raw, new.title, new.tag_string);
END;

CREATE TRIGGER IF NOT EXISTS library_file_fts_delete AFTER DELETE ON library_file BEGIN
    INSERT INTO library_search (library_search, rowid, artist_name, title, tag_string)
    VALUES ('delete', old.id, old.artist_name_raw, old.title, old.tag_string);
END;

CREATE TRIGGER IF NOT EXISTS library_file_fts_update
AFTER UPDATE OF artist_name_raw, title, tag_string ON library_file BEGIN
    INSERT INTO library_search (library_search, rowid, artist_name, title, tag_string)
    VALUES ('delete', old.id, old.artist_name_raw, old.title, old.tag_string);
    INSERT INTO library_search (rowid, artist_name, title, tag_string)
    VALUES (new.id, new.artist_name_raw, new.title, new.tag_string);
END;

CREATE TRIGGER IF NOT EXISTS library_file_zh_insert AFTER INSERT ON library_file BEGIN
    INSERT INTO library_search_zh (rowid, file_id, artist_name, title)
    VALUES (new.id, new.id, new.artist_name_raw, new.title);
END;

CREATE TRIGGER IF NOT EXISTS library_file_zh_delete AFTER DELETE ON library_file BEGIN
    INSERT INTO library_search_zh (library_search_zh, rowid, file_id, artist_name, title)
    VALUES ('delete', old.id, old.id, old.artist_name_raw, old.title);
END;

CREATE TRIGGER IF NOT EXISTS library_file_zh_update
AFTER UPDATE OF artist_name_raw, title ON library_file BEGIN
    INSERT INTO library_search_zh (library_search_zh, rowid, file_id, artist_name, title)
    VALUES ('delete', old.id, old.id, old.artist_name_raw, old.title);
    INSERT INTO library_search_zh (rowid, file_id, artist_name, title)
    VALUES (new.id, new.id, new.artist_name_raw, new.title);
END;
"""

SEED_CATEGORIES_SQL = """
INSERT OR IGNORE INTO content_category (id, slug, name_zh, sort_order) VALUES
    (1, 'pose',    '人体姿势', 10),
    (2, 'outfit',  '服装',     20),
    (3, 'prop',    '道具',     30),
    (4, 'scene',   '场景建筑', 40);
"""

SEED_SITES_SQL = """
INSERT OR IGNORE INTO source_site
    (slug, name, base_url, api_kind, enabled, requires_key, rate_limit_per_sec,
     rate_limit_note, user_agent_required, license_kind, notes)
VALUES
    ('danbooru', 'Danbooru', 'https://danbooru.donmai.us', 'danbooru', 0, 0, 1.0,
     '官方明文：全局读限每秒 10 次，长会话建议每秒 1 次', 1, 'site',
     '核心源。分级语法为 rating:general 等四级，匿名一次最多两个标签'),
    ('yandere', 'yande.re', 'https://yande.re', 'moebooru', 0, 0, 1.0,
     '无官方速率数值，社区显示官方要求控制负载', 1, 'site',
     'Moebooru 站点。每页上限 1000，无页数上限。部分常见标签会返回空数组'),
    ('konachan', 'Konachan', 'https://konachan.net', 'moebooru', 0, 0, 1.0,
     '无官方速率数值，按保守值处理', 1, 'site',
     'konachan.com 被 Cloudflare 拦，实测 konachan.net 同接口可用'),
    ('metmuseum', '大都会艺术博物馆',
     'https://collectionapi.metmuseum.org', 'museum', 0, 0, 2.0,
     '公开接口，未给出速率条款。详情端点实测不限流，因此给得比保守值宽', 0, 'open',
     '公共领域高分辨率藏品图，适合道具与服装类。检索走两步：先取编号再取详情');
"""

# 已退役的来源。
#
# 用户把来源收敛成四个：Danbooru、yande.re、Konachan、大都会艺术博物馆。
# Safebooru 与 Gelbooru 属于同族但没写客户端，Openverse 是通用底座而不是
# 参考图来源，三者都从列表里去掉。
#
# 用删除而不是标记。标记需要给表加列，而这一列除了界面过滤没有别的用处；
# 删除更干净。库里若已经有引用它们的图片，删除会被外键挡住，那时退回停用，
# 用户的图不会因为改来源列表而消失。
RETIRE_SITES_SQL = """
DELETE FROM source_site WHERE slug IN ('safebooru', 'gelbooru', 'openverse')
"""

DISABLE_SITES_SQL = """
UPDATE source_site SET enabled = 0
 WHERE slug IN ('safebooru', 'gelbooru', 'openverse')
"""


@dataclass
class LibraryStats:
    """图库统计，对应 F-13。"""

    total: int = 0
    missing: int = 0
    total_bytes: int = 0

    @property
    def total_mb(self) -> float:
        return round(self.total_bytes / 1_048_576, 1)


def connect(database_file: Path) -> sqlite3.Connection:
    """打开连接并设置必要的 PRAGMA。

    foreign_keys 必须显式打开，SQLite 默认不强制外键。漏掉这一条之后所有
    REFERENCES 都形同注释，数据损坏不会报错。
    """
    database_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_file)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize(connection: sqlite3.Connection) -> int:
    """建表并写入种子数据。返回当前模式版本。

    全部语句都带 IF NOT EXISTS 或 INSERT OR IGNORE，因此可以重复执行。
    """
    connection.executescript(SCHEMA_SQL)
    connection.executescript(SEED_CATEGORIES_SQL)
    connection.executescript(SEED_SITES_SQL)

    # 清掉已经退役的来源。老库里如果有引用它们的图片，删除会被外键挡住，
    # 那就退回停用，用户的图不会因为改了来源列表而消失。
    try:
        connection.executescript(RETIRE_SITES_SQL)
    except sqlite3.IntegrityError:
        connection.executescript(DISABLE_SITES_SQL)
        logger.info("退役来源仍被图片引用，已改为停用而不是删除")

    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        now = utc_now()
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('created_at', ?)", (now,)
        )
        connection.commit()
        logger.info("已建立图库索引数据库，模式版本 %s", SCHEMA_VERSION)
    else:
        connection.commit()

    return stats_version(connection)


def stats_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def library_stats(connection: sqlite3.Connection) -> LibraryStats:
    row = connection.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(file_size), 0) AS bytes,
                  COALESCE(SUM(CASE WHEN missing_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS missing
           FROM library_file"""
    ).fetchone()
    return LibraryStats(
        total=row["total"], total_bytes=row["bytes"], missing=row["missing"]
    )


def category_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            "SELECT id, slug, name_zh, sort_order FROM content_category ORDER BY sort_order"
        )
    )


def enabled_sites(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            "SELECT id, slug, name FROM source_site WHERE enabled = 1 ORDER BY id"
        )
    )


def dictionary_stats(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        """SELECT COUNT(*) AS total,
                  COUNT(DISTINCT term_zh) AS terms,
                  COALESCE(SUM(CASE WHEN source = 'generated' THEN 1 ELSE 0 END), 0) AS generated,
                  COALESCE(SUM(CASE WHEN source = 'manual' THEN 1 ELSE 0 END), 0) AS manual
           FROM dictionary_entry"""
    ).fetchone()
    return {
        "total": row["total"],
        "terms": row["terms"],
        "generated": row["generated"],
        "manual": row["manual"],
    }


def library_rows(connection: sqlite3.Connection, limit: int = 20000) -> list[sqlite3.Row]:
    """图库全部行，按下载时间倒序。

    比 recent_files 多返回两项：title 与 tag_string。图库页要按图片自带的
    文字信息分四类，那两项是分类的唯一依据，见 classify.py。

    上限 20000 行是防呆，不是分页。界面一次只渲染当前这一页，但分类统计与
    筛选要在整批上做，因此整批读进来。两万行的文本匹配在这个规模下是
    毫秒级的，不值得为它引入增量索引。
    """
    return list(
        connection.execute(
            """SELECT lf.id, lf.rel_path, lf.title, lf.tag_string,
                      lf.artist_name_raw, lf.width, lf.height, lf.file_size,
                      lf.downloaded_at, ss.slug AS site,
                      cc.name_zh AS category, cc.slug AS category_slug
               FROM library_file lf
               JOIN source_site ss ON ss.id = lf.source_site_id
               LEFT JOIN content_category cc ON cc.id = lf.content_category_id
               ORDER BY lf.downloaded_at DESC, lf.id DESC
               LIMIT ?""",
            (limit,),
        )
    )


def recent_files(connection: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """SELECT lf.rel_path, lf.artist_name_raw, lf.width, lf.height,
                      lf.file_size, lf.downloaded_at, ss.slug AS site,
                      cc.name_zh AS category
               FROM library_file lf
               JOIN source_site ss ON ss.id = lf.source_site_id
               LEFT JOIN content_category cc ON cc.id = lf.content_category_id
               ORDER BY lf.downloaded_at DESC
               LIMIT ?""",
            (limit,),
        )
    )


def search_files(connection: sqlite3.Connection, text: str) -> list[sqlite3.Row]:
    """按文本检索图库。

    返回的列与 recent_files 保持一致，图库页的图墙用同一套字段渲染，
    否则检索出来的卡片会缺类别与尺寸。

    中文的处理见 docs/database-design.md：trigram 分词器只对 3 个字符及以上的
    查询做子串匹配，更短的查询必须回退到 LIKE 扫描，否则会静默返回空结果。
    """
    text = text.strip()
    if not text:
        return []

    columns = """lf.rel_path, lf.artist_name_raw, lf.width, lf.height,
                 lf.file_size, lf.downloaded_at, ss.slug AS site,
                 cc.name_zh AS category"""
    joins = """FROM library_file lf
               JOIN source_site ss ON ss.id = lf.source_site_id
               LEFT JOIN content_category cc ON cc.id = lf.content_category_id"""

    if len(text) >= 3:
        try:
            return list(
                connection.execute(
                    f"""SELECT {columns}
                        FROM library_search_zh f
                        JOIN library_file lf ON lf.id = f.rowid
                        JOIN source_site ss ON ss.id = lf.source_site_id
                        LEFT JOIN content_category cc ON cc.id = lf.content_category_id
                        WHERE f.library_search_zh MATCH ?
                        LIMIT 200""",
                    (f'"{text}"',),
                )
            )
        except sqlite3.Error:
            logger.warning("中文全文检索失败，回退到 LIKE 匹配", exc_info=True)

    like = f"%{text}%"
    return list(
        connection.execute(
            f"""SELECT {columns} {joins}
                WHERE lf.artist_name_raw LIKE ? OR lf.title LIKE ? OR lf.tag_string LIKE ?
                ORDER BY lf.downloaded_at DESC
                LIMIT 200""",
            (like, like, like),
        )
    )
