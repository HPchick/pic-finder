"""审查发现的缺陷复现。断言描述期望行为；修复前失败是本脚本的预期结果。

独立运行：python tests/audit_regressions.py
不访问网络，不使用真实图库；通过 test_audit_regressions.py 纳入日常测试。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))
sys.path.insert(0, str(PROJECT / 'tests'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import httpx
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication
import shiboken6

from picfinder import db, paths
from picfinder.config import Config, load, save
from picfinder.download import DownloadJob, DownloadManager, DownloadResult
from picfinder.search import SearchRequest, SearchService, SearchSession
from picfinder.search.client import ClientContext, DanbooruClient, SearchQuery
from picfinder.search.types import SiteOutcome
from picfinder.translate import Translator
from picfinder.translate.dictionary import LocalDictionary, load_files
from picfinder.translate.global_index import GlobalTagIndex
from picfinder.ui.main_window import AppContext, MainWindow
from picfinder.ui.preview import PreviewDialog
from test_download import make_item, make_site, ok_response


class AuditRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        scratch = PROJECT / 'tests' / '.audit-scratch'
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.tmp.name)
        self.resolved = paths.for_root(self.root)
        self.resolved.ensure()
        self.connection = db.connect(self.resolved.database_file)
        db.initialize(self.connection)
        self.config = Config()
        self.windows = []
        self.contexts = []
        self.patch_index = patch('picfinder.translate.dictionary.global_index_dir', return_value=None)
        self.patch_index.start()
        self.patch_thumbs = patch('picfinder.ui.thumbnails.ThumbnailCache.request', return_value=None)
        self.patch_thumbs.start()

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        for context in self.contexts:
            context.thumbnail_cache.shutdown()
            context.connection.close()
        self.connection.close()
        self.patch_thumbs.stop()
        self.patch_index.stop()
        self.app.processEvents()
        self.tmp.cleanup()

    def window(self):
        context = AppContext(self.resolved, self.config, self.connection,
                             Translator(self.connection, self.config))
        self.contexts.append(context)
        window = MainWindow(context)
        self.windows.append(window)
        return window

    def switch(self, context, root):
        with patch('picfinder.paths.write_pointer', return_value=True), \
             patch('picfinder.translate.dictionary.install_builtin_files'), \
             patch('picfinder.translate.dictionary.install_global_files'), \
             patch('picfinder.translate.dictionary.install'):
            ok, message = context.change_data_root(root)
        self.assertTrue(ok, message)

    def manager(self, client):
        return DownloadManager(database_file=self.resolved.database_file,
                               library_dir=self.resolved.library_dir,
                               config=self.config, sites=[make_site()], client=client, workers=1)

    def test_B01_partial_last_page_is_reachable(self):
        page = self.window().search_page
        page.per_page = 2
        page.pool = [make_item(str(i)) for i in range(3)]
        page.session = SearchSession(SearchRequest('standing'), targets=['danbooru'],
                                     finished_sites={'danbooru'})
        self.assertTrue(page.can_go_next())
        page.go_next_page()
        self.assertEqual(page.page_index, 1)

    def test_B02_storage_switch_rebinds_search_database(self):
        window = self.window()
        self.switch(window.context, self.root / 'new-root')
        window.rebind_storage()
        self.assertEqual(window.search_page.runner.database_file,
                         window.context.paths.database_file)

    def test_B03_storage_switch_keeps_destination_configuration(self):
        window = self.window()
        target = self.root / 'existing-root'
        destination = Config()
        destination.search.max_tags_in_query = 1
        save(destination, target / 'data' / 'config.json')
        self.switch(window.context, target)
        self.assertEqual(load(target / 'data' / 'config.json').search.max_tags_in_query, 1)

    def test_B04_custom_library_is_consistent_after_switch(self):
        context = self.window().context
        custom = self.root / 'custom-library'
        self.assertTrue(context.set_library_dir(custom)[0])
        self.switch(context, self.root / 'new-root')
        self.assertEqual(context.paths.library_dir, Path(context.config.storage.library_dir))

    def test_B05_download_uses_search_category(self):
        page = self.window().search_page
        item = make_item()
        page.pool = [item]
        page.session = SearchSession(SearchRequest('standing', category_slug='pose'))
        page.select_category('prop')
        self.assertEqual(page.pool, [])
        self.assertIsNone(page.session)
        with patch.object(page.download_runner, 'start', return_value=False) as start:
            page.start_download([make_item()])
        self.assertEqual(start.call_args.args[3][0].category_slug, 'pose')

    def test_B06_cancelled_item_is_not_downloaded(self):
        page = self.window().search_page
        item = make_item()
        page.pool = [item]
        page.render_page()
        page._on_download_result(DownloadResult(item.post_key, status='skipped',
                                               error_detail='用户取消，未开始下载'))
        self.assertFalse(item.already_owned)

    def test_B07_off_page_download_updates_item(self):
        page = self.window().search_page
        item = make_item()
        page.pool = [make_item('other'), item]
        page.per_page = 1
        page.render_page()
        page._on_download_result(DownloadResult(item.post_key, status='done', rel_path='a.jpg'))
        self.assertTrue(item.already_owned)

    def test_B08_source_fallback_registered_alongside_md5(self):
        service = SearchService(self.connection)
        a = make_item('a', md5='aaa', source_url='https://example.invalid/work/1')
        b = make_item('b', md5='', source_url=a.source_url, artist_name='')
        merged, _ = service._deduplicate([a, b])
        self.assertEqual(len(merged), 1)

    def test_B09_equal_artist_and_pixels_do_not_prove_duplicates(self):
        service = SearchService(self.connection)
        a = make_item('a', md5='', width=100, height=200)
        b = make_item('b', md5='', width=200, height=100)
        merged, _ = service._deduplicate([a, b])
        self.assertEqual(len(merged), 2)

    def test_B10_blacklist_is_applied(self):
        context = self.window().context
        context.config.search.blacklist_tags = ['audit_blocked']
        item = make_item(tag_string='standing audit_blocked')
        with patch.object(context.search_service, '_run_sites',
                          return_value=[SiteOutcome(make_site(), items=[item])]):
            result = context.search_service.search(SearchRequest('standing', site_slugs=['danbooru']),
                                                   persist=False)
        self.assertEqual(result.items, [])

    def test_B11_user_removal_survives_global_dictionary_fallback(self):
        directory = self.root / 'zh'
        directory.mkdir()
        (directory / 'general.csv').write_text(
            'tag,category,aliases,zh,count,notes\nstanding,0,,站立,100,\n', encoding='utf-8')
        index = GlobalTagIndex(directory)
        index.load()
        self.connection.execute('INSERT INTO dictionary_override '
                                '(category_slug,term_zh,action,payload_json,created_at) '
                                'VALUES (?,?,?,?,?)', ('pose', '站立', 'remove', '{"all":true}', db.utc_now()))
        self.connection.commit()
        result = LocalDictionary(self.connection, index).translate('站立', 'pose')
        self.assertNotIn('standing', [candidate.tag for candidate in result.tags])

    def test_B12_thirty_candidates_not_truncated_to_six(self):
        directory = self.root / 'zh'
        directory.mkdir()
        text = 'tag,category,aliases,zh,count,notes\n'
        text += ''.join(f'variant_{i},4,,角色（版本{i}）,100,\n' for i in range(10))
        (directory / 'general.csv').write_text(text, encoding='utf-8')
        index = GlobalTagIndex(directory)
        index.load()
        self.assertEqual(len(index.translate(['角色'], max_tags=30, strict=True)), 10)

    def test_B13_bad_dictionary_top_level_degrades_gracefully(self):
        (self.root / 'common.json').write_text('[]', encoding='utf-8')
        report, entries, _ = load_files(self.root)
        self.assertTrue(report.problems)
        self.assertEqual(entries, [])

    def test_B14_missing_file_can_be_downloaded_again(self):
        with httpx.Client(transport=httpx.MockTransport(ok_response)) as client:
            manager = self.manager(client)
            first = manager.run([DownloadJob(make_item(), category_slug='pose')])
            generated = self.resolved.library_dir / first.results[0].rel_path
            self.assertTrue(generated.resolve().is_relative_to(self.root.resolve()))
            generated.unlink()
            manager.run([DownloadJob(make_item(), category_slug='pose')])
            self.assertTrue(generated.exists())

    def test_B15_metadata_failure_does_not_commit_partial_record(self):
        with httpx.Client(transport=httpx.MockTransport(ok_response)) as client:
            manager = self.manager(client)
            with patch.object(manager, '_attach_tags', side_effect=RuntimeError('injected metadata failure')), \
                 patch('picfinder.download.manager.time.sleep'):
                result = manager.run([DownloadJob(make_item(), category_slug='pose')])
        incomplete = self.connection.execute('SELECT COUNT(*) FROM library_file lf '
            'WHERE NOT EXISTS (SELECT 1 FROM image_tag it WHERE it.library_file_id=lf.id)').fetchone()[0]
        self.assertEqual(incomplete, 0, result.summary())

    def test_B16_cancelled_queue_is_persisted(self):
        cancel = threading.Event()
        cancel.set()
        with httpx.Client(transport=httpx.MockTransport(ok_response)) as client:
            self.manager(client).run([DownloadJob(make_item(), category_slug='pose')], cancel=cancel)
        status = self.connection.execute('SELECT status FROM download_task').fetchone()[0]
        self.assertEqual(status, 'skipped')

    def test_B17_cached_preview_rejects_old_download_callback(self):
        dialog = PreviewDialog()
        cache = Mock()
        cache.has.return_value = True
        cache.path_for.return_value = self.root / 'b.jpg'
        dialog._wanted_url = 'https://example.invalid/a'
        with patch.object(dialog, '_load_file') as load_file:
            dialog.show_search_item(make_item('b'), cache)
            dialog._on_job_done('https://example.invalid/a', str(self.root / 'a.jpg'))
            last_path = load_file.call_args.args[0]
        dialog.close()
        self.assertEqual(last_path, self.root / 'b.jpg')

    def test_B18_completed_preview_job_can_be_closed(self):
        dialog = PreviewDialog()
        cache = Mock()
        cache.local_path.return_value = self.root / 'preview.jpg'
        dialog._start_job(cache, 'https://example.invalid/a')
        job = dialog._job
        self.assertTrue(job.wait(2000), '测试线程应已完成')
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(shiboken6.isValid(job), '真实 finished 信号应触发 deleteLater')
        try:
            dialog.closeEvent(QCloseEvent())
        finally:
            dialog._job = None
            dialog.close()

    def test_B19_custom_limit_keeps_danbooru_paging(self):
        client = DanbooruClient(make_site(), ClientContext(limiter=Mock()))
        response = httpx.Response(200, json=[{'id': 1, 'file_url': 'https://example.invalid/1.jpg'}])
        with patch.object(client, '_request', return_value=response):
            result = client.search(SearchQuery('standing', limit=1))
        self.assertTrue(result.has_more)

    def test_B20_invalid_numeric_config_does_not_break_window(self):
        self.config = Config.from_dict({'ui': {'results_per_page': 'bad-value'}})
        self.window()


    def test_category_roundtrip_restores_input_tags_selection(self):
        from picfinder.translate.types import TagCandidate
        page = self.window().search_page
        page.input_box.setText('站立')
        page.tags = [TagCandidate('standing', 1, 'user')]
        item = make_item()
        page.pool = [item]
        page.selected_keys = {item.post_key}
        page.session = SearchSession(SearchRequest('standing', category_slug='pose'))
        session = page.session
        page.select_category('prop')
        self.assertEqual(page.input_box.text(), '')
        page.select_category('pose')
        self.assertEqual(page.input_box.text(), '站立')
        self.assertEqual([t.tag for t in page.tags], ['standing'])
        self.assertEqual(page.selected_keys, {item.post_key})
        self.assertIs(page.session, session)

    def test_pointer_failure_keeps_old_context(self):
        context = self.window().context
        previous = context.paths
        connection = context.connection
        with patch('picfinder.paths.write_pointer', return_value=False), \
             patch('picfinder.translate.dictionary.install_builtin_files'), \
             patch('picfinder.translate.dictionary.install_global_files'), \
             patch('picfinder.translate.dictionary.install'), \
             patch('picfinder.translate.dictionary.set_data_dictionary_dir') as setter:
            ok, _ = context.change_data_root(self.root / 'failed-switch')
        self.assertFalse(ok)
        self.assertIs(context.paths, previous)
        self.assertIs(context.connection, connection)
        self.assertEqual(connection.execute('SELECT 1').fetchone()[0], 1)
        self.assertEqual(setter.call_args.args[0], previous.dictionary_dir)

    def test_busy_context_rejects_switch(self):
        context = self.window().context
        target = self.root / 'busy-target'
        with patch.object(context, 'storage_busy', return_value=True):
            self.assertFalse(context.change_data_root(target)[0])
        self.assertFalse(target.exists())

    def test_rebind_preserves_destination_configuration_bytes(self):
        window = self.window()
        target = self.root / 'existing-target'
        destination = Config()
        destination.ui.last_category = 'prop'
        destination.ui.results_per_page = 50
        config_path = target / 'data' / 'config.json'
        save(destination, config_path)
        # Keep user formatting and unknown forward-compatible fields intact.
        payload = json.loads(config_path.read_text(encoding='utf-8'))
        payload['future_setting'] = 'keep'
        config_path.write_text(json.dumps(payload, indent=4), encoding='utf-8')
        before = config_path.read_bytes()
        self.switch(window.context, target)
        window.rebind_storage()
        self.assertEqual(config_path.read_bytes(), before)
        self.assertEqual(window.search_page.per_page, 50)

    def test_missing_repair_clears_missing_marker_and_keeps_id(self):
        with httpx.Client(transport=httpx.MockTransport(ok_response)) as client:
            manager = self.manager(client)
            first = manager.run([DownloadJob(make_item(), category_slug='pose')])
            row = self.connection.execute('SELECT id, rel_path FROM library_file').fetchone()
            generated = self.resolved.library_dir / row['rel_path']
            self.assertTrue(generated.resolve().is_relative_to(self.root.resolve()))
            generated.unlink()
            self.connection.execute('UPDATE library_file SET missing_at=?', (db.utc_now(),))
            self.connection.commit()
            repaired = manager.run([DownloadJob(make_item(), category_slug='pose')])
        current = self.connection.execute('SELECT id, missing_at FROM library_file').fetchone()
        self.assertEqual(current['id'], row['id'])
        self.assertIsNone(current['missing_at'])
        self.assertEqual(repaired.results[0].status, 'done')

    def test_metadata_failure_removes_downloaded_file(self):
        with httpx.Client(transport=httpx.MockTransport(ok_response)) as client:
            manager = self.manager(client)
            with patch.object(manager, '_attach_category', side_effect=RuntimeError('injected')), \
                 patch('picfinder.download.manager.time.sleep'):
                manager.run([DownloadJob(make_item(), category_slug='pose')])
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM library_file').fetchone()[0], 0)
        self.assertEqual([p for p in self.resolved.library_dir.rglob('*') if p.is_file()], [])

    def test_blacklist_snapshot_is_used_by_worker(self):
        config = Config()
        config.search.blacklist_tags = ['blocked']
        session = SearchService(self.connection, config=config).start_session(SearchRequest('standing'))
        config.search.blacklist_tags.clear()
        worker = SearchService(self.connection)
        filtered = {}
        self.assertEqual(worker._filter_items([make_item(tag_string='blocked')], session.request, filtered), [])

    def test_dedup_alias_bridge_merges_previous_groups(self):
        service = SearchService(self.connection)
        a = make_item('a', md5='aaa', source_url='')
        b = make_item('b', md5='', source_url='https://example.invalid/work/2')
        bridge = make_item('c', md5='aaa', source_url=b.source_url)
        result, duplicates = service._deduplicate([a, b, bridge])
        self.assertEqual(len(result), 1)
        self.assertEqual(duplicates, 2)


if __name__ == '__main__':
    output = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AuditRegressions)
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    report = PROJECT / 'tests' / 'audit-regressions-report.txt'
    report.write_text(output.getvalue(), encoding='utf-8')
    print(f'Cases={result.testsRun}; failures={len(result.failures)}; errors={len(result.errors)}')
    print(f'Report: {report}')
    raise SystemExit(0 if result.wasSuccessful() else 1)
