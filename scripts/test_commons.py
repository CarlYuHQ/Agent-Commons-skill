#!/usr/bin/env python3
"""只用临时目录和短生命周期本机服务验证 Commons；不访问默认用户目录。"""

import sys
sys.dont_write_bytecode = True

import ast
import concurrent.futures
import contextlib
import copy
import http.client
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

import commons


AUTHOR = {'kind': 'ai', 'model': '测试模型', 'framework': '本机代理', 'label': '助手甲'}
HUMAN = {'kind': 'human', 'model': '', 'framework': '', 'label': '用户甲'}


def payload(**changes):
    result = {'title': '中文工具验证经验', 'summary': '先明确适用范围，再执行验证。',
              'category': 'verification', 'tags': ['中文', '本地'], 'author': copy.deepcopy(AUTHOR),
              'scope': {'project': '示例项目', 'applicable': '本机测试', 'avoid': '生产环境'},
              'status': 'observed', 'evidenceType': 'hypothesis', 'source': '普通文本参考，不访问链接。',
              'body': '# 中文正文\n通过单元测试验证工具，保留复核依据。'}
    result.update(changes)
    return result


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='agent-commons-test-')
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'community'
        self.store = commons.CommonsStore(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def assert_error(self, expected_status, action, *args, **kwargs):
        with self.assertRaises(commons.CommonsError) as caught:
            action(*args, **kwargs)
        self.assertEqual(caught.exception.status, expected_status)
        self.assertTrue(caught.exception.message)
        return caught.exception


class StoreTests(Fixture):
    def test_create_read_edit_and_export(self):
        content = payload()
        post = self.store.create(content)
        self.assertEqual(commons.valid_id(post['id']), post['id'])
        self.assertEqual(post['revision'], 1)
        self.assertEqual(post['body'], content['body'])
        self.assertEqual(post['likeCount'], 0)
        self.assertEqual(post['adoptionCount'], 0)
        self.assertEqual(post['comments'], [])
        self.assertEqual(post['adoptions'], [])
        self.assertEqual(post['createdAt'], post['updatedAt'])
        self.assertEqual(self.store.read(post['id']), post)
        path = self.root / 'posts' / (post['id'] + '.md')
        self.assertEqual(path.read_text(encoding='utf-8'), post['body'])
        changed = self.store.edit(post['id'], {'expectedRevision': 1, 'title': '更新标题', 'body': '新版正文'})
        self.assertEqual(changed['revision'], 2)
        self.assertEqual(changed['title'], '更新标题')
        self.assertEqual(changed['summary'], post['summary'])
        self.assertEqual(changed['createdAt'], post['createdAt'])
        self.assertEqual(changed['body'], '新版正文')
        self.assertNotEqual(changed['updatedAt'], post['updatedAt'])
        exported = self.store.export()
        self.assertEqual(exported['schemaVersion'], 1)
        self.assertEqual(exported['posts'], [changed])
        # 当前正文不在 posts 表；revisions 是只读快照而不是第二份当前正文。
        with contextlib.closing(sqlite3.connect(str(self.store.db_path))) as db:
            fields = [item[1] for item in db.execute('PRAGMA table_info(posts)')]
            self.assertNotIn('body', fields)
            self.assertNotIn('body', json.loads(db.execute('SELECT metadata FROM posts').fetchone()[0]))

    def test_chinese_fulltext_search_and_no_fallback(self):
        post = self.store.create(payload(title='一般标题', summary='摘要无关键词', body='使用中文双字检索查找正文中的特殊术语。'))
        results = self.store.search('特殊术语')
        self.assertEqual([item['id'] for item in results], [post['id']])
        self.assertNotIn('body', results[0])
        self.assertNotIn('comments', results[0])
        self.assertEqual(self.store.search('火星不存在的关键词'), [])
        self.assertEqual(self.store.search(''), [])
        self.assertEqual(self.store.search('   '), [])
        self.assertEqual(self.store.search('!!!!'), [])
        self.assertEqual(self.store.search('特殊术语', category='coding'), [])
        self.assertEqual(self.store.search('特殊术语', project='另一个项目'), [])
        self.assertEqual(self.store.search('特殊术语', project='生产环境'), [])
        self.assertEqual(len(self.store.search('特殊术语', project='示例项目')), 1)

    def test_search_defaults_top_five_and_scope_evidence_before_heat(self):
        for status in ('draft', 'archived'):
            self.store.create(payload(status=status))
        for index in range(7):
            self.store.create(payload(title='中文工具普通%d' % index, scope={'project': '', 'applicable': '', 'avoid': ''}))
        hot = self.store.create(payload(title='中文工具热度高', scope={'project': '', 'applicable': '', 'avoid': ''}))
        for index in range(8):
            who = dict(AUTHOR, label='点赞者%d' % index)
            self.store.like(hot['id'], {'actor': who, 'liked': True})
        evidence = self.store.create(payload(title='中文工具实测证据', evidenceType='test_result', status='validated',
                                             scope={'project': '', 'applicable': '', 'avoid': ''}))
        scoped = self.store.create(payload(title='中文工具明确范围'))
        results = self.store.search('中文工具', project='示例项目')
        self.assertEqual(len(results), 5)
        self.assertEqual(results[0]['id'], scoped['id'])
        self.assertEqual(results[1]['id'], evidence['id'])
        self.assertTrue(all(item['status'] not in ('draft', 'archived') for item in results))
        self.assertEqual(self.store.read(hot['id'])['status'], 'observed')
        self.assertEqual(len(self.store.search('中文工具', limit=2)), 2)
        for invalid in (0, 6, True, '5'):
            self.assert_error(400, self.store.search, '中文', invalid)

    def test_like_is_idempotent_and_does_not_upgrade_status(self):
        post = self.store.create(payload())
        for _ in range(3):
            result = self.store.like(post['id'], {'actor': AUTHOR, 'liked': True})
        self.assertEqual(result['likeCount'], 1)
        self.assertEqual(result['likes'], [{'actor': AUTHOR}])
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['revision'], 1)
        self.assertEqual(self.store.like(post['id'], {'actor': dict(AUTHOR, kind='human'), 'liked': True})['likeCount'], 1)
        first_time = result['updatedAt']
        self.assertEqual(self.store.like(post['id'], {'actor': AUTHOR, 'liked': True})['updatedAt'], first_time)
        second = dict(AUTHOR, framework='另一个框架')
        self.assertEqual(self.store.like(post['id'], {'actor': second, 'liked': True})['likeCount'], 2)
        for _ in range(2):
            result = self.store.like(post['id'], {'actor': AUTHOR, 'liked': False})
        self.assertEqual(result['likeCount'], 1)
        self.assert_error(400, self.store.like, post['id'], {'actor': AUTHOR, 'liked': 1})

    def test_adoption_upsert_counts_and_outcome(self):
        post = self.store.create(payload())
        value = {'actor': AUTHOR, 'taskId': '任务一', 'status': 'planned', 'reason': '准备复核', 'outcome': ''}
        planned = self.store.adopt(post['id'], value)
        self.assertEqual(planned['adoptionCount'], 0)
        self.assertEqual(len(planned['adoptions']), 1)
        applied = self.store.adopt(post['id'], dict(value, status='applied'))
        self.assertEqual(applied['adoptionCount'], 1)
        switched_kind = self.store.adopt(post['id'], dict(value, actor=dict(AUTHOR, kind='human'), status='applied'))
        self.assertEqual(switched_kind['adoptionCount'], 1)
        self.assertEqual(len(switched_kind['adoptions']), 1)
        for status in ('helped', 'failed'):
            self.assert_error(400, self.store.adopt, post['id'], dict(value, status=status))
            self.assert_error(400, self.store.adopt, post['id'], dict(value, status=status, outcome='  '))
        helped = self.store.adopt(post['id'], dict(value, status='helped', outcome='测试通过，消除了错误。'))
        self.assertEqual(helped['adoptionCount'], 1)
        self.assertEqual(helped['helpedCount'], 1)
        self.assertEqual(helped['failedCount'], 0)
        self.assertEqual(len(helped['adoptions']), 1)
        failed = self.store.adopt(post['id'], dict(value, status='failed', outcome='复现失败，记录原因。'))
        self.assertEqual(failed['helpedCount'], 0)
        self.assertEqual(failed['failedCount'], 1)
        self.assertEqual(failed['status'], 'observed')
        additional = self.store.adopt(post['id'], dict(value, taskId='任务二', status='helped', outcome='第二次实测通过。'))
        self.assertEqual(additional['adoptionCount'], 2)
        back = self.store.adopt(post['id'], value)
        self.assertEqual(back['adoptionCount'], 1)
        self.assertEqual(back['helpedCount'], 1)
        self.assertEqual(back['failedCount'], 0)

    def test_comment_fields_and_limits(self):
        post = self.store.create(payload())
        result = self.store.comment(post['id'], {'actor': HUMAN, 'text': '请补充复核依据。', 'section': '验证'})
        comment = result['comments'][0]
        self.assertEqual(comment['actor'], HUMAN)
        self.assertEqual(comment['text'], '请补充复核依据。')
        self.assertEqual(comment['section'], '验证')
        self.assertTrue(comment['createdAt'])
        self.assertEqual(commons.valid_id(comment['id']), comment['id'])
        result = self.store.comment(post['id'], {'actor': AUTHOR, 'text': '补充第二条评论'})
        self.assertEqual(result['comments'][1]['section'], '')
        self.assertEqual(result['revision'], 1)
        for value in ('', '  ', 123, '字' * 10001):
            self.assert_error(400, self.store.comment, post['id'], {'actor': HUMAN, 'text': value})

    def test_stale_revision_conflict_and_immutable_history(self):
        original = self.store.create(payload())
        changed = self.store.edit(original['id'], {'expectedRevision': 1, 'body': '正文第二版'})
        error = self.assert_error(409, self.store.edit, original['id'], {'expectedRevision': 1, 'body': '不能覆盖'})
        self.assertEqual(error.details['currentRevision'], 2)
        self.assertEqual(self.store.read(original['id']), changed)
        snapshots = self.store.history(original['id'])
        self.assertEqual([item['body'] for item in snapshots], [original['body'], '正文第二版'])
        with contextlib.closing(sqlite3.connect(str(self.store.db_path))) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE revisions SET body=? WHERE post_id=?', ('篡改', original['id']))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM revisions WHERE post_id=?', (original['id'],))

    def test_manual_markdown_change_conflicts_without_losing_history(self):
        original = self.store.create(payload())
        path = self.store.posts_dir / (original['id'] + '.md')
        manual_body = '# 手动修改\nMarkdown 才是当前正文。'
        path.write_text(manual_body, encoding='utf-8')
        error = self.assert_error(409, self.store.edit, original['id'], {'expectedRevision': 1, 'body': '不应覆盖'})
        self.assertEqual(error.details['currentRevision'], 2)
        self.assertEqual(path.read_text(encoding='utf-8'), manual_body)
        reopened = commons.CommonsStore(self.root)
        current = reopened.read(original['id'])
        self.assertEqual(current['revision'], 2)
        self.assertEqual(current['revisionSource'], 'external_edit')
        self.assertEqual(current['body'], manual_body)
        self.assertEqual(reopened.read(original['id'])['revision'], 2)
        snapshots = reopened.history(original['id'])
        self.assertEqual([item['body'] for item in snapshots], [original['body'], manual_body])
        reopened.edit(original['id'], {'expectedRevision': 2, 'body': '复核后的第三版'})
        self.assertEqual([item['revision'] for item in reopened.history(original['id'])], [1, 2, 3])

    def test_get_export_and_search_sync_external_changes(self):
        post = self.store.create(payload(body='最初内容'))
        path = self.root / 'posts' / (post['id'] + '.md')
        path.write_text('现在写入手动检索词', encoding='utf-8')
        results = self.store.search('手动检索词')
        self.assertEqual(results[0]['revision'], 2)
        self.assertEqual(self.store.export()['posts'][0]['body'], '现在写入手动检索词')
        path.write_text('第三次外部编辑', encoding='utf-8')
        self.assertEqual(self.store.export()['posts'][0]['revision'], 3)

    def test_id_validation_and_missing_posts(self):
        malicious = ('../secret', '../../secret.md', '/tmp/secret', 'not-a-uuid',
                     'x; DROP TABLE posts;', '%2e%2e%2fsecret', str(uuid.uuid4()).upper(), None)
        for pid in malicious:
            with self.subTest(pid=pid):
                self.assert_error(400, self.store.read, pid)
                self.assert_error(400, self.store.edit, pid, {'expectedRevision': 1})
        self.assert_error(404, self.store.read, str(uuid.uuid4()))

    def test_injection_body_source_and_comments_are_plain_text(self):
        marker = self.base / 'never-executed'
        injection = ('<script>alert(1)</script>\n${system("id")}\n'
                     "'); DROP TABLE posts; --\n"
                     '__import__("pathlib").Path(%r).write_text("执行")' % str(marker))
        with mock.patch('socket.create_connection') as network:
            post = self.store.create(payload(body=injection, source='https://example.invalid/不应请求'))
            result = self.store.comment(post['id'], {'actor': HUMAN, 'text': injection})
            network.assert_not_called()
        self.assertEqual(result['body'], injection)
        self.assertEqual(result['comments'][0]['text'], injection)
        self.assertFalse(marker.exists())
        self.assertEqual(len(self.store.export()['posts']), 1)

    def test_string_enum_and_structure_validation(self):
        invalid = [('title', 123), ('title', ''), ('summary', None), ('category', 'other'),
                   ('tags', '标签'), ('tags', [1]), ('tags', ['x'] * 51), ('author', 'ai'),
                   ('author', dict(AUTHOR, kind='robot')), ('author', dict(AUTHOR, model=[])),
                   ('scope', []), ('scope', {'project': 123}), ('status', 'approved'),
                   ('evidenceType', 'likes'), ('source', {}), ('body', None),
                   ('body', '字' * 100001), ('body', '\ud800')]
        for field, value in invalid:
            with self.subTest(field=field, value=str(value)[:20]):
                self.assert_error(400, self.store.create, payload(**{field: value}))
        self.assertEqual(self.store.list_posts(), [])
        self.assert_error(400, self.store.create, dict(payload(), unexpected='额外字段'))
        post = self.store.create(payload(body='字' * 100000))
        self.assertEqual(len(post['body']), 100000)
        for revision in (None, True, 1.0, '1', 0):
            self.assert_error(400, self.store.edit, post['id'], {'expectedRevision': revision})
        self.assert_error(400, self.store.list_posts, category='other')
        self.assert_error(400, self.store.list_posts, status='other')

    def test_validated_requires_evidence_not_heat(self):
        for source in ('', '已验证', 'https://example.invalid/验证', '尚未验证，测试结果待确认'):
            self.assert_error(400, self.store.create, payload(status='validated', source=source))
        evidence = self.store.create(payload(status='validated', evidenceType='test_result'))
        explained = self.store.create(payload(status='validated', source='在本机执行单元测试验证，结果全部通过。'))
        self.assertEqual(evidence['status'], 'validated')
        self.assertEqual(explained['status'], 'validated')
        self.assert_error(400, self.store.edit, evidence['id'], {'expectedRevision': 1, 'evidenceType': 'hypothesis'})

    def test_json_limits_duplicates_and_invalid_values(self):
        for raw in (b'[]', b'{', b'\xff', b'{"x":NaN}', b'{"x":1,"x":2}', b'[' * 2000):
            self.assert_error(400, commons.decode_json, raw)
        self.assert_error(400, commons.decode_json, b' ' * (commons.MAX_JSON_BYTES + 1))
        raw = b'{"x":"' + b'x' * (commons.MAX_JSON_BYTES - 8) + b'"}'
        self.assertEqual(len(raw), commons.MAX_JSON_BYTES)
        self.assertIn('x', commons.decode_json(raw))

    def test_persistence_reopen_includes_all_interactions(self):
        post = self.store.create(payload())
        pid = post['id']
        self.store.comment(pid, {'actor': HUMAN, 'text': '持久评论'})
        self.store.like(pid, {'actor': AUTHOR, 'liked': True})
        self.store.adopt(pid, {'actor': AUTHOR, 'taskId': '持久任务', 'status': 'helped', 'outcome': '通过实测。'})
        expected = self.store.edit(pid, {'expectedRevision': 1, 'summary': '持久摘要'})
        actual = commons.CommonsStore(self.root).read(pid)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual['revisionHistory']), 2)
        self.assertEqual(list(self.store.journal_dir.glob('*.json')), [])

    def test_body_file_symlink_and_directory_traversal_rejected(self):
        post = self.store.create(payload())
        secret = self.base / 'secret.txt'
        secret.write_text('不应读取或覆盖', encoding='utf-8')
        path = self.root / 'posts' / (post['id'] + '.md')
        path.unlink()
        path.symlink_to(secret)
        self.assert_error(500, self.store.read, post['id'])
        self.assert_error(500, self.store.edit, post['id'], {'expectedRevision': 1, 'body': '覆盖'})
        self.assertEqual(secret.read_text(encoding='utf-8'), '不应读取或覆盖')
        unsafe = self.base / 'unsafe'
        unsafe.mkdir()
        (unsafe / 'posts').symlink_to(self.store.posts_dir, target_is_directory=True)
        self.assert_error(500, commons.CommonsStore, unsafe)

    def test_failed_body_replace_rolls_back_database_and_file(self):
        original = self.store.create(payload())
        path = self.store.posts_dir / (original['id'] + '.md')
        real_write = commons.atomic_write
        failed = [False]

        def fail_after_replace(target, data):
            real_write(target, data)
            if Path(target) == path and not failed[0]:
                failed[0] = True
                raise OSError('模拟正文替换后失败')

        with mock.patch.object(commons, 'atomic_write', side_effect=fail_after_replace):
            with self.assertRaises(OSError):
                self.store.edit(original['id'], {'expectedRevision': 1, 'body': '未提交正文'})
        self.assertEqual(self.store.read(original['id']), original)
        self.assertEqual(len(self.store.history(original['id'])), 1)
        self.assertEqual(list(self.store.journal_dir.glob('*.json')), [])

    def test_failed_create_leaves_no_post_or_history(self):
        real_write = commons.atomic_write
        failed = [False]

        def fail_after_replace(target, data):
            real_write(target, data)
            if Path(target).suffix == '.md' and not failed[0]:
                failed[0] = True
                raise OSError('模拟新正文写入失败')

        with mock.patch.object(commons, 'atomic_write', side_effect=fail_after_replace):
            with self.assertRaises(OSError):
                self.store.create(payload())
        self.assertEqual(self.store.list_posts(), [])
        self.assertEqual(list(self.store.posts_dir.glob('*.md')), [])
        with contextlib.closing(sqlite3.connect(str(self.store.db_path))) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM revisions').fetchone()[0], 0)

    def test_sqlite_commit_failure_restores_previous_body(self):
        original = self.store.create(payload())
        real_connection = self.store._connection
        failure = [False]

        class ConnectionProxy:
            def __init__(self, db):
                self.db = db

            def __getattr__(self, name):
                return getattr(self.db, name)

            def commit(self):
                if not failure[0]:
                    failure[0] = True
                    raise sqlite3.OperationalError('模拟提交失败')
                return self.db.commit()

        @contextlib.contextmanager
        def injected_connection():
            with real_connection() as db:
                yield ConnectionProxy(db)

        with mock.patch.object(self.store, '_connection', injected_connection):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.edit(original['id'], {'expectedRevision': 1, 'body': '提交失败的正文'})
        self.assertEqual(self.store.read(original['id']), original)
        self.assertEqual(len(self.store.history(original['id'])), 1)

    def test_crash_recovery_restores_uncommitted_write(self):
        original = self.store.create(payload())
        # 模拟正文已替换、SQLite 尚未提交时进程退出：连接关闭撤销 SQL，保留恢复日志。
        with self.store._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store._write_body(original['id'], '崩溃遗留正文', original['body'], 2)
            db.execute('UPDATE posts SET revision=2,body_hash=? WHERE id=?',
                       (commons.body_hash('崩溃遗留正文'), original['id']))
        self.assertTrue(list(self.store.journal_dir.glob('*.json')))
        reopened = commons.CommonsStore(self.root)
        self.assertEqual(reopened.read(original['id']), original)
        self.assertEqual(list(reopened.journal_dir.glob('*.json')), [])

    def test_crash_recovery_preserves_later_manual_change(self):
        original = self.store.create(payload())
        with self.store._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store._write_body(original['id'], '未提交正文', original['body'], 2)
        path = self.store.posts_dir / (original['id'] + '.md')
        path.write_text('崩溃之后的人工修改', encoding='utf-8')
        reopened = commons.CommonsStore(self.root)
        result = reopened.read(original['id'])
        self.assertEqual(result['body'], '崩溃之后的人工修改')
        self.assertEqual(result['revision'], 2)
        self.assertEqual(reopened.history(original['id'])[0]['body'], original['body'])

    def test_committed_write_survives_journal_cleanup_failure(self):
        original = self.store.create(payload())
        with mock.patch.object(self.store, '_remove_journal', side_effect=OSError('模拟清理失败')):
            updated = self.store.edit(original['id'], {'expectedRevision': 1, 'body': '已提交正文'})
        self.assertEqual(updated['revision'], 2)
        self.assertTrue(list(self.store.journal_dir.glob('*.json')))
        reopened = commons.CommonsStore(self.root)
        self.assertEqual(reopened.read(original['id'])['body'], '已提交正文')
        self.assertEqual(len(reopened.history(original['id'])), 2)

    def test_two_store_instances_cannot_overwrite_same_revision(self):
        original = self.store.create(payload())
        other = commons.CommonsStore(self.root)

        def edit(store, body):
            try:
                return store.edit(original['id'], {'expectedRevision': 1, 'body': body})['revision']
            except commons.CommonsError as error:
                return error.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda item: edit(*item), [(self.store, '并发甲'), (other, '并发乙')]))
        self.assertEqual(sorted(results), [2, 409])
        self.assertEqual(len(self.store.history(original['id'])), 2)


class HTTPTests(Fixture):
    def setUp(self):
        super().setUp()
        self.assets = self.base / 'assets'
        self.assets.mkdir()
        (self.assets / 'index.html').write_text('<!doctype html><p>本机社区</p>', encoding='utf-8')
        self.server = commons.make_server(self.store, port=0, assets_dir=self.assets)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02})
        self.thread.start()
        self.port = self.server.server_port
        self.host = '127.0.0.1:%d' % self.port
        self.token = self.request('GET', '/api/config')[1]['csrfToken']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())
        super().tearDown()

    def request(self, method, path, value=None, headers=None, token=False, raw=None):
        request_headers = dict(headers or {})
        if token:
            request_headers.setdefault('X-Commons-Token', self.token)
        if value is not None:
            raw = commons.encode_json(value).encode('utf-8')
            request_headers.setdefault('Content-Type', 'application/json')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.request(method, path, body=raw, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            response_headers = dict(response.getheaders())
            decoded = json.loads(data.decode('utf-8')) if response_headers['Content-Type'].startswith('application/json') else data
            self.assertNotIn('Access-Control-Allow-Origin', response_headers)
            return response.status, decoded, response_headers
        finally:
            connection.close()

    def test_config_bind_and_index_only(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        status, config, headers = self.request('GET', '/api/config')
        self.assertEqual(status, 200)
        self.assertEqual(config['root'], str(self.store.root))
        self.assertEqual(config['categories'], commons.CATEGORIES)
        self.assertGreaterEqual(len(config['csrfToken']), 32)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        for path in ('/', '/index.html'):
            status, html, _ = self.request('GET', path)
            self.assertEqual(status, 200)
            self.assertIn('本机社区'.encode('utf-8'), html)
        for path in ('/commons.sqlite3', '/posts/any.md', '/assets/index.html', '/../secret', '/%2e%2e/secret'):
            self.assertEqual(self.request('GET', path)[0], 404)
        with commons.make_server(self.store, 0, self.assets) as another:
            self.assertNotEqual(another.csrf_token, self.token)

    def test_full_api_contract_and_persistent_reopen(self):
        status, created, _ = self.request('POST', '/api/posts', payload(), token=True)
        self.assertEqual(status, 201)
        self.assertEqual(set(created), {'post'})
        post = created['post']
        pid = post['id']
        path = '/api/posts/' + pid
        self.assertEqual(self.request('GET', path)[1], {'post': post})
        status, result, _ = self.request('PATCH', path, {'expectedRevision': 1, 'body': '更新正文'}, token=True)
        self.assertEqual(status, 200)
        self.assertEqual(result['post']['revision'], 2)
        status, result, _ = self.request('POST', path + '/comments', {'actor': HUMAN, 'text': 'HTTP 评论'}, token=True)
        self.assertEqual(status, 200)
        self.assertEqual(result['post']['comments'][0]['text'], 'HTTP 评论')
        for _ in range(2):
            status, result, _ = self.request('PUT', path + '/like', {'actor': AUTHOR, 'liked': True}, token=True)
            self.assertEqual(status, 200)
            self.assertEqual(result['post']['likeCount'], 1)
        value = {'actor': AUTHOR, 'taskId': 'HTTP 任务', 'status': 'planned'}
        status, result, _ = self.request('PUT', path + '/adoptions', value, token=True)
        self.assertEqual(status, 200)
        self.assertEqual(result['post']['adoptionCount'], 0)
        self.assertEqual(self.request('PUT', path + '/adoptions', dict(value, status='helped'), token=True)[0], 400)
        status, result, _ = self.request('PUT', path + '/adoptions', dict(value, status='helped', outcome='本机测试通过。'), token=True)
        self.assertEqual(status, 200)
        self.assertEqual(result['post']['helpedCount'], 1)
        required = commons.POST_FIELDS | {'id', 'createdAt', 'updatedAt', 'revision', 'likeCount',
                                         'adoptionCount', 'helpedCount', 'failedCount', 'likes', 'comments', 'adoptions'}
        self.assertTrue(required <= set(result['post']))
        status, exported, _ = self.request('GET', '/api/export')
        self.assertEqual(status, 200)
        self.assertEqual(exported, {'schemaVersion': 1, 'posts': [result['post']]})
        self.assertEqual(commons.CommonsStore(self.root).read(pid), result['post'])
        # 约定中没有删除接口，不能把 HTTP DELETE 实现为隐式删除。
        self.assertEqual(self.request('DELETE', path, token=True)[0], 404)

    def test_list_query_category_author_and_status_filters(self):
        one = self.store.create(payload(body='正文独有检索词'))
        self.store.create(payload(category='coding', status='draft', author=HUMAN, body='另一条正文'))
        status, result, _ = self.request('GET', '/api/posts')
        self.assertEqual(status, 200)
        self.assertEqual(len(result['posts']), 2)
        status, result, _ = self.request('GET', '/api/posts?q=%E7%8B%AC%E6%9C%89&category=verification&author=%E6%B5%8B%E8%AF%95&status=observed')
        self.assertEqual(status, 200)
        self.assertEqual([item['id'] for item in result['posts']], [one['id']])
        self.assertEqual(self.request('GET', '/api/posts?q=notfound')[1], {'posts': []})
        for query in ('category=other', 'status=other', 'q=x&q=y', 'unexpected=x'):
            self.assertEqual(self.request('GET', '/api/posts?' + query)[0], 400)

    def test_csrf_required_for_every_mutation(self):
        pid = self.store.create(payload())['id']
        path = '/api/posts/' + pid
        mutations = [('POST', '/api/posts', payload()),
                     ('PATCH', path, {'expectedRevision': 1, 'body': '禁止覆盖'}),
                     ('POST', path + '/comments', {'actor': AUTHOR, 'text': '禁止评论'}),
                     ('PUT', path + '/like', {'actor': AUTHOR, 'liked': True}),
                     ('PUT', path + '/adoptions', {'actor': AUTHOR, 'taskId': 'x', 'status': 'planned'})]
        for method, target, value in mutations:
            with self.subTest(method=method, target=target):
                self.assertEqual(self.request(method, target, value)[0], 403)
                self.assertEqual(self.request(method, target, value, headers={'X-Commons-Token': 'wrong-token-é'})[0], 403)
        self.assertEqual(self.store.read(pid)['revision'], 1)
        self.assertEqual(self.store.read(pid)['likeCount'], 0)
        self.assertEqual(self.store.read(pid)['comments'], [])

    def test_non_loopback_host_and_origin_rejected_without_cors(self):
        for host in ('example.invalid:%d' % self.port, '127.0.0.1.evil.invalid:%d' % self.port,
                     '0.0.0.0:%d' % self.port, '127.0.0.1:1', 'user@' + self.host):
            with self.subTest(host=host):
                self.assertEqual(self.request('GET', '/api/config', headers={'Host': host})[0], 403)
        for origin in ('https://example.invalid', 'null', 'http://127.0.0.1:1',
                       'http://user@' + self.host, 'http://' + self.host + '/path'):
            with self.subTest(origin=origin):
                self.assertEqual(self.request('GET', '/api/config', headers={'Origin': origin})[0], 403)
                self.assertEqual(self.request('POST', '/api/posts', payload(), token=True, headers={'Origin': origin})[0], 403)
        self.assertEqual(self.request('GET', '/api/config', headers={'Host': 'localhost:%d' % self.port})[0], 200)
        self.assertEqual(self.request('POST', '/api/posts', payload(), token=True,
                                      headers={'Origin': 'http://' + self.host})[0], 201)
        self.assertEqual(self.request('OPTIONS', '/api/posts', headers={'Origin': 'https://example.invalid'})[0], 403)

    def test_duplicate_host_and_token_headers_rejected(self):
        for duplicate in ('Host', 'X-Commons-Token'):
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
            try:
                connection.putrequest('POST', '/api/posts', skip_host=True)
                connection.putheader('Host', self.host)
                if duplicate == 'Host':
                    connection.putheader('Host', self.host)
                connection.putheader('X-Commons-Token', self.token)
                if duplicate == 'X-Commons-Token':
                    connection.putheader('X-Commons-Token', self.token)
                connection.putheader('Content-Length', '2')
                connection.putheader('Content-Type', 'application/json')
                connection.endheaders(b'{}')
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
            finally:
                connection.close()

    def test_json_content_length_type_and_size_errors(self):
        common = {'X-Commons-Token': self.token, 'Content-Type': 'application/json'}
        cases = [(dict(common, **{'Content-Length': str(commons.MAX_JSON_BYTES + 1)}), b''),
                 (dict(common, **{'Content-Length': '-1'}), b''),
                 (dict(common, **{'Content-Type': 'text/plain'}), b'{}'),
                 (common, b'{'), (common, b'[]'), (common, b'\xff'),
                 (dict(common, **{'Transfer-Encoding': 'chunked'}), b'')]
        for headers, raw in cases:
            with self.subTest(headers=headers, raw=raw):
                self.assertEqual(self.request('POST', '/api/posts', headers=headers, raw=raw)[0], 400)
        self.assertEqual(self.request('POST', '/api/posts', payload(body='字' * 100001), token=True)[0], 400)

    def test_http_revision_conflict_and_external_edit(self):
        original = self.store.create(payload())
        path = '/api/posts/' + original['id']
        status, _, _ = self.request('PATCH', path, {'expectedRevision': 1, 'summary': '更新'}, token=True)
        self.assertEqual(status, 200)
        status, error, _ = self.request('PATCH', path, {'expectedRevision': 1, 'body': '过期'}, token=True)
        self.assertEqual(status, 409)
        self.assertEqual(error['error']['currentRevision'], 2)
        md = self.store.posts_dir / (original['id'] + '.md')
        md.write_text('用户手动编辑后的正文', encoding='utf-8')
        status, error, _ = self.request('PATCH', path, {'expectedRevision': 2, 'body': '禁止覆盖'}, token=True)
        self.assertEqual(status, 409)
        self.assertEqual(error['error']['currentRevision'], 3)
        self.assertEqual(self.request('GET', path)[1]['post']['body'], '用户手动编辑后的正文')
        self.assertEqual(len(self.store.history(original['id'])), 3)

    def test_http_malicious_id_missing_and_server_errors(self):
        for path in ('/api/posts/..%2fsecret', '/api/posts/%2e%2e', '/api/posts/not-uuid', '/api/posts/%00'):
            self.assertEqual(self.request('GET', path)[0], 400)
        self.assertEqual(self.request('GET', '/api/posts/' + str(uuid.uuid4()))[0], 404)
        post = self.store.create(payload())
        with mock.patch.object(self.store, 'read', side_effect=OSError('内部敏感信息')):
            status, error, _ = self.request('GET', '/api/posts/' + post['id'])
        self.assertEqual(status, 500)
        self.assertNotIn('内部敏感信息', json.dumps(error, ensure_ascii=False))
        self.assertNotIn(self.token, json.dumps(error))

    def test_token_and_requests_are_not_logged(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.request('POST', '/api/posts', payload(), token=True)
            self.request('GET', '/api/config', headers={'Host': 'example.invalid'})
        self.assertEqual(output.getvalue(), '')
        self.assertFalse(any(self.token.encode('ascii') in path.read_bytes()
                             for path in self.root.rglob('*') if path.is_file()))


class CLITests(Fixture):
    def test_python39_syntax(self):
        # 语法检查不生成 pyc；运行时 API 也仅使用 Python 3.9 已有的标准库能力。
        for path in (Path(commons.__file__), Path(__file__)):
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path), feature_version=(3, 9))

    def cli(self, *args, env=None):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        if env:
            environment.update(env)
        result = subprocess.run([sys.executable, str(Path(commons.__file__)), '--root', str(self.root), *args],
                                capture_output=True, text=True, encoding='utf-8', env=environment, timeout=15)
        self.assertEqual(result.stderr, '')
        return result.returncode, json.loads(result.stdout)

    def input_file(self, value):
        path = self.base / 'input.json'
        path.write_text(commons.encode_json(value), encoding='utf-8')
        return str(path)

    def test_cli_all_storage_subcommands(self):
        code, initialized = self.cli('init')
        self.assertEqual(code, 0)
        self.assertEqual(initialized['root'], str(self.root))
        code, created = self.cli('post', '--file', self.input_file(payload()))
        self.assertEqual(code, 0)
        pid = created['post']['id']
        self.assertEqual(self.cli('read', pid)[1], created)
        code, result = self.cli('search', '--query', '中文', '--limit', '5', '--category', 'verification', '--project', '示例项目')
        self.assertEqual(code, 0)
        self.assertEqual(result['posts'][0]['id'], pid)
        self.assertNotIn('body', result['posts'][0])
        self.assertEqual(self.cli('search', '--query', '绝不匹配', '--limit5')[1], {'posts': []})
        code, edited = self.cli('edit', pid, '--file', self.input_file({'expectedRevision': 1, 'body': 'CLI 正文新版'}))
        self.assertEqual(code, 0)
        self.assertEqual(edited['post']['revision'], 2)
        self.assertEqual(self.cli('comment', pid, '--file', self.input_file({'actor': HUMAN, 'text': 'CLI 评论'}))[0], 0)
        code, liked = self.cli('like', pid, '--file', self.input_file({'actor': AUTHOR, 'liked': True}))
        self.assertEqual(code, 0)
        self.assertEqual(liked['post']['likeCount'], 1)
        code, adopted = self.cli('adopt', pid, '--file', self.input_file({'actor': AUTHOR, 'taskId': 'CLI 任务', 'status': 'helped', 'outcome': '通过。'}))
        self.assertEqual(code, 0)
        self.assertEqual(adopted['post']['helpedCount'], 1)
        output = self.base / 'export.json'
        self.assertEqual(self.cli('export', '--output', str(output))[0], 0)
        exported = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(exported, self.store.export())
        self.assertEqual(exported['posts'][0]['body'], 'CLI 正文新版')

    def test_root_priority_and_no_default_directory_access(self):
        environment_root = self.base / 'environment-root'
        with mock.patch.dict(os.environ, {'AGENT_COMMONS_HOME': str(environment_root)}):
            self.assertEqual(commons.resolve_root(self.root), self.root.resolve())
            self.assertEqual(commons.resolve_root(), environment_root.resolve())
            self.assert_error(400, commons.resolve_root, '')
            self.assert_error(400, commons.resolve_root, '含\x00空字节')
            code, _ = self.cli('init', env={'AGENT_COMMONS_HOME': str(environment_root)})
            self.assertEqual(code, 0)
            self.assertFalse(environment_root.exists())
            environment_store = commons.CommonsStore()
            self.assertEqual(environment_store.root, environment_root)
        # 默认路径只计算，不创建、更不读取其中内容。
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(commons.resolve_root(), (Path.home() / '.agent-commons').resolve())

    def test_cli_errors_and_help_are_json(self):
        for args in (('read', '../secret'), ('search', '--query', '中文', '--limit', '6'),
                     ('read',), ('unknown',), ('search', '--limit', 'not-int')):
            code, error = self.cli(*args)
            self.assertEqual(code, 2)
            self.assertIn('error', error)
        code, help_value = self.cli('--help')
        self.assertEqual(code, 0)
        self.assertIn('help', help_value)
        original = self.store.create(payload())
        self.store.edit(original['id'], {'expectedRevision': 1, 'title': '新标题'})
        code, error = self.cli('edit', original['id'], '--file', self.input_file({'expectedRevision': 1, 'body': '过期'}))
        self.assertEqual(code, 2)
        self.assertEqual(error['error']['status'], 409)

    def test_export_cannot_overwrite_storage(self):
        post = self.store.create(payload())
        for target in (self.store.db_path, self.store.posts_dir / (post['id'] + '.md'),
                       self.store.journal_dir / 'export.json'):
            self.assertEqual(self.cli('export', '--output', str(target))[0], 2)
        self.assertEqual(self.store.read(post['id']), post)

    def test_serve_startup_json_without_token_or_persistent_server(self):
        server = mock.MagicMock()
        server.server_port = 12345
        server.csrf_token = '不得输出的测试令牌'
        server.__enter__.return_value = server
        server.serve_forever.side_effect = KeyboardInterrupt
        output = io.StringIO()
        with mock.patch.object(commons, 'make_server', return_value=server) as create_server:
            with contextlib.redirect_stdout(output):
                result = commons.main(['--root', str(self.root), 'serve', '--port', '0'])
        self.assertEqual(result, 0)
        create_server.assert_called_once()
        self.assertEqual(create_server.call_args.args[1], 0)
        self.assertEqual(json.loads(output.getvalue())['url'], 'http://127.0.0.1:12345')
        self.assertNotIn(server.csrf_token, output.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
