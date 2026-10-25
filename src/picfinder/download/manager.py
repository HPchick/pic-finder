"""下载引擎。

一个任务的完整过程是：查重、定路径、下载到临时文件、校验文件头、算哈希、
原子改名、写库。

四条硬约束。
    一、先写 .part 文件，完成后原子改名。中途断电或崩溃不会留下半张图，
       也不会让半张图被当成一张完整的图收进图库。
    二、落盘之前查文件头。图片被删或需要登录时，站点可能返回一个状态码
       200 的 HTML 错误页。只看状态码会得到一堆打不开的 jpg。
    三、同一站点同时只允许一个请求，并遵守站点的最小间隔，与检索层一致。
    四、失败要分类。可重试的只有超时与未知错误，其余重试只是浪费时间。

线程与数据库
    每个下载线程自己开一个数据库连接。SQLite 的连接默认不能跨线程使用，
    而 WAL 模式允许一个写者加多个读者，几毫秒的写入不会互相阻塞。
    run 方法在调用它的线程里也开一个连接，用来做入队阶段的工作。

回调在下载线程里执行。界面层必须把它接到 Qt 信号上，不能直接改控件。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import closing, contextmanager
from pathlib import Path

import httpx

from .. import db
from ..config import Config
from ..logging_setup import get as get_logger
from ..search.client import USER_AGENT
from ..search.types import SiteInfo
from . import naming
from .types import (
    ERROR_UNKNOWN,
    RETRYABLE_ERRORS,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_IN_LIBRARY,
    STATUS_SKIPPED,
    DownloadJob,
    DownloadReport,
    DownloadResult,
    classify_exception,
    classify_status_code,
)

logger = get_logger("download")

# 同时进行的下载线程数。
#
# 站点限速由每站一个闸门单独负责，每站同时仍然只有一个请求，因此提高线程数
# 不会让某一站被压得更狠，只是让多站之间与同站排队之间的等待被填满。
# 从 3 提到 6：用户反馈下载慢，而瓶颈其实是每站闸门之间的空隙没人用。
#
# 这个数字现在对用户开放，见 config.Performance 与设置页的「性能与并发」。
# 这里的常量只作为默认值兜底。
DEFAULT_WORKERS = 6

# 单个任务的尝试次数。首次加两次重试。
MAX_ATTEMPTS = 3

# 两次尝试之间的等待秒数。不做指数退避之外的随机化，因为请求本身已经被
# 站点闸门限制在每秒一次以内，再加抖动没有意义。
RETRY_BACKOFF = (1.0, 2.0)

# 单次请求的超时。原图可能较大，读超时给得比检索层宽。
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=15.0, read=60.0)

# 流式下载的分块大小。
CHUNK_SIZE = 64 * 1024

# 查文件头时读多少字节。
SNIFF_BYTES = 32


def _safe_temp_name(post_key: str) -> str:
    """临时文件名。带前导点，出错时不容易被用户当成正常图片。"""
    stem = naming.sanitize_segment(post_key.replace(":", "_"), "task", limit=60)
    return f".{stem}.part"


class _SiteGate:
    """每站一道闸。同一站点同时只允许一个请求，且遵守最小间隔。"""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self.lock = threading.Lock()
        self.last_request = 0.0

    @contextmanager
    def hold(self):
        with self.lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self.last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                yield
            finally:
                self.last_request = time.monotonic()


class DownloadManager:
    """批量下载。界面只与它打交道。"""

    def __init__(
        self,
        *,
        database_file: Path,
        library_dir: Path,
        config: Config,
        sites: Sequence[SiteInfo],
        client: httpx.Client | None = None,
        workers: int = DEFAULT_WORKERS,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.database_file = Path(database_file)
        self.library_dir = Path(library_dir)
        self.config = config
        self.sites = {site.slug: site for site in sites}
        self.workers = max(1, int(workers))
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._client_lock = threading.Lock()

        # 站点闸门按需创建。用字典加锁，避免两个线程同时建同一个闸门。
        self._gates: dict[str, _SiteGate] = {}
        self._gates_lock = threading.Lock()

        self._index = 0
        self._index_lock = threading.Lock()
        self._results: list[DownloadResult] = []
        self._results_lock = threading.Lock()

    # ------------------------------------------------------------------ 客户端

    def _get_client(self) -> httpx.Client:
        """取共用的 HTTP 客户端。

        全批共用一个客户端，因为 httpx 的客户端内部维护连接池，每次新建都会
        重新握手。多线程下 httpx.Client 是线程安全的。
        """
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                )
            return self._client

    def close(self) -> None:
        """关闭自己创建的客户端。外部传入的客户端由调用方负责关闭。"""
        if self._owns_client and self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def _gate_for(self, slug: str) -> _SiteGate:
        with self._gates_lock:
            gate = self._gates.get(slug)
            if gate is None:
                site = self.sites.get(slug)
                interval = site.min_interval if site else 1.0
                gate = _SiteGate(interval)
                self._gates[slug] = gate
            return gate

    # ------------------------------------------------------------------ 入口

    def run(
        self,
        jobs: Sequence[DownloadJob],
        *,
        on_result: Callable[[DownloadResult], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> DownloadReport:
        """下载一批任务。返回汇总，不抛异常。

        入队阶段的查重在主线程完成。已经在库的任务不会进下载线程，
        因为它们的结局是确定的，没必要占一个线程。
        """
        started = time.perf_counter()
        report = DownloadReport()
        total = len(jobs)
        if total == 0:
            return report

        self._index = 0
        self._results = []
        self.library_dir.mkdir(parents=True, exist_ok=True)

        connection = db.connect(self.database_file)
        try:
            pending, early = self._prepare(jobs, connection)
        finally:
            connection.close()

        for result in early:
            self._record(result, on_result)
        if on_progress is not None:
            on_progress(len(early), total)

        if pending and not (cancel is not None and cancel.is_set()):
            self._run_workers(pending, on_result, on_progress, cancel, total)

        # 取消时没轮到的任务也要给出结局。否则界面上的计数与用户看到的不一致，
        # 用户会以为程序把剩下的任务忘了。它们在 download_task 里仍是 queued，
        # 因此下次可以继续下载。
        for job in self._unclaimed(pending):
            result = DownloadResult(
                post_key=job.post_key,
                site_slug=job.item.site_slug,
                status=STATUS_SKIPPED,
                error_detail="用户取消，未开始下载",
            )
            with closing(db.connect(self.database_file)) as cancelled_connection:
                self._write_task(cancelled_connection, job, STATUS_SKIPPED, result.error_detail)
            self._record(result, on_result)
            if on_progress is not None:
                on_progress(len(self._results), total)

        with self._results_lock:
            report.results = list(self._results)
        report.elapsed_ms = int((time.perf_counter() - started) * 1000)
        report.cancelled = bool(cancel is not None and cancel.is_set())
        logger.info("下载批次结束：%s", report.summary())
        return report

    # ------------------------------------------------------------------ 入队

    def _prepare(
        self, jobs: Sequence[DownloadJob], connection: sqlite3.Connection
    ) -> tuple[list[DownloadJob], list[DownloadResult]]:
        """查重并登记任务。返回待下载的任务与已经有结局的结果。"""
        pending: list[DownloadJob] = []
        early: list[DownloadResult] = []
        category_ids = {
            row["slug"]: row["id"]
            for row in connection.execute("SELECT id, slug FROM content_category")
        }

        for job in jobs:
            item = job.item
            result = DownloadResult(post_key=item.post_key, site_slug=item.site_slug)

            if job.category_id is None:
                job.category_id = category_ids.get(job.category_slug)

            # 判据一：同一作品已经下过。作品编号与站点构成唯一键。
            row = connection.execute(
                "SELECT rel_path FROM library_file WHERE post_key = ?", (item.post_key,)
            ).fetchone()
            if row is not None and self._file_exists(row["rel_path"]):
                result.status = STATUS_IN_LIBRARY
                result.rel_path = row["rel_path"]
                self._write_task(connection, job, STATUS_IN_LIBRARY)
                early.append(result)
                continue

            # 判据二：站点给了整图哈希时，先看看图库里有没有同一张图。
            # 同一张图可能被作者发在多个站，重复下载只是浪费带宽。
            if item.md5:
                row = connection.execute(
                    """SELECT rel_path FROM library_file
                       WHERE file_hash = ? AND file_hash_algo = 'md5'""",
                    (item.md5,),
                ).fetchone()
                if row is not None and self._file_exists(row["rel_path"]):
                    result.status = STATUS_SKIPPED
                    result.rel_path = row["rel_path"]
                    result.error_detail = f"同一张图已在库：{row['rel_path']}"
                    self._write_task(connection, job, STATUS_SKIPPED, result.error_detail)
                    early.append(result)
                    continue

            self._write_task(connection, job, "queued")
            pending.append(job)

        return pending, early

    def _write_task(
        self,
        connection: sqlite3.Connection,
        job: DownloadJob,
        status: str,
        detail: str = "",
    ) -> None:
        """登记或更新一条下载任务。

        download_task 上有 (source_site_id, post_key) 的唯一约束，因此重复
        入队要写成更新。用 ON CONFLICT 而不是 INSERT OR IGNORE，因为后者会
        把真实错误一并吞掉。
        """
        site = self.sites.get(job.item.site_slug)
        if site is None:
            return
        now = db.utc_now()
        connection.execute(
            """INSERT INTO download_task
                   (source_site_id, post_key, content_category_id, file_url, status,
                    attempt_count, target_rel_path, error_detail, created_at, finished_at)
               VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, ?)
               ON CONFLICT (source_site_id, post_key) DO UPDATE SET
                   status = excluded.status,
                   content_category_id = excluded.content_category_id,
                   file_url = excluded.file_url,
                   error_detail = excluded.error_detail,
                   finished_at = excluded.finished_at""",
            (
                site.id,
                job.item.post_key,
                job.category_id,
                job.file_url,
                status,
                detail or None,
                now,
                now if status != "queued" else None,
            ),
        )
        connection.commit()

    # ------------------------------------------------------------------ 线程池

    def _next_job(self, jobs: Sequence[DownloadJob]) -> DownloadJob | None:
        with self._index_lock:
            if self._index >= len(jobs):
                return None
            job = jobs[self._index]
            self._index += 1
            return job

    def _unclaimed(self, jobs: Sequence[DownloadJob]) -> list[DownloadJob]:
        """还没被任何线程领走的任务。取消时用来补结局。"""
        with self._index_lock:
            return list(jobs[self._index:])

    def _run_workers(
        self,
        jobs: Sequence[DownloadJob],
        on_result: Callable[[DownloadResult], None] | None,
        on_progress: Callable[[int, int], None] | None,
        cancel: threading.Event | None,
        total: int,
    ) -> None:
        """起若干个线程把任务做完。任何一个线程出错都不影响其他线程。"""
        errors: list[BaseException] = []
        count_lock = threading.Lock()

        def worker() -> None:
            connection: sqlite3.Connection | None = None
            try:
                # 下载线程自己开连接，见模块开头的说明。
                connection = db.connect(self.database_file)
                while True:
                    if cancel is not None and cancel.is_set():
                        break
                    job = self._next_job(jobs)
                    if job is None:
                        break
                    result = self._process(job, connection, cancel)
                    self._record(result, on_result)
                    if on_progress is not None:
                        with count_lock:
                            on_progress(len(self._results), total)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                logger.exception("下载线程异常退出")
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except sqlite3.Error:
                        pass

        threads = [
            threading.Thread(target=worker, name=f"download-{index}")
            for index in range(min(self.workers, len(jobs)))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        for error in errors:
            logger.error("下载批次中有线程失败：%s", error)

    def _record(
        self,
        result: DownloadResult,
        on_result: Callable[[DownloadResult], None] | None,
    ) -> None:
        """收下一个结局并通知界面。回调在下载线程里执行。"""
        with self._results_lock:
            self._results.append(result)
        if on_result is not None:
            on_result(result)

    # ------------------------------------------------------------------ 单个任务

    def _process(
        self,
        job: DownloadJob,
        connection: sqlite3.Connection,
        cancel: threading.Event | None,
    ) -> DownloadResult:
        """下载一张图。任何异常都在这里被收成结果，不向上抛。"""
        started = time.perf_counter()
        result = DownloadResult(post_key=job.post_key, site_slug=job.item.site_slug)
        url = job.file_url
        if not url:
            result.status = STATUS_FAILED
            result.error_kind = ERROR_UNKNOWN
            result.error_detail = "检索结果没有给出下载地址"
            self._finish_task(connection, job, result)
            return result

        last_kind = ERROR_UNKNOWN
        last_detail = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if cancel is not None and cancel.is_set():
                result.status = STATUS_SKIPPED
                result.error_detail = "用户取消"
                break
            result.attempts = attempt
            try:
                return self._attempt(job, connection, url, result, started)
            except _DuplicateImage as duplicate:
                # 同一张图已经下过。这不是失败，重试也没有意义。
                result.status = STATUS_SKIPPED
                result.error_detail = str(duplicate)
                result.rel_path = duplicate.rel_path
                logger.info("跳过重复图片 %s：%s", job.post_key, duplicate)
                break
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, _DownloadFailure):
                    # 状态码一类的判断已经给出了分类，不必再猜一次。
                    last_kind = exc.kind
                    last_detail = exc.detail
                else:
                    last_kind = classify_exception(exc)
                    last_detail = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "下载失败（第 %s 次）%s：%s", attempt, job.post_key, last_detail
                )
                if last_kind not in RETRYABLE_ERRORS or attempt >= MAX_ATTEMPTS:
                    break
                delay = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
                if cancel is not None:
                    cancel.wait(delay)
                else:
                    time.sleep(delay)

        if result.status != STATUS_SKIPPED:
            result.status = STATUS_FAILED
            result.error_kind = last_kind
            result.error_detail = last_detail
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        self._finish_task(connection, job, result)
        return result

    def _attempt(
        self,
        job: DownloadJob,
        connection: sqlite3.Connection,
        url: str,
        result: DownloadResult,
        started: float,
    ) -> DownloadResult:
        """一次完整的尝试。失败时抛异常，由上层决定要不要重试。"""
        item = job.item
        site = self.sites.get(item.site_slug)
        if site is None:
            raise RuntimeError(f"未知的检索源：{item.site_slug}")

        target_dir = self.library_dir / Path(
            *naming.archive_segments(
                rules=self.config.archive,
                site_slug=item.site_slug,
                category_name=job.category_name,
                artist=item.artist_name,
                timestamp=item.created_at,
                width=item.width,
                height=item.height,
            )
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        temp_path = target_dir / f"{_safe_temp_name(item.post_key)}.{uuid.uuid4().hex}.part"

        digest = hashlib.md5()
        written = 0
        head = b""
        content_type = ""
        try:
            with self._gate_for(item.site_slug).hold():
                client = self._get_client()
                with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
                    kind = classify_status_code(response.status_code)
                    if kind:
                        raise _DownloadFailure(
                            kind, f"HTTP {response.status_code}"
                        )
                    content_type = response.headers.get("content-type", "")
                    with temp_path.open("wb") as handle:
                        for chunk in response.iter_bytes(CHUNK_SIZE):
                            if not chunk:
                                continue
                            if len(head) < SNIFF_BYTES:
                                head += chunk[: SNIFF_BYTES - len(head)]
                            handle.write(chunk)
                            digest.update(chunk)
                            written += len(chunk)
        except BaseException:
            # 任何失败都要清掉临时文件，否则图库里会留下 .part 残渣。
            _remove_quietly(temp_path)
            raise

        if written == 0:
            _remove_quietly(temp_path)
            raise _DownloadFailure(ERROR_UNKNOWN, "返回内容为空")

        sniffed = naming.sniff_image_extension(head)
        if not sniffed:
            _remove_quietly(temp_path)
            # 站点返回了 200 但不是图片，多半是错误页。
            raise _DownloadFailure(
                ERROR_UNKNOWN,
                f"返回内容不是图片（内容类型 {content_type or '未知'}）",
            )

        # 落盘之前再查一次哈希。站点没给 md5 时，入队阶段查不出同一张图，
        # 只能等下载完、算出真实哈希之后才发现。此处不拦，后面写库会撞上
        # file_hash 的唯一索引，结果是文件已经落盘而记录写不进去，
        # 图库里多出一个不在索引里的孤儿文件。
        digest_hex = digest.hexdigest()
        final_path = None
        try:
            # 网络传输在事务外，落盘与全部元数据在同一写入事务中协调。
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT rel_path FROM library_file WHERE file_hash = ? AND file_hash_algo = 'md5'",
                (digest_hex,),
            ).fetchone()
            if row is not None and self._file_exists(row["rel_path"]):
                raise _DuplicateImage(f"同一张图已在库：{row['rel_path']}", row["rel_path"])
            extension = naming.extension_from(sniffed, item.file_ext)
            filename = naming.build_filename(item.artist_name, item.site_slug, item.post_id, extension)
            final_path, rel_path = self._resolve_target(target_dir, filename)
            os.replace(temp_path, final_path)
            file_id = self._insert_library_file(
                connection, job, rel_path, final_path, extension, written, digest_hex
            )
            if file_id is None:
                raise RuntimeError("图片主记录未建立")
            self._attach_tags(connection, file_id, site.id, item.tag_string)
            self._attach_category(connection, file_id, job.category_id)
            connection.commit()
        except BaseException:
            connection.rollback()
            _remove_quietly(temp_path)
            if final_path is not None:
                _remove_quietly(final_path)
            raise

        result.status = STATUS_DONE
        result.rel_path = rel_path
        result.bytes_done = written
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        self._finish_task(connection, job, result, library_file_id=file_id)
        return result

    def _file_exists(self, relative: str) -> bool:
        root = self.library_dir.resolve()
        candidate = (root / relative).resolve()
        return candidate.is_relative_to(root) and candidate.is_file()

    def _resolve_target(self, target_dir: Path, filename: str) -> tuple[Path, str]:
        """给出最终绝对路径与相对路径。重名时在扩展名之前加序号。

        重名几乎不可能出现，因为文件名里有站点与作品编号。这里只是兜底，
        因为 rel_path 上有唯一约束，撞上会让整条记录写不进去。
        """
        candidate = target_dir / filename
        stem, suffix = candidate.stem, candidate.suffix
        index = 2
        while candidate.exists():
            candidate = target_dir / f"{stem}_{index}{suffix}"
            index += 1
        rel = candidate.relative_to(self.library_dir).as_posix()
        return candidate, rel

    # ------------------------------------------------------------------ 写库

    def _insert_library_file(
        self,
        connection: sqlite3.Connection,
        job: DownloadJob,
        rel_path: str,
        final_path: Path,
        extension: str,
        size: int,
        md5: str,
    ) -> int | None:
        """写入图库记录。返回新记录的编号，写入失败时返回空。"""
        item = job.item
        site = self.sites.get(item.site_slug)
        if site is None:
            return None
        existing = connection.execute(
            "SELECT id, rel_path FROM library_file WHERE post_key = ? OR (file_hash = ? AND file_hash_algo = 'md5')",
            (item.post_key, md5),
        ).fetchone()
        if existing is not None:
            existing_path = (self.library_dir / existing["rel_path"]).resolve()
            if self._file_exists(existing["rel_path"]) and existing_path != final_path.resolve():
                raise _DuplicateImage("同一张图已在库", existing["rel_path"])
            # 缺失文件恢复时沿用主键，保留分类与用户关联信息。
            connection.execute(
                "UPDATE library_file SET rel_path=?, abs_path_hint=?, file_size=?, file_ext=?, "
                "file_hash=?, file_hash_algo='md5', missing_at=NULL, updated_at=? WHERE id=?",
                (rel_path, str(final_path), size, extension, md5, db.utc_now(), existing["id"]),
            )
            return existing["id"]
        now = db.utc_now()
        rating = (item.rating or "").strip().lower() or "unknown"
        if rating not in ("g", "s", "q", "e"):
            rating = "unknown"
        is_mature = 1 if rating in ("q", "e") else 0
        artist_id = self._ensure_artist(connection, site.id, item.artist_name)

        cursor = connection.execute(
            """INSERT INTO library_file
                   (source_site_id, post_id, post_key, post_url, artist_id,
                    artist_name_raw, content_category_id, title, rating, is_mature,
                    width, height, file_size, file_ext, file_hash, file_hash_algo,
                    orig_url, rel_path, abs_path_hint, tag_string, tag_count,
                    source_created_at, downloaded_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'md5', ?, ?, ?, ?, ?,
                       ?, ?, ?, ?)""",
            (
                site.id,
                item.post_id,
                item.post_key,
                item.post_url or None,
                artist_id,
                item.artist_name or None,
                job.category_id,
                item.title or None,
                rating,
                is_mature,
                item.width,
                item.height,
                size,
                extension,
                md5,
                item.thumbnail.original or item.thumbnail.sample or None,
                rel_path,
                str(final_path),
                item.tag_string,
                item.tag_count,
                item.created_at or None,
                now,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid) if cursor.lastrowid else None

    def _ensure_artist(
        self, connection: sqlite3.Connection, site_id: int, name: str
    ) -> int | None:
        """取得画师编号，没有就建一条。画师为空时返回空编号。

        与标签同理，多线程下会同时插入同一个画师，因此用 ON CONFLICT
        再回查，而不是先查后插。
        """
        cleaned = (name or "").strip()
        if not cleaned:
            return None
        normalized = cleaned.casefold()
        now = db.utc_now()
        cursor = connection.execute(
            """INSERT INTO artist
                   (source_site_id, name, name_normalized, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (source_site_id, name_normalized) DO NOTHING""",
            (site_id, cleaned, normalized, now, now),
        )
        if cursor.rowcount:
            return int(cursor.lastrowid) if cursor.lastrowid else None
        row = connection.execute(
            "SELECT id FROM artist WHERE source_site_id = ? AND name_normalized = ?",
            (site_id, normalized),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "UPDATE artist SET last_seen_at = ? WHERE id = ?", (now, row["id"])
        )
        return int(row["id"])

    def _attach_tags(
        self,
        connection: sqlite3.Connection,
        file_id: int,
        site_id: int,
        tag_string: str,
    ) -> None:
        """把站内标签写入 tag 与 image_tag。

        先查再插在单线程里没问题，但多个下载线程同时下到带同一标签的图时会撞车：
        两边都查不到，两边都插，后插的那个撞唯一约束直接失败，整张图被判为下载
        失败并重试，而重试又会撞上 post_key 的唯一约束，最终这张图变成「跳过」。

        因此这里用 ON CONFLICT DO NOTHING 再回查编号。冲突在这里是预期内的，
        不是错误；其他类型的约束错误仍然会抛出来。
        """
        names = [name for name in (tag_string or "").split() if name]
        if not names:
            return
        for name in names:
            cursor = connection.execute(
                """INSERT INTO tag (source_site_id, name, kind, post_count)
                   VALUES (?, ?, 0, 0)
                   ON CONFLICT (source_site_id, name) DO NOTHING""",
                (site_id, name),
            )
            if cursor.rowcount:
                tag_id = int(cursor.lastrowid)
            else:
                row = connection.execute(
                    "SELECT id FROM tag WHERE source_site_id = ? AND name = ?",
                    (site_id, name),
                ).fetchone()
                if row is None:
                    continue
                tag_id = int(row["id"])
            connection.execute(
                "INSERT INTO image_tag (library_file_id, tag_id, weight) VALUES (?, ?, NULL)"
                " ON CONFLICT (library_file_id, tag_id) DO NOTHING",
                (file_id, tag_id),
            )

    def _attach_category(
        self, connection: sqlite3.Connection, file_id: int, category_id: int | None
    ) -> None:
        """记录类别归属。类别始终记录，与是否按类别建目录无关，见 OQ-19。"""
        if category_id is None:
            return
        connection.execute(
            "INSERT INTO image_category (library_file_id, content_category_id) VALUES (?, ?)"
            " ON CONFLICT (library_file_id, content_category_id) DO NOTHING",
            (file_id, category_id),
        )

    def _finish_task(
        self,
        connection: sqlite3.Connection,
        job: DownloadJob,
        result: DownloadResult,
        library_file_id: int | None = None,
    ) -> None:
        """更新任务记录。失败时只写日志，不影响已经落盘的文件。"""
        site = self.sites.get(job.item.site_slug)
        if site is None:
            return
        try:
            connection.execute(
                """UPDATE download_task
                   SET status = ?, library_file_id = COALESCE(?, library_file_id),
                       attempt_count = ?, bytes_done = ?, target_rel_path = ?,
                       error_kind = ?, error_detail = ?, finished_at = ?
                   WHERE source_site_id = ? AND post_key = ?""",
                (
                    result.status,
                    library_file_id,
                    result.attempts,
                    result.bytes_done or None,
                    result.rel_path or None,
                    result.error_kind or None,
                    result.error_detail or None,
                    db.utc_now(),
                    site.id,
                    job.post_key,
                ),
            )
            connection.commit()
        except sqlite3.Error:
            logger.exception("更新下载任务失败：%s", job.post_key)


class _DownloadFailure(Exception):
    """带分类的下载失败。用于把状态码一类的判断结果带出请求作用域。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class _DuplicateImage(Exception):
    """同一张图已经在库里。

    与失败分开的理由：它不是错误，重试也不会变好。用户看到「跳过重复」
    与看到「失败」的感受完全不同，前者说明程序在替他省空间。
    """

    def __init__(self, message: str, rel_path: str = "") -> None:
        super().__init__(message)
        self.rel_path = rel_path


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
