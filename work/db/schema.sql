-- PIC FINDER 图库索引数据库表结构
-- 适用 SQLite 3.35 及以上。开发环境实测版本为 3.50.4。
-- 设计理由见 docs/database-design.md。
--
-- 约定
--   1. 全部使用 STRICT 表，禁止 SQLite 的动态类型。
--   2. 时间戳统一存 UTC 的 ISO 8601 文本，形如 2026-10-07T12:00:00Z。
--   3. 外部键一律开启，由连接层执行 PRAGMA foreign_keys = ON。
--   4. 需要枚举语义的列用 CHECK 约束，不用魔法数字。
--   5. :schema_version 为构建时注入的模式版本号。

CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

INSERT INTO schema_meta (key, value) VALUES ('schema_version', :schema_version);
INSERT INTO schema_meta (key, value) VALUES ('created_at', :created_at);

-- 参考图类别。第一期固定四类，对应 DD-18。
-- 与用户自定义类别分开：这里只存软件内置的四类，用户自定义分类见 archive_rule。
CREATE TABLE content_category (
    id         INTEGER PRIMARY KEY,
    slug       TEXT NOT NULL UNIQUE,
    name_zh    TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
) STRICT;

-- 检索源。站点的代码行为差异用列显式表达，避免代码里到处写 if 站点名。
CREATE TABLE source_site (
    id                   INTEGER PRIMARY KEY,
    slug                 TEXT NOT NULL UNIQUE,
    name                 TEXT NOT NULL,
    base_url             TEXT NOT NULL,
    api_kind             TEXT NOT NULL CHECK (api_kind IN ('danbooru', 'moebooru', 'gelbooru', 'openverse', 'museum')),
    enabled              INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    requires_key         INTEGER NOT NULL DEFAULT 0 CHECK (requires_key IN (0, 1)),
    rate_limit_per_sec   REAL,
    rate_limit_note      TEXT,
    user_agent_required  INTEGER NOT NULL DEFAULT 0 CHECK (user_agent_required IN (0, 1)),
    license_kind         TEXT NOT NULL DEFAULT 'unknown' CHECK (license_kind IN ('open', 'site', 'unknown')),
    notes                TEXT
) STRICT;

-- 画师。按来源站与画师名唯一。
CREATE TABLE artist (
    id                INTEGER PRIMARY KEY,
    source_site_id    INTEGER NOT NULL REFERENCES source_site (id),
    name              TEXT NOT NULL,
    name_normalized   TEXT NOT NULL,
    profile_url       TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    UNIQUE (source_site_id, name_normalized)
) STRICT;

-- 图库记录。一行一张已入库的图片。
-- 四个唯一键的分工：
--   post_key   同一站点同一作品只入库一次
--   file_hash  同一份文件不重复占用磁盘
--   orig_url   作品换站后仍可识别
--   rel_path   同一物理文件只登记一次
CREATE TABLE library_file (
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
                           CASE WHEN width IS NULL OR height IS NULL THEN NULL ELSE width * height END
                       ) STORED,
    file_size          INTEGER,
    file_ext           TEXT,
    file_hash          TEXT,
    file_hash_algo      TEXT CHECK (file_hash_algo IS NULL OR file_hash_algo IN ('sha256', 'md5')),
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

CREATE UNIQUE INDEX idx_library_file_hash ON library_file (file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX idx_library_file_site_post ON library_file (source_site_id, post_id);
CREATE INDEX idx_library_file_artist ON library_file (artist_id);
CREATE INDEX idx_library_file_category ON library_file (content_category_id);
CREATE INDEX idx_library_file_rating ON library_file (rating);
CREATE INDEX idx_library_file_downloaded ON library_file (downloaded_at DESC);
CREATE INDEX idx_library_file_pixels ON library_file (pixel_count DESC);
CREATE INDEX idx_library_file_orig_url ON library_file (orig_url) WHERE orig_url IS NOT NULL;
CREATE INDEX idx_library_file_missing ON library_file (missing_at) WHERE missing_at IS NOT NULL;

-- 标签字典。按来源站与标签名唯一。分类沿用 Danbooru 的标签类型。
CREATE TABLE tag (
    id             INTEGER PRIMARY KEY,
    source_site_id INTEGER NOT NULL REFERENCES source_site (id),
    name           TEXT NOT NULL,
    kind           INTEGER NOT NULL DEFAULT 0 CHECK (kind IN (0, 1, 3, 4, 5)),
    post_count     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source_site_id, name)
) STRICT;

CREATE TABLE image_tag (
    library_file_id INTEGER NOT NULL REFERENCES library_file (id) ON DELETE CASCADE,
    tag_id          INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
    weight          REAL,
    PRIMARY KEY (library_file_id, tag_id)
) STRICT;

CREATE INDEX idx_image_tag_tag ON image_tag (tag_id);

-- 一张图可以同时属于多个类别，例如人体姿势加道具。
-- 该表始终写入，是否作为归档目录维度由归档规则决定，见 docs/database-design.md。
CREATE TABLE image_category (
    library_file_id     INTEGER NOT NULL REFERENCES library_file (id) ON DELETE CASCADE,
    content_category_id INTEGER NOT NULL REFERENCES content_category (id),
    PRIMARY KEY (library_file_id, content_category_id)
) STRICT;

-- 检索会话。四个类别各自保留自己的条件与结果，对应 F-15。
CREATE TABLE search_session (
    id                   INTEGER PRIMARY KEY,
    content_category_id  INTEGER NOT NULL REFERENCES content_category (id),
    query_zh             TEXT NOT NULL DEFAULT '',
    tags_json            TEXT NOT NULL DEFAULT '[]',
    sites_json           TEXT NOT NULL DEFAULT '[]',
    sort_key             TEXT NOT NULL DEFAULT 'mixed'
                             CHECK (sort_key IN ('mixed', 'match', 'popularity', 'resolution', 'time')),
    -- 成人内容开关的状态。0 为全年龄（general 与 sensitive），1 为含 R18
    -- （questionable 与 explicit）。这里存布尔值而不存单个分级字符串，因为
    -- 关闭状态对应两个分级值，存成字符串无法表达，见 DD-21。
    include_r18          INTEGER NOT NULL DEFAULT 0 CHECK (include_r18 IN (0, 1)),
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
) STRICT;

CREATE TABLE search_result (
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

CREATE INDEX idx_search_result_session ON search_result (search_session_id, score_final DESC);

-- 下载任务。状态迁移见 docs/database-design.md。
CREATE TABLE download_task (
    id                INTEGER PRIMARY KEY,
    library_file_id   INTEGER REFERENCES library_file (id) ON DELETE SET NULL,
    search_result_id  INTEGER REFERENCES search_result (id) ON DELETE SET NULL,
    source_site_id    INTEGER NOT NULL REFERENCES source_site (id),
    post_key          TEXT NOT NULL,
    content_category_id INTEGER REFERENCES content_category (id),
    file_url          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued', 'running', 'done', 'archived', 'in_library', 'failed', 'skipped')),
    priority          INTEGER NOT NULL DEFAULT 100,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    bytes_total       INTEGER,
    bytes_done        INTEGER,
    target_rel_path   TEXT,
    error_kind        TEXT CHECK (error_kind IS NULL OR error_kind IN
                          ('deleted', 'auth_required', 'timeout', 'no_space', 'bad_filename', 'unknown')),
    error_detail      TEXT,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    UNIQUE (source_site_id, post_key)
) STRICT;

CREATE INDEX idx_download_task_status ON download_task (status, priority);

-- 翻译缓存。同一中文输入与同一接口配置在一段时间内不重复请求。
CREATE TABLE translation_cache (
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

-- 词典条目。来源为内置词典文件，用户覆盖见 dictionary_override。
-- source 取值说明：builtin 为随程序分发的正式词条，generated 为接口生成待校对，
-- manual 为手工撰写，user 为程序内用户新增。区分它们是为了界面能提示未校对比例。
-- content_category_id 允许为 NULL，表示四类通用。词典文件的 common 类别即写入 NULL，
-- 因为 common 只是文件组织方式，不是参考图的类别，见 dictionary-format.md。
-- 注意：SQLite 中 NULL 互不相等，唯一约束挡不住通用词的重复写入，
-- 因此写入层必须先按唯一键探测，不能只依赖约束。
CREATE TABLE dictionary_entry (
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

CREATE INDEX idx_dictionary_entry_term ON dictionary_entry (term_zh);
CREATE INDEX idx_dictionary_entry_tag ON dictionary_entry (tag);

-- 用户覆盖。与词典条目分开存放，这样词典文件升级不会冲掉用户的修改。
CREATE TABLE dictionary_override (
    id          INTEGER PRIMARY KEY,
    category_slug TEXT NOT NULL,
    term_zh     TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('add', 'remove', 'replace')),
    payload_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (category_slug, term_zh, action)
) STRICT;

-- 标签别名。用户或词典都可以为一个标签登记多个中文说法。
CREATE TABLE tag_alias (
    id         INTEGER PRIMARY KEY,
    tag_id     INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,
    lang       TEXT NOT NULL DEFAULT 'zh',
    source     TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'dictionary')),
    UNIQUE (tag_id, alias)
) STRICT;

-- 运行期状态。例如磁盘对账的最后时间、词典文件版本。
CREATE TABLE runtime_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

-- 图库全文检索。行数较小，用外部内容表，索引与 library_file 由触发器保持同步。
-- 分词器为 unicode61，适用于拉丁标签。中文画师名与中文标题改由 LIKE 子串匹配处理，
-- 因为 FTS5 对中文无法按词切分。理由见 docs/database-design.md。
CREATE VIRTUAL TABLE library_search USING fts5 (
    artist_name,
    title,
    tag_string,
    content = 'library_file',
    content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);

-- 同步触发器。少了它们，索引会与内容表不一致，检索结果将静默出错。
CREATE TRIGGER library_file_fts_insert AFTER INSERT ON library_file BEGIN
    INSERT INTO library_search (rowid, artist_name, title, tag_string)
    VALUES (new.id, new.artist_name_raw, new.title, new.tag_string);
END;

CREATE TRIGGER library_file_fts_delete AFTER DELETE ON library_file BEGIN
    INSERT INTO library_search (library_search, rowid, artist_name, title, tag_string)
    VALUES ('delete', old.id, old.artist_name_raw, old.title, old.tag_string);
END;

CREATE TRIGGER library_file_fts_update AFTER UPDATE OF artist_name_raw, title, tag_string ON library_file BEGIN
    INSERT INTO library_search (library_search, rowid, artist_name, title, tag_string)
    VALUES ('delete', old.id, old.artist_name_raw, old.title, old.tag_string);
    INSERT INTO library_search (rowid, artist_name, title, tag_string)
    VALUES (new.id, new.artist_name_raw, new.title, new.tag_string);
END;

-- 中文检索索引。trigram 分词器提供子串匹配，适用于中文画师名与标题。
-- 已实测的三条边界：
--   查询串 3 个字符及以上可以子串命中，例如「边少女」能命中「海边少女与鲸鱼」。
--   查询串少于 3 个字符一律返回 0 条，即使内容包含该串。例如「肥鱼」命中不到「大肥鱼」。
--   因此查询层必须对 1 至 2 字的输入改走 LIKE 子串匹配，不能只用 MATCH。
-- file_id 标为 UNINDEXED，只用于回表，不参与匹配。
CREATE VIRTUAL TABLE library_search_zh USING fts5 (
    file_id UNINDEXED,
    artist_name,
    title,
    content = 'library_file',
    content_rowid = 'id',
    tokenize = 'trigram'
);

CREATE TRIGGER library_file_zh_insert AFTER INSERT ON library_file BEGIN
    INSERT INTO library_search_zh (rowid, file_id, artist_name, title)
    VALUES (new.id, new.id, new.artist_name_raw, new.title);
END;

CREATE TRIGGER library_file_zh_delete AFTER DELETE ON library_file BEGIN
    INSERT INTO library_search_zh (library_search_zh, rowid, file_id, artist_name, title)
    VALUES ('delete', old.id, old.id, old.artist_name_raw, old.title);
END;

CREATE TRIGGER library_file_zh_update AFTER UPDATE OF artist_name_raw, title ON library_file BEGIN
    INSERT INTO library_search_zh (library_search_zh, rowid, file_id, artist_name, title)
    VALUES ('delete', old.id, old.id, old.artist_name_raw, old.title);
    INSERT INTO library_search_zh (rowid, file_id, artist_name, title)
    VALUES (new.id, new.id, new.artist_name_raw, new.title);
END;

-- 四个内置类别。
INSERT INTO content_category (id, slug, name_zh, sort_order) VALUES
    (1, 'pose',    '人体姿势', 10),
    (2, 'outfit',  '服装',     20),
    (3, 'prop',    '道具',     30),
    (4, 'scene',   '场景建筑', 40);

-- 检索源种子。enabled 默认 0，由用户在设置页显式启用，符合 DD-03 与 OQ-07 的保守默认。
-- rate_limit_per_sec 为每秒请求上限。Danbooru 官方建议长会话压到每秒 1 次。
INSERT INTO source_site
    (slug, name, base_url, api_kind, enabled, requires_key, rate_limit_per_sec, rate_limit_note, user_agent_required, license_kind, notes)
VALUES
    ('danbooru', 'Danbooru', 'https://danbooru.donmai.us', 'danbooru', 0, 0, 1.0,
     '官方明文：全局读限每秒 10 次，长会话建议每秒 1 次', 1, 'site',
     '第一期核心源。分级语法为 rating:general 等四级'),
    ('safebooru', 'Safebooru', 'https://safebooru.org', 'gelbooru', 0, 0, 1.0,
     '文档未给出速率条款，按保守值处理', 1, 'site',
     '站点定位即安全向，默认安全源。接口与 Gelbooru 同族'),
    ('yandere', 'yande.re', 'https://yande.re', 'moebooru', 0, 0, 1.0,
     '无官方速率数值，社区显示官方要求控制负载', 1, 'site',
     'Moebooru 站点，limit 上限 100，不支持按编号便捷取单帖'),
    ('konachan', 'Konachan', 'https://konachan.com', 'moebooru', 0, 0, 1.0,
     '无官方速率数值，按保守值处理', 1, 'site',
     '接口版本很老，缺少编号范围游标与速率响应头，须单独适配'),
    ('gelbooru', 'Gelbooru', 'https://gelbooru.com', 'gelbooru', 0, 1, 1.0,
     '无公开速率数值，站点会限流', 1, 'site',
     '必须由用户自备 api_key 与 user_id。rating:safe 已废弃'),
    ('openverse', 'Openverse', 'https://api.openverse.org/v1', 'openverse', 0, 0, 0.5,
     '匿名可用，注册后额度更高。额度接口为 GET /v1/rate_limit/', 0, 'open',
     '开放授权通用底座。服务条款明文禁止爬取，分页深度受限，只作精准检索'),
    ('metmuseum', '大都会艺术博物馆', 'https://collectionapi.metmuseum.org/public/collection/v1', 'museum', 0, 0, 0.5,
     '公开接口，未给出速率条款，按保守值处理', 0, 'open',
     '公共领域高分辨率藏品图，适合道具与服装类');
