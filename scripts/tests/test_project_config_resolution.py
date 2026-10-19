"""Project設定解決の文書契約を実Git fixtureで検証する。

解決器は手順を検証するテスト専用ハーネスであり、配布CLIではない。
GitHub照合・実エージェントの手順実行はこのテストとは別の検証。
"""

import copy
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DOC = Path('.agents/docs/aide/03-project.md')
DISCOVERY_COMMANDS = (
    'git rev-parse --is-bare-repository',
    'git rev-parse --path-format=absolute --show-toplevel',
    'git rev-parse --path-format=absolute --git-dir',
    'git rev-parse --path-format=absolute --git-common-dir',
    'git worktree list --porcelain -z',
)
TEMPLATE = {
    'schema_version': 1,
    'repository': None,
    'project_defaults': {'title': None, 'visibility': 'PRIVATE'},
    'project': None,
}
LIVE = {
    **TEMPLATE,
    'repository': 'fixture-owner/fixture-repository',
    'project': {
        'owner': 'fixture-owner',
        'owner_type': 'User',
        'number': 7,
        'node_id': 'fixture-private-node',
        'title': 'fixture-private-title',
        'url': 'https://github.com/users/fixture-owner/projects/7',
        'visibility': 'PRIVATE',
        'repository': 'fixture-owner/fixture-repository',
    },
}


class ContractStop(ValueError):
    pass


def git(cwd, command, env):
    result = subprocess.run(
        shlex.split(command), cwd=cwd, env=env, capture_output=True, check=False,
    )
    if result.returncode:
        raise ContractStop('Git discovery failed')
    return result.stdout


def regular_file(root, relative):
    path = root
    for component in Path(relative).parts[:-1]:
        path = path / component
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ContractStop('Invalid configuration directory')
    path = root / relative
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ContractStop('Invalid configuration file')
    return path


def config_json(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate key')
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError):
        raise ContractStop('Invalid configuration encoding or JSON') from None


def origin_identity(raw):
    raw = raw.decode('utf-8').strip()
    url = urlsplit(raw)
    if url.scheme in ('https', 'ssh') and url.hostname and not url.query and not url.fragment:
        if url.password or (url.scheme == 'https' and url.username):
            raise ContractStop('Invalid origin')
        host, path = url.hostname, url.path.lstrip('/')
    else:
        match = re.fullmatch(r'(?:[^@/:]+@)?([^/:]+):([^:]+)', raw)
        if not match:
            raise ContractStop('Invalid origin')
        host, path = match.groups()
    if path.endswith('.git'):
        path = path[:-4]
    if not re.fullmatch(r'[^/\s]+/[^/\s]+', path):
        raise ContractStop('Invalid origin')
    return host, path


def resolve_documented_config(cwd, env):
    """正本文書の読み取り専用手順を、成功／停止条件まで実行する。"""
    if any(key in env for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR')):
        raise ContractStop('Git directory override is not allowed')
    commands = extract_commands()
    outputs = [git(cwd, command, env) for command in commands]
    if outputs[0] != b'false\n':
        raise ContractStop('A non-bare working tree is required')
    current, git_dir, common = [Path(os.fsdecode(value.removesuffix(b'\n'))).resolve() for value in outputs[1:4]]
    records = outputs[4].split(b'\0\0')
    first = records[0].split(b'\0')
    if not first or not first[0].startswith(b'worktree ') or b'bare' in first:
        raise ContractStop('Primary working tree is unavailable')
    primary = Path(os.fsdecode(first[0][len(b'worktree '):]))
    if not primary.is_absolute() or not primary.is_dir() or primary.is_symlink():
        raise ContractStop('Invalid primary working tree')
    primary = primary.resolve()
    primary_outputs = [git(primary, command, env) for command in commands[:4]]
    if primary_outputs[0] != b'false\n':
        raise ContractStop('Primary working tree is bare')
    primary_root, primary_git, primary_common = [
        Path(os.fsdecode(value.removesuffix(b'\n'))).resolve() for value in primary_outputs[1:]
    ]
    if primary_root != primary or primary_git != primary_common or primary_common != common:
        raise ContractStop('Primary working tree is from another clone')
    if current != primary and os.path.lexists(current / '.agents/project.local.json'):
        raise ContractStop('Linked local configuration is not allowed')
    identities = [origin_identity(git(path, 'git remote get-url origin', env)) for path in (current, primary)]
    if identities[0] != identities[1]:
        raise ContractStop('Origin mismatch')
    configurations = {
        path: config_json(regular_file(path, '.agents/project.json'))
        for path in {current, primary}
    }
    for value in configurations.values():
        if value == TEMPLATE and type(value['schema_version']) is not int:
            raise ContractStop('Invalid neutral schema version')
    shared_value = configurations[primary]
    if any(value != shared_value for value in configurations.values()):
        raise ContractStop('Shared configuration mismatch between working trees')
    local = primary / '.agents/project.local.json'
    if shared_value != TEMPLATE:
        validate_live_config(shared_value, identities[0])
        if os.path.lexists(local):
            raise ContractStop('Shared and local configuration conflict')
        for path in {current, primary}:
            if not git(path, 'git ls-files -- .agents/project.json', env):
                raise ContractStop('Shared configuration must be tracked')
            ignored = subprocess.run(
                ['git', 'check-ignore', '-q', '--no-index', '--', '.agents/project.json'],
                cwd=path, env=env, capture_output=True,
            )
            if ignored.returncode != 1:
                raise ContractStop('Shared configuration must not be ignored')
        return primary, primary / '.agents/project.json'
    if not os.path.lexists(local):
        if current != primary:
            raise ContractStop('Initialize from the primary working tree')
        return primary, None
    local = regular_file(primary, '.agents/project.local.json')
    if stat.S_IMODE(local.stat().st_mode) != 0o600:
        raise ContractStop('Local configuration permissions are invalid')
    tracked = git(primary, 'git ls-files -- .agents/project.local.json', env)
    ignored = subprocess.run(
        ['git', 'check-ignore', '-q', '--no-index', '--', '.agents/project.local.json'],
        cwd=primary, env=env, capture_output=True,
    )
    if tracked or ignored.returncode:
        raise ContractStop('Local configuration is not private to the clone')
    validate_live_config(config_json(local), identities[0])
    return primary, local


def validate_live_config(value, identity):
    project = value.get('project') if isinstance(value, dict) else None
    if (
        not isinstance(value, dict) or set(value) != set(TEMPLATE)
        or type(value.get('schema_version')) is not int or value['schema_version'] != 1
        or value.get('repository') != identity[1]
        or value.get('project_defaults') != TEMPLATE['project_defaults']
        or not isinstance(project, dict) or set(project) != set(LIVE['project'])
        or project.get('repository') != value['repository']
        or project.get('owner_type') not in ('User', 'Organization')
        or project.get('owner') != value['repository'].split('/')[0]
        or type(project.get('number')) is not int or project['number'] <= 0
        or project.get('visibility') not in ('PRIVATE', 'PUBLIC')
        or any(not isinstance(project.get(key), str) or not project[key] for key in ('node_id', 'title', 'url'))
    ):
        raise ContractStop('Local configuration schema or identity is invalid')
    kind = 'users' if project['owner_type'] == 'User' else 'orgs'
    expected_url = f"https://{identity[0]}/{kind}/{project['owner']}/projects/{project['number']}"
    if project['url'] != expected_url:
        raise ContractStop('Project URL does not match the origin host and identity')


def extract_commands():
    text = (ROOT / PROJECT_DOC).read_text(encoding='utf-8')
    section = text.split('### 読み取り専用の設定解決', 1)
    if len(section) != 2:
        raise ContractStop('Documented resolution procedure is missing')
    blocks = re.findall(r'^```sh\n(.*?)^```$', section[1], flags=re.MULTILINE | re.DOTALL)
    if len(blocks) != 1:
        raise ContractStop('Discovery commands are missing or ambiguous')
    commands = tuple(blocks[0].strip().splitlines())
    if commands != DISCOVERY_COMMANDS:
        raise ContractStop('Discovery command contract changed')
    return commands


class ProjectConfigResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.primary = self.root / 'primary space\nline'
        self.linked = self.root / 'linked space\nline'
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.env.update(GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid', GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
        seed = self.root / 'seed'
        self.command('git', 'init', '--initial-branch=main', str(seed), cwd=self.root)
        (seed / '.agents').mkdir()
        (seed / '.agents/project.json').write_text(json.dumps(TEMPLATE))
        (seed / '.gitignore').write_text('/.agents/project.local.json\n')
        self.command('git', 'add', '.agents/project.json', '.gitignore', cwd=seed)
        self.command('git', 'commit', '-m', 'fixture', cwd=seed)
        self.command('git', 'clone', '--no-hardlinks', str(seed), str(self.primary), cwd=self.root)
        self.command('git', 'remote', 'set-url', 'origin', 'https://github.com/fixture-owner/fixture-repository.git')
        self.command('git', 'worktree', 'add', str(self.linked), '-b', 'topic', 'origin/main')
        self.local = self.primary / '.agents/project.local.json'
        self.write_live(LIVE)

    def command(self, *args, cwd=None):
        result = subprocess.run(args, cwd=cwd or self.primary, env=self.env, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def write_live(self, data):
        self.local.write_text(json.dumps(data))
        self.local.chmod(0o600)

    def snapshot(self):
        result = {}
        for base in (self.primary, self.linked):
            for path in base.rglob('*'):
                if '.git' in path.relative_to(base).parts:
                    continue
                meta = path.lstat()
                payload = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
                result[str(path)] = (meta.st_mode, payload)
        return result

    def test_documented_commands_discover_primary_and_linked_read_only(self):
        self.assertIn('### 読み取り専用の設定解決', (ROOT / PROJECT_DOC).read_text())
        before = self.snapshot()
        for cwd in (self.primary, self.linked, self.linked / '.agents'):
            self.assertEqual(resolve_documented_config(cwd, self.env), (self.primary, self.local))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.linked / '.agents/project.local.json').exists())

    def test_entry_points_delegate_to_single_configuration_owner(self):
        document = (ROOT / PROJECT_DOC).read_text()
        for contract in (
            'NUL', '先頭レコード', 'common Git directory', '別clone', 'リンク側のローカル設定',
            'symlink', 'mode `0600`', '完全なschema', '設定値を出力しない',
            'GitHubの読み取り専用照会', 'フォールバックせず停止', 'GIT_DIR',
            'GIT_WORK_TREE', 'GIT_COMMON_DIR', '追跡されていない',
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, document)
        for path in ('AGENTS.md', '.agents/templates/aide/AGENTS.md.template', '.agents/skills/aide-workflow/SKILL.md'):
            with self.subTest(path=path):
                text = (ROOT / path).read_text()
                self.assertIn('03-project.md#読み取り専用の設定解決', text)
                self.assertIn('主作業ツリー', text)
                self.assertNotIn('ローカル設定がなくテンプレート設定の `project` が `null` の場合は', text)
        init = (ROOT / '.agents/skills/aide-init/SKILL.md').read_text()
        self.assertIn('03-project.md#読み取り専用の設定解決', init)
        self.assertIn('aide-init`は主作業ツリーからのみ実行', init)

    def test_linked_local_configuration_is_rejected_without_fallback(self):
        override = self.linked / '.agents/project.local.json'
        for kind in ('identical', 'symlink', 'directory'):
            with self.subTest(kind=kind):
                if kind == 'identical':
                    override.write_bytes(self.local.read_bytes())
                    override.chmod(0o600)
                elif kind == 'symlink':
                    override.symlink_to(self.local)
                else:
                    override.mkdir()
                before = self.snapshot()
                with self.assertRaisesRegex(ContractStop, 'Linked local'):
                    resolve_documented_config(self.linked, self.env)
                self.assertEqual(self.snapshot(), before)
                override.rmdir() if kind == 'directory' else override.unlink()

    def test_source_placement_permissions_and_git_privacy_fail_closed(self):
        for kind in ('symlink', 'directory', 'permissions', 'tracked', 'not-ignored', 'parent-symlink'):
            with self.subTest(kind=kind):
                original = self.local.read_bytes()
                external = self.root / 'external-config'
                external.write_bytes(original)
                if kind == 'symlink':
                    self.local.unlink()
                    self.local.symlink_to(external)
                elif kind == 'directory':
                    self.local.unlink()
                    self.local.mkdir()
                elif kind == 'permissions':
                    self.local.chmod(0o644)
                elif kind == 'tracked':
                    self.command('git', 'add', '-f', '.agents/project.local.json')
                elif kind == 'not-ignored':
                    (self.primary / '.gitignore').write_text('')
                else:
                    (self.primary / '.agents').rename(self.primary / 'moved-agents')
                    (self.primary / '.agents').symlink_to(self.primary / 'moved-agents', target_is_directory=True)
                before = self.snapshot()
                with self.assertRaises((ContractStop, OSError)):
                    resolve_documented_config(self.linked, self.env)
                self.assertEqual(self.snapshot(), before)
                if kind == 'parent-symlink':
                    (self.primary / '.agents').unlink()
                    (self.primary / 'moved-agents').rename(self.primary / '.agents')
                elif kind == 'directory':
                    self.local.rmdir()
                elif kind == 'symlink':
                    self.local.unlink()
                elif kind == 'tracked':
                    self.command('git', 'reset', '--', '.agents/project.local.json')
                (self.primary / '.gitignore').write_text('/.agents/project.local.json\n')
                self.write_live(LIVE)

    def test_malformed_schema_and_identity_do_not_disclose_values(self):
        variants = []
        for section, key, value in (
            (None, 'repository', 'other-owner/other-repository'),
            (None, 'schema_version', 9),
            (None, 'project', None),
            ('project', 'repository', 'other-owner/other-repository'),
            ('project', 'url', 'https://other.example/users/fixture-owner/projects/7'),
            ('project', 'number', True),
            ('project', 'owner_type', 'Other'),
        ):
            data = copy.deepcopy(LIVE)
            (data if section is None else data[section])[key] = value
            variants.append(json.dumps(data))
        incomplete = copy.deepcopy(LIVE)
        del incomplete['project']['node_id']
        variants.extend((json.dumps(incomplete), '{invalid-json', '[]', b'\xff'))
        for payload in variants:
            with self.subTest(payload_type=type(payload).__name__):
                self.local.write_bytes(payload if isinstance(payload, bytes) else payload.encode())
                before = self.snapshot()
                with self.assertRaises((ContractStop, ValueError)) as caught:
                    resolve_documented_config(self.linked, self.env)
                for private in ('fixture-private-title', 'fixture-private-node', LIVE['project']['url']):
                    self.assertNotIn(private, str(caught.exception))
                self.assertEqual(self.snapshot(), before)

    def test_missing_primary_config_stops_linked_initialization(self):
        self.local.unlink()
        before = self.snapshot()
        self.assertEqual(resolve_documented_config(self.primary, self.env), (self.primary, None))
        with self.assertRaisesRegex(ContractStop, 'Initialize from the primary'):
            resolve_documented_config(self.linked, self.env)
        self.assertEqual(self.snapshot(), before)

    def test_git_directory_overrides_and_origin_mismatch_stop(self):
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR'):
            with self.subTest(key=key), self.assertRaisesRegex(ContractStop, 'override'):
                resolve_documented_config(self.linked, dict(self.env, **{key: str(self.primary)}))
        self.command('git', 'config', 'extensions.worktreeConfig', 'true')
        self.command('git', 'config', '--unset-all', 'remote.origin.url')
        self.command('git', 'config', '--worktree', 'remote.origin.url', 'https://github.com/fixture-owner/fixture-repository.git')
        self.command('git', 'config', '--worktree', 'remote.origin.url', 'https://other.example/fixture-owner/fixture-repository.git', cwd=self.linked)
        self.assertNotEqual(
            self.command('git', 'remote', 'get-url', 'origin'),
            self.command('git', 'remote', 'get-url', 'origin', cwd=self.linked),
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ContractStop, 'Origin mismatch'):
            resolve_documented_config(self.linked, self.env)
        self.assertEqual(self.snapshot(), before)

    def test_same_origin_separate_clone_is_not_a_primary_source(self):
        other = self.root / 'other-clone'
        self.command('git', 'clone', '--no-hardlinks', str(self.primary), str(other))
        self.command('git', 'remote', 'set-url', 'origin', 'https://github.com/fixture-owner/fixture-repository.git', cwd=other)
        real_git = git

        def substitute_primary(cwd, command, env):
            if cwd == self.linked and command == DISCOVERY_COMMANDS[-1]:
                return b'worktree ' + os.fsencode(other) + b'\0\0'
            return real_git(cwd, command, env)

        before = self.snapshot()
        with mock.patch(__name__ + '.git', side_effect=substitute_primary):
            with self.assertRaisesRegex(ContractStop, 'another clone'):
                resolve_documented_config(self.linked, self.env)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_template_or_git_discovery_stops_without_values(self):
        for root in (self.primary, self.linked):
            template = root / '.agents/project.json'
            template.write_text(json.dumps(LIVE))
            before = self.snapshot()
            with self.assertRaisesRegex(ContractStop, 'Shared configuration mismatch'):
                resolve_documented_config(self.linked, self.env)
            self.assertEqual(self.snapshot(), before)
            template.write_text(json.dumps(TEMPLATE))
        with self.assertRaisesRegex(ContractStop, 'Git discovery failed'):
            resolve_documented_config(self.root, self.env)

    def enable_shared(self):
        self.local.unlink()
        for root in (self.primary, self.linked):
            (root / '.agents/project.json').write_text(json.dumps(LIVE))

    def test_shared_primary_and_linked_resolve_without_local_writes(self):
        self.enable_shared()
        before = self.snapshot()
        for root in (self.primary, self.linked, self.linked / '.agents'):
            self.assertEqual(
                resolve_documented_config(root, self.env),
                (self.primary, self.primary / '.agents/project.json'),
            )
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.local.exists())

    def test_same_repository_new_clone_uses_shared_settings_without_init(self):
        self.enable_shared()
        self.command('git', 'add', '.agents/project.json')
        self.command('git', 'commit', '-m', 'fixture shared configuration')
        other = self.root / 'team-clone'
        self.command('git', 'clone', '--no-hardlinks', str(self.primary), str(other))
        self.command('git', 'remote', 'set-url', 'origin', 'https://github.com/fixture-owner/fixture-repository.git', cwd=other)
        self.assertEqual(resolve_documented_config(other, self.env), (other, other / '.agents/project.json'))
        self.assertFalse((other / '.agents/project.local.json').exists())

    def test_shared_and_local_conflict_even_if_identical(self):
        self.enable_shared()
        self.write_live(LIVE)
        before = self.snapshot()
        with self.assertRaisesRegex(ContractStop, 'conflict'):
            resolve_documented_config(self.primary, self.env)
        self.assertEqual(self.snapshot(), before)

    def test_shared_invalid_schema_and_origin_stop_without_disclosure(self):
        self.enable_shared()
        variants = []
        for mutate in (
            lambda v: v.update(repository='other-owner/other-repository'),
            lambda v: v.update(schema_version=True),
            lambda v: v.update(token='fixture-secret'),
            lambda v: v['project'].update(number=True),
            lambda v: v['project'].update(url='https://other.example/project'),
            lambda v: v['project'].pop('node_id'),
        ):
            value = copy.deepcopy(LIVE)
            mutate(value)
            variants.append(json.dumps(value))
        variants.extend(('{invalid-json', '[]', json.dumps({**TEMPLATE, 'project': LIVE['project']})))
        for payload in variants:
            with self.subTest(payload_type=type(payload).__name__):
                for root in (self.primary, self.linked):
                    (root / '.agents/project.json').write_text(payload)
                before = self.snapshot()
                with self.assertRaises(ContractStop) as caught:
                    resolve_documented_config(self.linked, self.env)
                for private in ('fixture-private-title', 'fixture-private-node', 'fixture-secret'):
                    self.assertNotIn(private, str(caught.exception))
                self.assertEqual(self.snapshot(), before)

    def test_shared_file_must_be_tracked_and_not_ignored(self):
        self.enable_shared()
        self.command('git', 'rm', '--cached', '.agents/project.json')
        with self.assertRaisesRegex(ContractStop, 'tracked'):
            resolve_documented_config(self.primary, self.env)
        self.command('git', 'add', '.agents/project.json')
        (self.primary / '.gitignore').write_text('/.agents/project.json\n')
        with self.assertRaisesRegex(ContractStop, 'not be ignored'):
            resolve_documented_config(self.primary, self.env)

    def test_shared_symlink_and_linked_revision_mismatch_stop(self):
        self.enable_shared()
        shared = self.primary / '.agents/project.json'
        external = self.root / 'shared-external'
        external.write_bytes(shared.read_bytes())
        shared.unlink()
        shared.symlink_to(external)
        with self.assertRaises((ContractStop, OSError)):
            resolve_documented_config(self.linked, self.env)
        shared.unlink()
        shared.write_bytes(external.read_bytes())
        (self.linked / '.agents/project.json').write_text(json.dumps(TEMPLATE))
        before = self.snapshot()
        with self.assertRaisesRegex(ContractStop, 'mismatch'):
            resolve_documented_config(self.linked, self.env)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_shared_does_not_fall_back_to_valid_local(self):
        shared = self.primary / '.agents/project.json'
        for payload in ('{invalid-json', json.dumps({**TEMPLATE, 'repository': 'other/repo'})):
            shared.write_text(payload)
            before = self.snapshot()
            with self.assertRaises(ContractStop):
                resolve_documented_config(self.primary, self.env)
            self.assertEqual(self.snapshot(), before)

    def test_neutral_schema_rejects_boolean_version(self):
        invalid = {**TEMPLATE, 'schema_version': True}
        for root in (self.primary, self.linked):
            (root / '.agents/project.json').write_text(json.dumps(invalid))
        with self.assertRaisesRegex(ContractStop, 'schema version'):
            resolve_documented_config(self.linked, self.env)

    def test_duplicate_keys_rejected_in_shared_and_local_configuration(self):
        duplicate = '{"repository":"hidden-value",' + json.dumps(LIVE)[1:]
        self.local.write_text(duplicate)
        with self.assertRaisesRegex(ContractStop, 'JSON') as caught:
            resolve_documented_config(self.primary, self.env)
        self.assertNotIn('hidden-value', str(caught.exception))
        self.local.unlink()
        (self.primary / '.agents/project.json').write_text(duplicate)
        with self.assertRaisesRegex(ContractStop, 'JSON'):
            resolve_documented_config(self.primary, self.env)


if __name__ == '__main__':
    unittest.main()
