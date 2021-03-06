# -*- coding: utf-8 -*-
'''
    :codeauthor: :email:`Pedro Algarvio (pedro@algarvio.me)`


    ====================================
    Custom Salt TestCase Implementations
    ====================================

    Custom reusable :class:`TestCase<python2:unittest.TestCase>`
    implementations.
'''

# Import python libs
import os
import re
import sys
import json
import errno
import signal
import logging
import subprocess
from datetime import datetime, timedelta

# Import salt testing libs
from salttesting.unit import TestCase
from salttesting.helpers import RedirectStdStreams
from salttesting.runtests import RUNTIME_VARS
from salttesting.mixins import AdaptedConfigurationTestCaseMixIn, SaltClientTestCaseMixIn

STATE_FUNCTION_RUNNING_RE = re.compile(
    r'''The function (?:"|')(?P<state_func>.*)(?:"|') is running as PID '''
    r'(?P<pid>[\d]+) and was started at (?P<date>.*) with jid (?P<jid>[\d]+)'
)
SCRIPT_TEMPLATES = {
    'salt': [
        'from salt.scripts import salt_main\n',
        'if __name__ == \'__main__\':'
        '    salt_main()'
    ],
    'salt-api': [
        'import salt.cli\n',
        'def main():',
        '    sapi = salt.cli.SaltAPI()',
        '    sapi.run()\n',
        'if __name__ == \'__main__\':',
        '    main()'
    ],
    'common': [
        'from salt.scripts import salt_{0}\n',
        'if __name__ == \'__main__\':',
        '    salt_{0}()'
    ]
}

log = logging.getLogger(__name__)


class ShellTestCase(TestCase, AdaptedConfigurationTestCaseMixIn):
    '''
    Execute a test for a shell command
    '''

    def get_script_path(self, script_name):
        '''
        Return the path to a testing runtime script
        '''
        if not os.path.isdir(RUNTIME_VARS.TMP_SCRIPT_DIR):
            os.makedirs(RUNTIME_VARS.TMP_SCRIPT_DIR)

        script_path = os.path.join(RUNTIME_VARS.TMP_SCRIPT_DIR, script_name)
        if not os.path.isfile(script_path):
            log.debug('Generating {0}'.format(script_path))

            # Late import
            import salt.utils

            with salt.utils.fopen(script_path, 'w') as sfh:
                script_template = SCRIPT_TEMPLATES.get(script_name, None)
                if script_template is None:
                    script_template = SCRIPT_TEMPLATES.get('common', None)
                if script_template is None:
                    raise RuntimeError(
                        '{0} does not know how to handle the {1} script'.format(
                            self.__class__.__name__,
                            script_name
                        )
                    )
                sfh.write(
                    #'#!/usr/bin/env python{0}.{1}\n'.format(*sys.version_info),
                    '\n'.join(script_template).format(script_name.replace('salt-', ''))
                )

        return script_path

    def run_salt(self, arg_str, with_retcode=False, catch_stderr=False):
        r'''
        Run the ``salt`` CLI tool with the provided arguments

        .. code-block:: python

            class MatchTest(ShellTestCase):
                def test_list(self):
                    """
                    test salt -L matcher
                    """
                    data = self.run_salt('-L minion test.ping')
                    data = '\n'.join(data)
                    self.assertIn('minion', data)
        '''
        arg_str = '-c {0} {1}'.format(self.get_config_dir(), arg_str)
        return self.run_script('salt', arg_str, with_retcode=with_retcode, catch_stderr=catch_stderr)

    def run_ssh(self, arg_str, with_retcode=False, catch_stderr=False):
        '''
        Execute salt-ssh
        '''
        arg_str = '-c {0} -i --priv {1} --roster-file {2} localhost {3} --out=json'.format(
            self.get_config_dir(),
            os.path.join(RUNTIME_VARS.TMP_CONF_DIR, 'key_test'),
            os.path.join(RUNTIME_VARS.TMP_CONF_DIR, 'roster'),
            arg_str
        )
        return self.run_script('salt-ssh', arg_str, with_retcode=with_retcode, catch_stderr=catch_stderr, raw=True)

    def run_run(self, arg_str, with_retcode=False, catch_stderr=False):
        '''
        Execute salt-run
        '''
        arg_str = '-c {0} {1}'.format(self.get_config_dir(), arg_str)
        return self.run_script('salt-run', arg_str, with_retcode=with_retcode, catch_stderr=catch_stderr)

    def run_run_plus(self, fun, options='', *arg, **kwargs):
        '''
        Execute Salt run and the salt run function and return the data from
        each in a dict
        '''
        ret = {}
        ret['out'] = self.run_run(
            '{0} {1} {2}'.format(options, fun, ' '.join(arg)), catch_stderr=kwargs.get('catch_stderr', None)
        )
        # Late import
        import salt.config
        import salt.runner

        opts = salt.config.master_config(
            self.get_config_file_path('master')
        )
        opts.update({'doc': False, 'fun': fun, 'arg': arg})
        with RedirectStdStreams():
            runner = salt.runner.Runner(opts)
            ret['fun'] = runner.run()
        return ret

    def run_key(self, arg_str, catch_stderr=False, with_retcode=False):
        '''
        Execute salt-key
        '''
        arg_str = '-c {0} {1}'.format(self.get_config_dir(), arg_str)
        return self.run_script(
            'salt-key',
            arg_str,
            catch_stderr=catch_stderr,
            with_retcode=with_retcode
        )

    def run_cp(self, arg_str, with_retcode=False, catch_stderr=False):
        '''
        Execute salt-cp
        '''
        arg_str = '--config-dir {0} {1}'.format(self.get_config_dir(), arg_str)
        return self.run_script('salt-cp', arg_str, with_retcode=with_retcode, catch_stderr=catch_stderr)

    def run_call(self, arg_str, with_retcode=False, catch_stderr=False):
        arg_str = '--config-dir {0} {1}'.format(self.get_config_dir(), arg_str)
        return self.run_script('salt-call', arg_str, with_retcode=with_retcode, catch_stderr=catch_stderr)

    def run_cloud(self, arg_str, catch_stderr=False, timeout=None):
        '''
        Execute salt-cloud
        '''
        arg_str = '-c {0} {1}'.format(self.get_config_dir(), arg_str)
        return self.run_script('salt-cloud', arg_str, catch_stderr, timeout)

    def run_script(self,
                   script,
                   arg_str,
                   catch_stderr=False,
                   with_retcode=False,
                   timeout=None,
                   raw=False):
        '''
        Execute a script with the given argument string
        '''
        script_path = self.get_script_path(script)
        if not os.path.isfile(script_path):
            return False

        python_path = os.environ.get('PYTHONPATH', None)
        cmd = 'PYTHONPATH='
        if python_path is not None:
            cmd += '{0}:'.format(python_path)

        cmd += '{0} '.format(':'.join(sys.path[1:]))
        cmd += 'python{0}.{1} '.format(*sys.version_info)
        cmd += '{0} '.format(script_path)
        cmd += '{0} '.format(arg_str)

        popen_kwargs = {
            'shell': True,
            'stdout': subprocess.PIPE
        }

        if catch_stderr is True:
            popen_kwargs['stderr'] = subprocess.PIPE

        if not sys.platform.lower().startswith('win'):
            popen_kwargs['close_fds'] = True

            def detach_from_parent_group():
                # detach from parent group (no more inherited signals!)
                os.setpgrp()

            popen_kwargs['preexec_fn'] = detach_from_parent_group

        elif sys.platform.lower().startswith('win') and timeout is not None:
            raise RuntimeError('Timeout is not supported under windows')

        process = subprocess.Popen(cmd, **popen_kwargs)

        if timeout is not None:
            stop_at = datetime.now() + timedelta(seconds=timeout)
            term_sent = False
            while True:
                process.poll()

                if datetime.now() > stop_at:
                    if term_sent is False:
                        # Kill the process group since sending the term signal
                        # would only terminate the shell, not the command
                        # executed in the shell
                        os.killpg(os.getpgid(process.pid), signal.SIGINT)
                        term_sent = True
                        continue

                    try:
                        # As a last resort, kill the process group
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except OSError as exc:
                        if exc.errno != 3:
                            raise

                    out = [
                        'Process took more than {0} seconds to complete. '
                        'Process Killed!'.format(timeout)
                    ]
                    if catch_stderr:
                        err = ['Process killed, unable to catch stderr output']
                        if with_retcode:
                            return out, err, process.returncode
                        else:
                            return out, err
                    if with_retcode:
                        return out, process.returncode
                    else:
                        return out

                if process.returncode is not None:
                    break

        if catch_stderr:
            if sys.version_info < (2, 7):
                # On python 2.6, the subprocess'es communicate() method uses
                # select which, is limited by the OS to 1024 file descriptors
                # We need more available descriptors to run the tests which
                # need the stderr output.
                # So instead of .communicate() we wait for the process to
                # finish, but, as the python docs state "This will deadlock
                # when using stdout=PIPE and/or stderr=PIPE and the child
                # process generates enough output to a pipe such that it
                # blocks waiting for the OS pipe buffer to accept more data.
                # Use communicate() to avoid that." <- a catch, catch situation
                #
                # Use this work around were it's needed only, python 2.6
                process.wait()
                out = process.stdout.read()
                err = process.stderr.read()
            else:
                out, err = process.communicate()
            # Force closing stderr/stdout to release file descriptors
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            # pylint: disable=maybe-no-member
            try:
                if with_retcode:
                    if out is not None and err is not None:
                        if not raw:
                            return out.splitlines(), err.splitlines(), process.returncode
                        else:
                            return out, err, process.returncode
                    return out.splitlines(), [], process.returncode
                else:
                    if out is not None and err is not None:
                        if not raw:
                            return out.splitlines(), err.splitlines()
                        else:
                            return out, err
                    if not raw:
                        return out.splitlines(), []
                    else:
                        return out, []
            finally:
                try:
                    process.terminate()
                except OSError as err:
                    # process already terminated
                    pass
            # pylint: enable=maybe-no-member

        data = process.communicate()
        process.stdout.close()

        try:
            if with_retcode:
                if not raw:
                    return data[0].splitlines(), process.returncode
                else:
                    return data[0], process.returncode
            else:
                if not raw:
                    return data[0].splitlines()
                else:
                    return data[0]
        finally:
            try:
                process.terminate()
            except OSError as err:
                # process already terminated
                pass


class ModuleCase(TestCase, SaltClientTestCaseMixIn):
    '''
    Execute a module function
    '''

    def minion_run(self, _function, *args, **kw):
        '''
        Run a single salt function on the 'minion' target and condition
        the return down to match the behavior of the raw function call
        '''
        return self.run_function(_function, args, **kw)

    def run_function(self, function, arg=(), minion_tgt='minion', timeout=25,
                     **kwargs):
        '''
        Run a single salt function and condition the return down to match the
        behavior of the raw function call
        '''
        know_to_return_none = (
            'file.chown', 'file.chgrp', 'ssh.recv_known_host'
        )
        orig = self.client.cmd(
            minion_tgt, function, arg, timeout=timeout, kwarg=kwargs
        )

        if minion_tgt not in orig:
            self.skipTest(
                'WARNING(SHOULD NOT HAPPEN #1935): Failed to get a reply '
                'from the minion \'{0}\'. Command output: {1}'.format(
                    minion_tgt, orig
                )
            )
        elif orig[minion_tgt] is None and function not in know_to_return_none:
            self.skipTest(
                'WARNING(SHOULD NOT HAPPEN #1935): Failed to get \'{0}\' from '
                'the minion \'{1}\'. Command output: {2}'.format(
                    function, minion_tgt, orig
                )
            )

        # Try to match stalled state functions
        orig[minion_tgt] = self._check_state_return(
            orig[minion_tgt], func=function
        )

        return orig[minion_tgt]

    def run_state(self, function, **kwargs):
        '''
        Run the state.single command and return the state return structure
        '''
        ret = self.run_function('state.single', [function], **kwargs)
        return self._check_state_return(ret)

    def _check_state_return(self, ret, func='state.single'):
        if isinstance(ret, dict):
            # This is the supposed return format for state calls
            return ret

        # Late import
        import salt._compat

        if isinstance(ret, list):
            jids = []
            # These are usually errors
            for item in ret[:]:
                if not isinstance(item, salt._compat.string_types):
                    # We don't know how to handle this
                    continue
                match = STATE_FUNCTION_RUNNING_RE.match(item)
                if not match:
                    # We don't know how to handle this
                    continue
                jid = match.group('jid')
                if jid in jids:
                    continue

                jids.append(jid)

                job_data = self.run_function(
                    'saltutil.find_job', [jid]
                )
                job_kill = self.run_function('saltutil.kill_job', [jid])
                msg = (
                    'A running state.single was found causing a state lock. '
                    'Job details: {0!r}  Killing Job Returned: {1!r}'.format(
                        job_data, job_kill
                    )
                )
                ret.append('[TEST SUITE ENFORCED]{0}'
                           '[/TEST SUITE ENFORCED]'.format(msg))
        return ret


class SyndicCase(TestCase, SaltClientTestCaseMixIn):
    '''
    Execute a syndic based execution test
    '''
    _salt_client_config_file_name_ = 'syndic_master'

    def run_function(self, function, arg=()):
        '''
        Run a single salt function and condition the return down to match the
        behavior of the raw function call
        '''
        orig = self.client.cmd('minion', function, arg, timeout=25)
        if 'minion' not in orig:
            self.skipTest(
                'WARNING(SHOULD NOT HAPPEN #1935): Failed to get a reply '
                'from the minion. Command output: {0}'.format(orig)
            )
        return orig['minion']


class SSHCase(ShellTestCase):
    '''
    Execute a command via salt-ssh
    '''
    def _arg_str(self, function, arg):
        return '{0} {1}'.format(function, ' '.join(arg))

    def run_function(self, function, arg=(), timeout=25, **kwargs):
        ret = self.run_ssh(self._arg_str(function, arg))
        try:
            return json.loads(ret)['localhost']
        except Exception:
            return ret


class ClientCase(AdaptedConfigurationTestCaseMixIn, TestCase):
    '''
    A base class containing relevant options for starting the various Salt
    Python API entrypoints
    '''
    def get_opts(self):
        # Late import
        import salt.config

        return salt.config.client_config(self.get_config_file_path('master'))

    def mkdir_p(self, path):
        try:
            os.makedirs(path)
        except OSError as exc:  # Python >2.5
            if exc.errno == errno.EEXIST and os.path.isdir(path):
                pass
            else:
                raise

# ----- Backwards Compatible Imports -------------------------------------------------------------------------------->
from salttesting.mixins import ShellCaseCommonTestsMixIn  # pylint: disable=unused-import
# <---- Backwards Compatible Imports ---------------------------------------------------------------------------------
