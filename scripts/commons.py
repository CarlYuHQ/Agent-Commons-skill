#!/usr/bin/env python3
"""Agent Commons 本机存储、HTTP API 和命令行入口（Python 3.9+）。"""

import sys
sys.dont_write_bytecode = True

import argparse
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import stat
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

CATEGORIES = ['scope', 'communication', 'tools', 'verification', 'coding',
              'data', 'design', 'research', 'workflow']
STATUSES = ('draft', 'observed', 'validated', 'disputed', 'archived')
EVIDENCE_TYPES = ('conversation_reference', 'user_report', 'test_result', 'hypothesis')
ADOPTION_STATUSES = ('planned', 'applied', 'helped', 'failed')
MAX_JSON_BYTES = 1024 * 1024
MAX_BODY_CHARS = 100000
POST_FIELDS = {'title', 'summary', 'category', 'tags', 'author', 'scope',
               'status', 'evidenceType', 'source', 'body'}
ID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')


class CommonsError(Exception):
    def __init__(self, status, message, code=None, commit_changes=False, **details):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code or {400: 'bad_request', 403: 'forbidden', 404: 'not_found',
                             409: 'revision_conflict', 500: 'storage_error'}.get(status, 'error')
        self.commit_changes = commit_changes
        self.details = details

    def as_json(self):
        return {'error': dict(code=self.code, status=self.status,
                              message=self.message, **self.details)}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def body_hash(body):
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def valid_id(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise CommonsError(400, '帖子 id 必须是小写 UUID v4，不能包含路径。')
    return value


def text(value, field, limit=4000, required=False):
    if not isinstance(value, str):
        raise CommonsError(400, field + ' 必须是字符串。')
    if len(value) > limit or (required and not value.strip()):
        raise CommonsError(400, field + ' 不能为空或超过长度限制（%d 字符）。' % limit)
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise CommonsError(400, field + ' 含有无效 Unicode 字符。')
    return value


def enum(value, values, field):
    if not isinstance(value, str) or value not in values:
        raise CommonsError(400, field + ' 必须是以下值之一：' + ', '.join(values))
    return value


def obj(value, field, allowed):
    if not isinstance(value, dict):
        raise CommonsError(400, field + ' 必须是 JSON 对象。')
    if set(value) - set(allowed):
        raise CommonsError(400, field + ' 含有未知字段。')
    return value


def actor(value, field='actor'):
    value = obj(value, field, ('kind', 'model', 'framework', 'label'))
    return {'kind': enum(value.get('kind'), ('ai', 'human'), field + '.kind'),
            **{key: text(value.get(key, ''), field + '.' + key, 200)
               for key in ('model', 'framework', 'label')}}


def actor_key(value):
    # 模型、框架和 label 构成唯一身份；切换 kind 不能重复点赞或重复计数。
    return encode_json([value[k] for k in ('model', 'framework', 'label')])


def has_verification(source):
    lowered = source.casefold()
    if re.search(r'未验证|未经验证|尚未验证|待验证|未测试|未经测试|not\s+(verified|tested)|unverified', lowered):
        return False
    return (len(source.strip()) >= 12
            and bool(re.search(r'验证|测试|复现|核验|实测|verify|verified|test|reproduced', lowered))
            and bool(re.search(r'通过|成功|符合|结果|步骤|命令|passed|confirmed|result|expected', lowered)))


def validate_post(value):
    value = obj(value, 'post', POST_FIELDS)
    title = text(value.get('title'), 'title', 300, True)
    summary = text(value.get('summary', ''), 'summary', 4000)
    category = enum(value.get('category'), CATEGORIES, 'category')
    tags = value.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 50:
        raise CommonsError(400, 'tags 必须是最多 50 个字符串的列表。')
    tags = [text(tag, 'tags[]', 100, True) for tag in tags]
    author = actor(value.get('author'), 'author')
    scope = obj(value.get('scope', {}), 'scope', ('project', 'applicable', 'avoid'))
    scope = {key: text(scope.get(key, ''), 'scope.' + key) for key in ('project', 'applicable', 'avoid')}
    status = enum(value.get('status', 'observed'), STATUSES, 'status')
    evidence = enum(value.get('evidenceType', 'hypothesis'), EVIDENCE_TYPES, 'evidenceType')
    source = text(value.get('source', ''), 'source', 10000)
    body = text(value.get('body'), 'body', MAX_BODY_CHARS)
    if status == 'validated' and evidence != 'test_result' and not has_verification(source):
        raise CommonsError(400, 'validated 必须使用 test_result，或在 source 中填写明确的验证过程与结果。')
    return dict(title=title, summary=summary, category=category, tags=tags, author=author,
                scope=scope, status=status, evidenceType=evidence, source=source, body=body)


def decode_json(raw):
    if len(raw) > MAX_JSON_BYTES:
        raise CommonsError(400, 'JSON 请求不能超过 1MB。')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('重复字段')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('非法数字')

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise CommonsError(400, '请求必须是有效 UTF-8 JSON，不能含重复字段或非法数字。')
    if not isinstance(value, dict):
        raise CommonsError(400, '请求必须是 JSON 对象。')
    return value


def resolve_root(explicit=None):
    # 这里只计算路径；只有创建存储实例时才会建立目录。
    chosen = os.fspath(explicit) if explicit is not None else os.environ.get('AGENT_COMMONS_HOME')
    if explicit is not None and (not isinstance(chosen, str) or not chosen.strip()):
        raise CommonsError(400, '--root 必须是非空目录路径。')
    chosen = chosen or '~/.agent-commons'
    if '\x00' in chosen:
        raise CommonsError(400, '根目录路径不能包含空字节。')
    try:
        return Path(chosen).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        raise CommonsError(400, '无法解析根目录路径。')


def _fsync_directory(directory):
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data):
    """同目录临时文件、fsync、replace；不沿着目标符号链接写入。"""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
        _fsync_directory(path.parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def safe_read(path, limit):
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise CommonsError(500, '存储文件必须是普通文件，不能是链接或设备。')
            data = handle.read(limit + 1)
    except FileNotFoundError:
        raise CommonsError(500, '正文或存储文件缺失，请检查本地文件。')
    except OSError:
        raise CommonsError(500, '无法安全读取本地文件，不能使用符号链接。')
    if len(data) > limit:
        raise CommonsError(400, '文件超过允许的长度。')
    return data


SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY, metadata TEXT NOT NULL, body_hash TEXT NOT NULL,
    revision INTEGER NOT NULL, revision_source TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL, metadata TEXT NOT NULL, body TEXT NOT NULL,
    body_hash TEXT NOT NULL, origin TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(post_id, revision)
);
CREATE TABLE IF NOT EXISTS likes (
    post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    actor_key TEXT NOT NULL, actor TEXT NOT NULL,
    PRIMARY KEY(post_id, actor_key)
);
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    actor TEXT NOT NULL, text TEXT NOT NULL, section TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adoptions (
    post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    actor_key TEXT NOT NULL, actor TEXT NOT NULL, task_id TEXT NOT NULL,
    status TEXT NOT NULL, reason TEXT NOT NULL, outcome TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(post_id, actor_key, task_id)
);
CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON revisions
BEGIN SELECT RAISE(ABORT, '历史快照不能修改'); END;
CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON revisions
BEGIN SELECT RAISE(ABORT, '历史快照不能删除'); END;
PRAGMA user_version = 1;
"""


class CommonsStore:
    """Markdown 是唯一当前正文；DB 的 revisions 仅作不可变复核快照。"""

    def __init__(self, root=None):
        self.root = resolve_root(root)
        self.posts_dir = self.root / 'posts'
        self.journal_dir = self.root / '.transactions'
        self.db_path = self.root / 'commons.sqlite3'
        self._lock = threading.RLock()
        if not self.root.is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
        for path in (self.posts_dir, self.journal_dir):
            if path.is_symlink():
                raise CommonsError(500, '存储子目录不能是符号链接。')
            if not path.is_dir():
                path.mkdir(exist_ok=True)
        self._check_paths()
        with self._connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise CommonsError(500, '不支持该本地数据库版本。')
            db.executescript(SCHEMA)
        with self._transaction():
            pass

    def _check_paths(self):
        for path in (self.posts_dir, self.journal_dir, self.db_path):
            if path.is_symlink() or path.parent.resolve() != self.root:
                raise CommonsError(500, '存储路径不安全，不能使用符号链接。')
        for suffix in ('-journal', '-wal', '-shm'):
            if Path(str(self.db_path) + suffix).is_symlink():
                raise CommonsError(500, '数据库辅助文件不能是符号链接。')

    @contextlib.contextmanager
    def _connection(self):
        self._check_paths()
        db = sqlite3.connect(str(self.db_path), timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys = ON')
            db.execute('PRAGMA synchronous = FULL')
            yield db
        finally:
            db.close()

    def _remove_journal(self, path):
        path.unlink()
        _fsync_directory(self.journal_dir)

    def _recover(self, db):
        # BEGIN IMMEDIATE 同时锁住其他进程；崩溃遗留事务在任何读取前恢复。
        for journal in sorted(self.journal_dir.glob('*.json')):
            item = decode_json(safe_read(journal, MAX_JSON_BYTES))
            pid = valid_id(item.get('id'))
            if journal.name != pid + '.json':
                raise CommonsError(500, '原子写恢复记录的 id 与文件名不一致。')
            row = db.execute('SELECT * FROM posts WHERE id=?', (pid,)).fetchone()
            path = self.posts_dir / (pid + '.md')
            current = self._read_body(pid) if os.path.lexists(path) else None
            current_hash = body_hash(current) if current is not None else None
            committed = (row is not None and row['revision'] == item['targetRevision']
                         and row['body_hash'] == item['targetHash'])
            if not committed:
                # 只恢复仍与本次写入相同的文件；不覆盖崩溃后用户另行修改的正文。
                if current_hash == item['targetHash'] or current is None:
                    previous = item['previousBody']
                    if previous is None:
                        if current is not None:
                            path.unlink()
                            _fsync_directory(self.posts_dir)
                    else:
                        atomic_write(path, text(previous, '恢复正文', MAX_BODY_CHARS).encode('utf-8'))
                elif row is None:
                    raise CommonsError(500, '未提交帖子正文已被外部修改，请人工复核恢复记录。')
            self._remove_journal(journal)

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                self._recover(db)
                yield db
                db.commit()
            except CommonsError as error:
                if error.commit_changes:
                    db.commit()
                else:
                    db.rollback()
                    self._recover_after_failure(db)
                raise
            except BaseException:
                db.rollback()
                self._recover_after_failure(db)
                raise
            else:
                # 提交后清理日志；清理失败不将已经提交的操作误报为失败。
                try:
                    self._recover_after_failure(db)
                except (OSError, CommonsError, sqlite3.Error):
                    pass

    def _recover_after_failure(self, db):
        db.execute('BEGIN IMMEDIATE')
        try:
            self._recover(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise

    def _read_body(self, pid):
        valid_id(pid)
        raw = safe_read(self.posts_dir / (pid + '.md'), MAX_BODY_CHARS * 4)
        try:
            body = raw.decode('utf-8')
        except UnicodeError:
            raise CommonsError(400, '手动编辑的 Markdown 必须是 UTF-8。')
        return text(body, '正文', MAX_BODY_CHARS)

    def _row(self, db, pid):
        row = db.execute('SELECT * FROM posts WHERE id=?', (valid_id(pid),)).fetchone()
        if row is None:
            raise CommonsError(404, '帖子不存在。')
        return row

    def _snapshot(self, db, row, body):
        db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?)',
                   (row['id'], row['revision'], row['metadata'], body, row['body_hash'],
                    row['revision_source'], row['updated_at']))

    def _sync(self, db, pid):
        row = self._row(db, pid)
        body = self._read_body(pid)
        digest = body_hash(body)
        if digest != row['body_hash']:
            db.execute('UPDATE posts SET body_hash=?, revision=revision+1, '
                       'revision_source=?, updated_at=? WHERE id=?',
                       (digest, 'external_edit', now(), pid))
            row = self._row(db, pid)
            self._snapshot(db, row, body)
        return row, body

    def _write_body(self, pid, body, previous, revision):
        path = self.posts_dir / (pid + '.md')
        journal = self.journal_dir / (pid + '.json')
        recovery = dict(id=pid, previousBody=previous, targetHash=body_hash(body),
                        targetRevision=revision)
        atomic_write(journal, encode_json(recovery).encode('utf-8'))
        # 外部编辑不遵循 SQLite 锁；在替换前再次核对，发现变化则留下正文。
        actual = self._read_body(pid) if os.path.lexists(path) else None
        if actual != previous:
            self._remove_journal(journal)
            raise CommonsError(409, '正文在保存期间被外部修改，请重新读取后编辑。')
        atomic_write(path, body.encode('utf-8'))

    def _post(self, db, row, body):
        pid = row['id']
        likes = [{'actor': json.loads(item['actor'])} for item in
                 db.execute('SELECT actor FROM likes WHERE post_id=? ORDER BY actor_key', (pid,))]
        comments = [dict(id=item['id'], actor=json.loads(item['actor']), text=item['text'],
                         section=item['section'], createdAt=item['created_at']) for item in
                    db.execute('SELECT * FROM comments WHERE post_id=? ORDER BY created_at,id', (pid,))]
        adoptions = [dict(actor=json.loads(item['actor']), taskId=item['task_id'],
                          status=item['status'], reason=item['reason'], outcome=item['outcome'],
                          updatedAt=item['updated_at']) for item in
                     db.execute('SELECT * FROM adoptions WHERE post_id=? ORDER BY actor_key,task_id', (pid,))]
        history = [dict(revision=item['revision'], bodyHash=item['body_hash'],
                        origin=item['origin'], createdAt=item['created_at']) for item in
                   db.execute('SELECT revision,body_hash,origin,created_at FROM revisions '
                              'WHERE post_id=? ORDER BY revision', (pid,))]
        return dict(json.loads(row['metadata']), body=body, id=pid,
                    createdAt=row['created_at'], updatedAt=row['updated_at'],
                    revision=row['revision'], bodyHash=row['body_hash'],
                    revisionSource=row['revision_source'], revisionHistory=history,
                    likeCount=len(likes), likes=likes, comments=comments, adoptions=adoptions,
                    adoptionCount=sum(item['status'] != 'planned' for item in adoptions),
                    helpedCount=sum(item['status'] == 'helped' for item in adoptions),
                    failedCount=sum(item['status'] == 'failed' for item in adoptions))

    def create(self, value):
        content = validate_post(value)
        body = content.pop('body')
        pid = str(uuid.uuid4())
        timestamp = now()
        with self._transaction() as db:
            if os.path.lexists(self.posts_dir / (pid + '.md')):
                raise CommonsError(409, '新帖子文件已存在。')
            db.execute('INSERT INTO posts VALUES (?,?,?,?,?,?,?)',
                       (pid, encode_json(content), body_hash(body), 1, 'created', timestamp, timestamp))
            row = self._row(db, pid)
            self._snapshot(db, row, body)
            self._write_body(pid, body, None, 1)
            result = self._post(db, row, body)
        return result

    def read(self, pid):
        valid_id(pid)
        with self._transaction() as db:
            row, body = self._sync(db, pid)
            result = self._post(db, row, body)
        return result

    def edit(self, pid, value):
        valid_id(pid)
        value = obj(value, 'PATCH', POST_FIELDS | {'expectedRevision'})
        expected = value.get('expectedRevision')
        if type(expected) is not int or expected < 1:
            raise CommonsError(400, 'expectedRevision 必须是正整数。')
        with self._transaction() as db:
            row, old_body = self._sync(db, pid)
            if row['revision'] != expected:
                # 外部正文同步必须提交，不能随 409 一起回滚。
                raise CommonsError(409, '版本已变化，请重新读取并复核后再编辑。',
                                   commit_changes=True, currentRevision=row['revision'])
            merged = dict(json.loads(row['metadata']), body=old_body)
            merged.update({key: value[key] for key in POST_FIELDS if key in value})
            content = validate_post(merged)
            body = content.pop('body')
            revision = row['revision'] + 1
            db.execute('UPDATE posts SET metadata=?,body_hash=?,revision=?,revision_source=?,updated_at=? '
                       'WHERE id=?', (encode_json(content), body_hash(body), revision, 'edited', now(), pid))
            row = self._row(db, pid)
            self._snapshot(db, row, body)
            self._write_body(pid, body, old_body, revision)
            result = self._post(db, row, body)
        return result

    def comment(self, pid, value):
        valid_id(pid)
        value = obj(value, 'comment', ('actor', 'text', 'section'))
        who = actor(value.get('actor'))
        message = text(value.get('text'), 'text', 10000, True)
        section = text(value.get('section', ''), 'section', 300)
        with self._transaction() as db:
            self._sync(db, pid)
            timestamp = now()
            db.execute('INSERT INTO comments VALUES (?,?,?,?,?,?)',
                       (str(uuid.uuid4()), pid, encode_json(who), message, section, timestamp))
            db.execute('UPDATE posts SET updated_at=? WHERE id=?', (timestamp, pid))
            row, body = self._sync(db, pid)
            result = self._post(db, row, body)
        return result

    def like(self, pid, value):
        valid_id(pid)
        value = obj(value, 'like', ('actor', 'liked'))
        who = actor(value.get('actor'))
        if type(value.get('liked')) is not bool:
            raise CommonsError(400, 'liked 必须是布尔值。')
        with self._transaction() as db:
            self._sync(db, pid)
            if value['liked']:
                db.execute('INSERT OR IGNORE INTO likes VALUES (?,?,?)',
                           (pid, actor_key(who), encode_json(who)))
            else:
                db.execute('DELETE FROM likes WHERE post_id=? AND actor_key=?', (pid, actor_key(who)))
            if db.execute('SELECT changes()').fetchone()[0]:
                db.execute('UPDATE posts SET updated_at=? WHERE id=?', (now(), pid))
            row, body = self._sync(db, pid)
            result = self._post(db, row, body)
        return result

    def adopt(self, pid, value):
        valid_id(pid)
        value = obj(value, 'adoption', ('actor', 'taskId', 'status', 'reason', 'outcome'))
        who = actor(value.get('actor'))
        task = text(value.get('taskId'), 'taskId', 300, True)
        status = enum(value.get('status'), ADOPTION_STATUSES, 'status')
        reason = text(value.get('reason', ''), 'reason', 4000)
        outcome = text(value.get('outcome', ''), 'outcome', 10000, status in ('helped', 'failed'))
        with self._transaction() as db:
            self._sync(db, pid)
            timestamp = now()
            db.execute('INSERT INTO adoptions VALUES (?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(post_id,actor_key,task_id) DO UPDATE SET '
                       'actor=excluded.actor,status=excluded.status,reason=excluded.reason,'
                       'outcome=excluded.outcome,updated_at=excluded.updated_at',
                       (pid, actor_key(who), encode_json(who), task, status, reason, outcome, timestamp))
            db.execute('UPDATE posts SET updated_at=? WHERE id=?', (timestamp, pid))
            row, body = self._sync(db, pid)
            result = self._post(db, row, body)
        return result

    def history(self, pid):
        valid_id(pid)
        with self._transaction() as db:
            self._sync(db, pid)
            return [dict(revision=item['revision'], body=item['body'],
                         metadata=json.loads(item['metadata']), bodyHash=item['body_hash'],
                         origin=item['origin'], createdAt=item['created_at']) for item in
                    db.execute('SELECT * FROM revisions WHERE post_id=? ORDER BY revision', (pid,))]

    @staticmethod
    def _matches(post, query):
        haystack = '\n'.join([post['title'], post['summary'], post['body'], post['source'],
                              ' '.join(post['tags']), *post['scope'].values()]).casefold()
        groups = re.findall(r'[\u3400-\u9fff]+|[\w]+', query.casefold())
        if not groups:
            return False
        for group in groups:
            if group in haystack:
                continue
            if re.fullmatch(r'[\u3400-\u9fff]{3,}', group):
                # 中文长词允许相邻双字全部命中，不调用云端分词服务。
                if all(group[i:i + 2] in haystack for i in range(len(group) - 1)):
                    continue
            return False
        return True

    def list_posts(self, query='', category='', author='', status=''):
        query = text(query, 'q')
        author = text(author, 'author', 800)
        if category:
            enum(category, CATEGORIES, 'category')
        if status:
            enum(status, STATUSES, 'status')
        with self._transaction() as db:
            result = []
            ids = [row['id'] for row in db.execute('SELECT id FROM posts ORDER BY updated_at DESC,id')]
            for pid in ids:
                row, body = self._sync(db, pid)
                post = self._post(db, row, body)
                if category and post['category'] != category:
                    continue
                if status and post['status'] != status:
                    continue
                if author and author.casefold() not in ' '.join(post['author'].values()).casefold():
                    continue
                if query and not self._matches(post, query):
                    continue
                result.append(post)
        return sorted(result, key=lambda item: (item['updatedAt'], item['id']), reverse=True)

    def search(self, query, limit=5, category='', project=''):
        text(query, 'query')
        text(project, 'project')
        if type(limit) is not int or not 1 <= limit <= 5:
            raise CommonsError(400, 'limit 必须是 1 到 5 的整数。')
        if category:
            enum(category, CATEGORIES, 'category')
        if not query.strip():
            return []
        candidates = []
        for post in self.list_posts(query=query, category=category):
            if post['status'] in ('draft', 'archived'):
                continue
            scope = post['scope']
            scope_score = 1
            if project:
                term = project.casefold()
                if term in scope['avoid'].casefold():
                    continue
                if scope['project'] and term in scope['project'].casefold():
                    scope_score = 3
                elif term in scope['applicable'].casefold():
                    scope_score = 2
                elif scope['project']:
                    continue
            evidence_score = {'test_result': 4, 'user_report': 2,
                              'conversation_reference': 1, 'hypothesis': 0}[post['evidenceType']]
            if has_verification(post['source']):
                evidence_score = max(evidence_score, 3)
            status_score = {'validated': 3, 'observed': 2, 'disputed': 1}[post['status']]
            # 热度仅作末位排序因素，既不替代适用范围也不自动升级状态。
            rank = (scope_score, evidence_score, status_score, post['helpedCount'],
                    post['adoptionCount'], post['likeCount'], post['updatedAt'], post['id'])
            brief = {key: value for key, value in post.items()
                     if key not in ('body', 'likes', 'comments', 'adoptions', 'revisionHistory')}
            candidates.append((rank, brief))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in candidates[:limit]]

    def export(self):
        return {'schemaVersion': 1, 'posts': self.list_posts()}


class CommonsHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, store, port=8765, assets_dir=None):
        if type(port) is not int or not 0 <= port <= 65535:
            raise CommonsError(400, 'port 必须是 0 到 65535 的整数。')
        self.store = store
        self.csrf_token = secrets.token_urlsafe(32)
        self.index_path = (Path(assets_dir) if assets_dir is not None else
                           Path(__file__).resolve().parent.parent / 'assets') / 'index.html'
        super().__init__(('127.0.0.1', port), CommonsHandler)


def make_server(store, port=8765, assets_dir=None):
    return CommonsHTTPServer(store, port, assets_dir)


def _loopback_host(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


class CommonsHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'AgentCommons/1'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # 不记录请求、正文、身份或令牌，避免日志泄漏和遥测。
        pass

    def _send(self, status, value, content_type='application/json; charset=utf-8'):
        raw = value if isinstance(value, bytes) else encode_json(value).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(raw)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self._send(code, CommonsError(code, 'HTTP 请求格式或方法不受支持。', code='http_error').as_json())

    def _guard(self, mutation):
        hosts = self.headers.get_all('Host', [])
        origins = self.headers.get_all('Origin', [])
        if len(hosts) != 1 or len(origins) > 1:
            raise CommonsError(403, '必须使用唯一的本机 Host/Origin。')
        try:
            host_value = hosts[0]
            if any(char.isspace() for char in host_value):
                raise ValueError()
            host = urlsplit('//' + host_value)
            if (not _loopback_host(host.hostname) or host.username is not None
                    or host.password is not None or host.path or host.query or host.fragment
                    or (host.port or 80) != self.server.server_port):
                raise ValueError()
            if origins:
                origin = urlsplit(origins[0])
                if (origin.scheme != 'http' or not _loopback_host(origin.hostname)
                        or origin.username is not None or origin.password is not None
                        or origin.path or origin.query or origin.fragment
                        or (origin.port or 80) != self.server.server_port
                        or any(char.isspace() for char in origins[0])):
                    raise ValueError()
        except (ValueError, TypeError):
            raise CommonsError(403, '拒绝非本机或端口不匹配的 Host/Origin；不提供 CORS。')
        if mutation:
            tokens = self.headers.get_all('X-Commons-Token', [])
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode('utf-8'), self.server.csrf_token.encode('ascii')):
                raise CommonsError(403, '写入需要 config 返回的 X-Commons-Token。')

    def _json_body(self):
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
            raise CommonsError(400, 'JSON 请求必须带唯一 Content-Length，不支持分块传输。')
        if not re.fullmatch(r'[0-9]+', lengths[0]) or len(lengths[0]) > 10:
            raise CommonsError(400, 'Content-Length 不合法。')
        size = int(lengths[0])
        if size > MAX_JSON_BYTES:
            raise CommonsError(400, 'JSON 请求不能超过 1MB。')
        if self.headers.get_content_type() != 'application/json':
            raise CommonsError(400, 'Content-Type 必须是 application/json。')
        try:
            raw = self.rfile.read(size)
        except OSError:
            raise CommonsError(400, '读取 JSON 请求超时或失败。')
        if len(raw) != size:
            raise CommonsError(400, 'JSON 请求长度与 Content-Length 不符。')
        return decode_json(raw)

    def _route(self):
        method = self.command
        mutation = method in ('POST', 'PATCH', 'PUT', 'DELETE')
        self._guard(mutation)
        try:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise ValueError()
            path = unquote(parsed.path, encoding='utf-8', errors='strict')
            params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
        except (ValueError, UnicodeError):
            raise CommonsError(400, '请求路径或查询参数不合法。')
        store = self.server.store
        if method == 'GET' and path in ('/', '/index.html'):
            if not self.server.index_path.exists():
                raise CommonsError(404, 'assets/index.html 尚未提供。')
            return 200, safe_read(self.server.index_path, 5 * MAX_JSON_BYTES), 'text/html; charset=utf-8'
        if method == 'GET' and path == '/api/config':
            return 200, dict(csrfToken=self.server.csrf_token, root=str(store.root), categories=CATEGORIES), None
        if method == 'GET' and path == '/api/posts':
            if set(params) - {'q', 'category', 'author', 'status'} or any(len(v) != 1 for v in params.values()):
                raise CommonsError(400, '帖子列表只接受单个 q/category/author/status 参数。')
            values = {key: params.get(key, [''])[0] for key in ('q', 'category', 'author', 'status')}
            return 200, {'posts': store.list_posts(query=values['q'], category=values['category'],
                                                   author=values['author'], status=values['status'])}, None
        if method == 'GET' and path == '/api/export':
            return 200, store.export(), None
        if method == 'POST' and path == '/api/posts':
            return 201, {'post': store.create(self._json_body())}, None
        if path.startswith('/api/posts/'):
            parts = path[len('/api/posts/'):].split('/')
            pid = valid_id(parts[0])
            if len(parts) == 1:
                if method == 'GET':
                    return 200, {'post': store.read(pid)}, None
                if method == 'PATCH':
                    return 200, {'post': store.edit(pid, self._json_body())}, None
            if len(parts) == 2:
                action = {('POST', 'comments'): store.comment,
                          ('PUT', 'like'): store.like,
                          ('PUT', 'adoptions'): store.adopt}.get((method, parts[1]))
                if action:
                    return 200, {'post': action(pid, self._json_body())}, None
        raise CommonsError(404, '接口或页面不存在；不提供任意磁盘文件。')

    def _handle(self):
        try:
            status, value, content_type = self._route()
            self._send(status, value, content_type or 'application/json; charset=utf-8')
        except CommonsError as error:
            self.close_connection = True
            self._send(error.status, error.as_json())
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self._send(500, CommonsError(500, '本地存储或服务处理失败；未提交的写入将回滚。').as_json())

    do_GET = _handle
    do_POST = _handle
    do_PATCH = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_OPTIONS = _handle
    do_HEAD = _handle


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CommonsError(400, '命令行参数不合法，请用 --help 查看子命令、必填参数和参数类型。', code='cli_arguments')

    def print_help(self, file=None):
        print(encode_json({'help': self.format_help()}), file=file or sys.stdout)


def build_parser():
    parser = JSONArgumentParser(description='仅供本机使用的 Agent Commons 社区存储。')
    parser.add_argument('--root', help='根目录；须放在子命令之前。')
    commands = parser.add_subparsers(dest='command', required=True, parser_class=JSONArgumentParser)
    commands.add_parser('init', help='初始化指定本机存储。')
    search = commands.add_parser('search', help='只返回匹配结果的前五条摘要。')
    search.add_argument('--query', required=True)
    search.add_argument('--limit', type=int, default=5)
    search.add_argument('--limit5', dest='limit', action='store_const', const=5)
    search.add_argument('--category', default='')
    search.add_argument('--project', default='')
    read = commands.add_parser('read', help='读取完整帖子。')
    read.add_argument('id')
    for name in ('post', 'edit', 'comment', 'like', 'adopt'):
        command = commands.add_parser(name, help='从 JSON 文件读取内容并写入本机存储。')
        if name != 'post':
            command.add_argument('id')
        command.add_argument('--file', required=True)
    export = commands.add_parser('export', help='导出全部完整帖子。')
    export.add_argument('--output', required=True)
    serve = commands.add_parser('serve', help='只绑定 127.0.0.1 的本机 API 与页面。')
    serve.add_argument('--port', type=int, default=8765)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        store = CommonsStore(args.root)
        if args.command == 'init':
            result = {'root': str(store.root), 'schemaVersion': 1, 'categories': CATEGORIES}
        elif args.command == 'search':
            result = {'posts': store.search(args.query, args.limit, args.category, args.project)}
        elif args.command == 'read':
            result = {'post': store.read(args.id)}
        elif args.command in ('post', 'edit', 'comment', 'like', 'adopt'):
            value = decode_json(safe_read(Path(args.file).expanduser(), MAX_JSON_BYTES))
            if args.command == 'post':
                result = {'post': store.create(value)}
            else:
                action = {'edit': store.edit, 'comment': store.comment,
                          'like': store.like, 'adopt': store.adopt}[args.command]
                result = {'post': action(args.id, value)}
        elif args.command == 'export':
            result = store.export()
            output = Path(args.output).expanduser().absolute()
            if not output.parent.is_dir():
                raise CommonsError(400, '导出文件的父目录不存在。')
            if output.resolve() == store.db_path or output.parent.resolve() in (store.posts_dir, store.journal_dir):
                raise CommonsError(400, '导出文件不能覆盖存储数据库、正文或恢复日志。')
            atomic_write(output, encode_json(result).encode('utf-8'))
            result = {'output': str(output), 'schemaVersion': 1, 'postCount': len(result['posts'])}
        else:
            with make_server(store, args.port) as server:
                print(encode_json({'url': 'http://127.0.0.1:%d' % server.server_port,
                                   'root': str(store.root)}), flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
            return 0
        print(encode_json(result))
        return 0
    except CommonsError as error:
        print(encode_json(error.as_json()))
        return 2 if error.status < 500 else 1
    except (OSError, sqlite3.Error, ValueError):
        print(encode_json(CommonsError(500, '本地文件或数据库操作失败，请检查路径和权限。').as_json()))
        return 1


if __name__ == '__main__':
    sys.exit(main())
