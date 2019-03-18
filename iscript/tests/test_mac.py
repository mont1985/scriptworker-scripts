#!/usr/bin/env python
# coding=utf-8
"""Test iscript.mac
"""
import arrow
from functools import partial
import mock
import os
import pexpect
import pytest
import iscript.mac as mac
from iscript.exceptions import IScriptError, TimeoutError, UnknownAppDir
from scriptworker_client.utils import makedirs


# helpers {{{1
async def noop_async(*args, **kwargs):
    pass


def touch(path):
    parent_dir = os.path.dirname(path)
    makedirs(parent_dir)
    with open(path, 'w') as fh:
        pass


# App {{{1
def test_app():
    """``App`` attributes can be set, and ``check_required_attrs`` raises if
    the required attrs aren't set.

    """
    a = mac.App()
    assert a.orig_path == ''
    a.orig_path = 'foo'
    assert a.orig_path == 'foo'
    a.check_required_attrs(['orig_path'])
    with pytest.raises(IScriptError):
        a.check_required_attrs(['app_path'])


# sign {{{1
@pytest.mark.asyncio
async def test_sign(mocker, tmpdir):
    """Render ``sign`` noop and verify we have complete code coverage.

    """
    config = {
        'mac_config': {
            'key': {
                'identity': 'id',
            },
        },
    }
    app = mac.App()
    app.parent_dir = str(tmpdir)
    entitlements_path = os.path.join(tmpdir, 'entitlements')
    app_path = os.path.join(tmpdir, 'foo.app')
    for p in list(mac.INITIAL_FILES_TO_SIGN) + ['Resources/MacOS/foo']:
        p = p.replace('*', 'foo')
        touch(os.path.join(app_path, p))
    mocker.patch.object(mac, 'run_command', new=noop_async)
    await mac.sign(config, app, 'key', entitlements_path)


# unlock_keychain {{{1
def fake_spawn(val, *args, **kwargs):
    return val


@pytest.mark.parametrize('results', ([1, 0], [0]))
@pytest.mark.asyncio
async def test_unlock_keychain_successful(results, mocker):
    """Mock a successful keychain unlock.

    """

    def fake_expect(*args, **kwargs):
        return results.pop(0)

    child = mocker.Mock()
    child.expect = fake_expect
    child.exitstatus = 0
    child.signalstatus = None
    mocker.patch.object(pexpect, 'spawn', new=partial(fake_spawn, child))
    await mac.unlock_keychain('x', 'y')


@pytest.mark.asyncio
async def test_unlock_keychain_timeout(mocker):
    """``unlock_keychain`` raises a ``TimeoutError`` on pexpect timeout.

    """

    def fake_expect(*args, **kwargs):
        raise pexpect.exceptions.TIMEOUT('foo')

    child = mocker.Mock()
    child.expect = fake_expect
    child.exitstatus = 0
    child.signalstatus = None
    mocker.patch.object(pexpect, 'spawn', new=partial(fake_spawn, child))
    with pytest.raises(TimeoutError):
        await mac.unlock_keychain('x', 'y')


@pytest.mark.asyncio
async def test_unlock_keychain_failure(mocker):
    """``unlock_keychain`` throws an ``IScriptError`` on non-zero exit status.

    """
    results = [0]

    def fake_expect(*args, **kwargs):
        return results.pop(0)

    child = mocker.Mock()
    child.expect = fake_expect
    child.exitstatus = 1
    child.signalstatus = None
    mocker.patch.object(pexpect, 'spawn', new=partial(fake_spawn, child))
    with pytest.raises(IScriptError):
        await mac.unlock_keychain('x', 'y')


# get_app_dir {{{1
@pytest.mark.parametrize('apps, raises', ((
    [], True,
), (
    ['foo.app'], False,
), (
    ['foo.notanapp'], True,
), (
    ['one.app', 'two.app'], True,
)))
def test_get_app_dir(tmpdir, apps, raises):
    """``get_app_dir`` returns the single ``.app`` dir in ``parent_dir``, and
    raises ``UnknownAppDir`` if there is greater or fewer than one ``.app``.

    """
    for app in apps:
        os.makedirs(os.path.join(tmpdir, app))

    if raises:
        with pytest.raises(UnknownAppDir):
            mac.get_app_dir(tmpdir)
    else:
        assert mac.get_app_dir(tmpdir) == os.path.join(tmpdir, apps[0])


# get_key_config {{{1
@pytest.mark.parametrize('key, config_key, raises', ((
    'dep', 'mac_config', False
), (
    'nightly', 'mac_config', False
), (
    'invalid_key', 'mac_config', True
), (
    'dep', 'invalid_config_key', True
)))
def test_get_config_key(key, config_key, raises):
    """``get_config_key`` returns the correct subconfig.

    """
    config = {
        'mac_config': {
            'dep': {
                'key': 'dep',
            },
            'nightly': {
                'key': 'nightly',
            },
        },
    }
    if raises:
        with pytest.raises(IScriptError):
            mac.get_key_config(config, key, config_key=config_key)
    else:
        assert mac.get_key_config(config, key, config_key=config_key) == config[config_key][key]


# get_app_paths {{{1
def test_get_app_paths():
    """``get_app_paths`` creates ``App`` objects with ``orig_path`` set to
    the cot-downloaded artifact paths.

    """
    config = {'work_dir': 'work'}
    task = {
        'payload': {
            'upstreamArtifacts': [{
                'paths': ['public/foo'],
                'taskId': 'task1',
            }, {
                'paths': ['public/bar', 'public/baz'],
                'taskId': 'task2',
            }],
        }
    }
    paths = []
    apps = mac.get_app_paths(config, task)
    for app in apps:
        assert isinstance(app, mac.App)
        paths.append(app.orig_path)
    assert paths == [
        'work/cot/task1/public/foo', 'work/cot/task2/public/bar',
        'work/cot/task2/public/baz'
    ]


# extract_all_apps {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_extract_all_apps(mocker, tmpdir, raises):
    """``extract_all_apps`` creates ``parent_dir`` and raises if any tar command
    fails. The ``run_command`` calls all start with a commandline that calls
    ``tar``.

    """

    async def fake_run_command(*args, **kwargs):
        assert args[0][0] == 'tar'
        if raises:
            raise IScriptError('foo')

    mocker.patch.object(mac, 'run_command', new=fake_run_command)
    work_dir = os.path.join(str(tmpdir), 'work')
    all_paths = [
        mac.App(orig_path=os.path.join(work_dir, 'orig1')),
        mac.App(orig_path=os.path.join(work_dir, 'orig2')),
        mac.App(orig_path=os.path.join(work_dir, 'orig3')),
    ]
    if raises:
        with pytest.raises(IScriptError):
            await mac.extract_all_apps(work_dir, all_paths)
    else:
        await mac.extract_all_apps(work_dir, all_paths)
        for i in ('0', '1', '2'):
            assert os.path.isdir(os.path.join(work_dir, i))


# create_all_app_zipfiles {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_create_all_app_zipfiles(mocker, tmpdir, raises):
    """``create_all_app_zipfiles`` calls ``zip -r``, and raises on failure.

    """

    async def fake_run_command(*args, **kwargs):
        assert args[0][0:2] == ['zip', '-r']
        if raises:
            raise IScriptError('foo')

    mocker.patch.object(mac, 'run_command', new=fake_run_command)
    all_paths = []
    work_dir = str(tmpdir)
    for i in range(3):
        parent_dir = os.path.join(work_dir, str(i))
        app_name = 'fx {}.app'.format(str(i))
        all_paths.append(mac.App(parent_dir=parent_dir, app_name=app_name))

    if raises:
        with pytest.raises(IScriptError):
            await mac.create_all_app_zipfiles(all_paths)
    else:
        await mac.create_all_app_zipfiles(all_paths)


# create_one_app_zipfile {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_create_one_app_zipfile(mocker, tmpdir, raises):
    """``create_one_app_zipfile`` calls the expected cmdline, and raises on
    failure.

    """
    work_dir = str(tmpdir)

    async def fake_run_command(*args, **kwargs):
        assert args[0] == [
            'zip', '-r', os.path.join(work_dir, 'target.zip'),
            '0/0.app', '1/1.app', '2/2.app'
        ]
        if raises:
            raise IScriptError('foo')

    mocker.patch.object(mac, 'run_command', new=fake_run_command)
    all_paths = []
    for i in range(3):
        all_paths.append(mac.App(app_path=os.path.join(work_dir, str(i), '{}.app'.format(i))))
    if raises:
        with pytest.raises(IScriptError):
            await mac.create_one_app_zipfile(work_dir, all_paths)
    else:
        await mac.create_one_app_zipfile(work_dir, all_paths)


# sign_all_apps {{{1
@pytest.mark.parametrize('raises', (True, False))
@pytest.mark.asyncio
async def test_sign_all_apps(mocker, tmpdir, raises):
    """``sign_all_apps`` calls ``sign`` and raises on failure.

    """
    key_config = {'x': 'y'}
    entitlements_path = 'fake_entitlements_path'
    work_dir = str(tmpdir)
    all_paths = [
        mac.App(parent_dir=os.path.join(work_dir, '0')),
        mac.App(parent_dir=os.path.join(work_dir, '1')),
        mac.App(parent_dir=os.path.join(work_dir, '2')),
    ]

    async def fake_sign(arg1, arg2, arg3):
        assert arg1 == key_config
        assert arg2 in all_paths
        assert arg3 == entitlements_path
        if raises:
            raise IScriptError('foo')

    mocker.patch.object(mac, 'sign', new=fake_sign)
    if raises:
        with pytest.raises(IScriptError):
            await mac.sign_all_apps(key_config, entitlements_path, all_paths)
    else:
        await mac.sign_all_apps(key_config, entitlements_path, all_paths)


# get_bundle_id {{{1
@pytest.mark.parametrize('task_id', (None, 'asdf'))
@pytest.mark.parametrize('counter', (None, 3))
def test_get_bundle_id(mocker, task_id, counter):
    """``get_bundle_id`` returns a unique bundle id

    """
    now = mock.MagicMock()
    now.timestamp = 51
    now.microsecond = 50
    mocker.patch.object(arrow, 'utcnow', return_value=now)
    base = 'org.foo.base'
    expected = base
    if task_id:
        expected = '{}.{}.{}{}'.format(expected, task_id, now.timestamp, now.microsecond)
        mocker.patch.object(os, 'environ', new={'TASK_ID': task_id})
    else:
        expected = '{}.None.{}{}'.format(expected, now.timestamp, now.microsecond)
    if counter:
        expected = '{}.{}'.format(expected, counter)
    assert mac.get_bundle_id(base, counter=counter) == expected


# get_uuid_from_log {{{1
@pytest.mark.parametrize('uuid, raises', ((
    '07307e2c-db26-494c-8630-cfa239d4b86b', False,
), (
    'd4d31c49-c075-4ea1-bb7f-150c74f608e1', False,
), (
    'd4d31c49-c075-4ea1-bb7f-150c74f608e1', 'missing file',
), (
    '%%%%\\\\=', 'missing uuid',
)))
def test_get_uuid_from_log(tmpdir, uuid, raises):
    """``get_uuid_from_log`` returns the correct uuid from the logfile if present.
    It raises if it has problems finding the uuid in the log.

    """
    log_path = os.path.join(str(tmpdir), 'log')
    if raises != 'missing file':
        with open(log_path, 'w') as fh:
            fh.write("foo\nbar\nbaz\n RequestUUID = {} \nblah\n".format(uuid))
    if raises:
        with pytest.raises(IScriptError):
            mac.get_uuid_from_log(log_path)
    else:
        assert mac.get_uuid_from_log(log_path) == uuid
