# -*- coding:utf-8 -*-
# Copyright 2016 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Functions that implement the actual checks."""

from __future__ import print_function

import json
import os
import platform
import re
import sys

_path = os.path.realpath(__file__ + '/../..')
if sys.path[0] != _path:
    sys.path.insert(0, _path)
del _path

import rh.results
import rh.git
import rh.utils


class HookOptions(object):
    """Holder class for hook options."""

    def __init__(self, args, tool_paths):
        """Initialize.

        Args:
          args: The override commandline arguments for the hook.
          tool_paths: A dictionary with tool names to paths.
        """
        self._args = args
        self._tool_paths = tool_paths

    def args(self, default_args=(), diff=()):
        """Gets the hook arguments, after performing place holder expansion.

        Args:
          default_args: The list to return if |self._args| is empty.
          diff: The list of files that changed in the current commit.

        Returns:
          A list with arguments.
        """
        args = self._args
        if not args:
            args = default_args

        ret = []
        for arg in args:
            if arg == '${PREUPLOAD_FILES}':
                ret.extend(x.file for x in diff if x.status != 'D')
            elif arg == '${PREUPLOAD_COMMIT_MESSAGE}':
                ret.append(os.environ['PREUPLOAD_COMMIT_MESSAGE'])
            elif arg == '${PREUPLOAD_COMMIT}':
                ret.append(os.environ['PREUPLOAD_COMMIT'])
            else:
                ret.append(arg)

        return ret

    def tool_path(self, tool_name):
        """Gets the path in which the |tool_name| executable can be found.

        This function performs expansion for some place holders.  If the tool
        does not exist in the overriden |self._tool_paths| dictionary, the tool
        name will be returned and will be run from the user's $PATH.

        Args:
          tool_name: The name of the executable.

        Returns:
          The path of the tool with all optional place holders expanded.
        """
        assert tool_name in TOOL_PATHS
        if tool_name not in self._tool_paths:
            return tool_name

        components = []
        tool_path = os.path.normpath(self._tool_paths[tool_name])
        for component in tool_path.split(os.sep):
            if component == '${REPO_ROOT}':
                components.append(rh.git.find_repo_root())
            elif component == '${BUILD_OS}':
                components.append(_get_build_os_name())
            else:
                components.append(component)

        return os.sep.join(components)


def _run_command(cmd, **kwargs):
    """Helper command for checks that tend to gather output."""
    kwargs.setdefault('redirect_stderr', True)
    kwargs.setdefault('combine_stdout_stderr', True)
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('error_code_ok', True)
    return rh.utils.run_command(cmd, **kwargs)


def _match_regex_list(subject, expressions):
    """Try to match a list of regular expressions to a string.

    Args:
      subject: The string to match regexes on.
      expressions: An iterable of regular expressions to check for matches with.

    Returns:
      Whether the passed in subject matches any of the passed in regexes.
    """
    for expr in expressions:
        if re.search(expr, subject):
            return True
    return False


def _filter_diff(diff, include_list, exclude_list=()):
    """Filter out files based on the conditions passed in.

    Args:
      diff: list of diff objects to filter.
      include_list: list of regex that when matched with a file path will cause
          it to be added to the output list unless the file is also matched with
          a regex in the exclude_list.
      exclude_list: list of regex that when matched with a file will prevent it
          from being added to the output list, even if it is also matched with a
          regex in the include_list.

    Returns:
      A list of filepaths that contain files matched in the include_list and not
      in the exclude_list.
    """
    filtered = []
    for d in diff:
        if (d.status != 'D' and
                _match_regex_list(d.file, include_list) and
                not _match_regex_list(d.file, exclude_list)):
            # We've got a match!
            filtered.append(d)
    return filtered


def _get_build_os_name():
    """Gets the build OS name.

    Returns:
      A string in a format usable to get prebuilt tool paths.
    """
    system = platform.system()
    if 'Darwin' in system or 'Macintosh' in system:
        return 'darwin-x86'
    else:
        # TODO: Add more values if needed.
        return 'linux-x86'


def _check_cmd(project, commit, cmd, **kwargs):
    """Runs |cmd| and returns its result as a HookCommandResult."""
    return [rh.results.HookCommandResult(project, commit,
                                         _run_command(cmd, **kwargs))]


# Where helper programs exist.
TOOLS_DIR = os.path.realpath(__file__ + '/../../tools')

def get_helper_path(tool):
    """Return the full path to the helper |tool|."""
    return os.path.join(TOOLS_DIR, tool)


def check_custom(project, commit, _desc, diff, options=None, **kwargs):
    """Run a custom hook."""
    return _check_cmd(project, commit, options.args((), diff), **kwargs)


def check_checkpatch(project, commit, _desc, diff, options=None):
    """Run |diff| through the kernel's checkpatch.pl tool."""
    tool = get_helper_path('checkpatch.pl')
    cmd = ([tool, '-', '--root', project.dir] +
           options.args(('--ignore=GERRIT_CHANGE_ID',), diff))
    return _check_cmd(project, commit, cmd, input=rh.git.get_patch(commit))


def check_clang_format(project, commit, _desc, diff, options=None):
    """Run git clang-format on the commit."""
    tool = get_helper_path('clang-format.py')
    clang_format = options.tool_path('clang-format')
    git_clang_format = options.tool_path('git-clang-format')
    cmd = ([tool, '--clang-format', clang_format, '--git-clang-format',
            git_clang_format] +
           options.args(('--style', 'file', '--commit', commit), diff))
    return _check_cmd(project, commit, cmd)


def check_commit_msg_bug_field(project, commit, desc, _diff, options=None):
    """Check the commit message for a 'Bug:' line."""
    field = 'Bug'
    regex = r'^%s: (None|[0-9]+(, [0-9]+)*)$' % (field,)
    check_re = re.compile(regex)

    if options.args():
        raise ValueError('commit msg %s check takes no options' % (field,))

    found = []
    for line in desc.splitlines():
        if check_re.match(line):
            found.append(line)

    if not found:
        error = ('Commit message is missing a "%s:" line.  It must match:\n'
                 '%s') % (field, regex)
    else:
        return

    return [rh.results.HookResult('commit msg: "%s:" check' % (field,),
                                  project, commit, error=error)]


def check_commit_msg_changeid_field(project, commit, desc, _diff, options=None):
    """Check the commit message for a 'Change-Id:' line."""
    field = 'Change-Id'
    regex = r'^%s: I[a-f0-9]+$' % (field,)
    check_re = re.compile(regex)

    if options.args():
        raise ValueError('commit msg %s check takes no options' % (field,))

    found = []
    for line in desc.splitlines():
        if check_re.match(line):
            found.append(line)

    if len(found) == 0:
        error = ('Commit message is missing a "%s:" line.  It must match:\n'
                 '%s') % (field, regex)
    elif len(found) > 1:
        error = ('Commit message has too many "%s:" lines.  There can be only '
                 'one.') % (field,)
    else:
        return

    return [rh.results.HookResult('commit msg: "%s:" check' % (field,),
                                  project, commit, error=error)]


def check_commit_msg_test_field(project, commit, desc, _diff, options=None):
    """Check the commit message for a 'Test:' line."""
    field = 'Test'
    regex = r'^%s: .*$' % (field,)
    check_re = re.compile(regex)

    if options.args():
        raise ValueError('commit msg %s check takes no options' % (field,))

    found = []
    for line in desc.splitlines():
        if check_re.match(line):
            found.append(line)

    if not found:
        error = ('Commit message is missing a "%s:" line.  It must match:\n'
                 '%s') % (field, regex)
    else:
        return

    return [rh.results.HookResult('commit msg: "%s:" check' % (field,),
                                  project, commit, error=error)]


def check_cpplint(project, commit, _desc, diff, options=None):
    """Run cpplint."""
    # This list matches what cpplint expects.  We could run on more (like .cxx),
    # but cpplint would just ignore them.
    filtered = _filter_diff(diff, [r'\.(cc|h|cpp|cu|cuh)$'])
    if not filtered:
        return

    cmd = ['cpplint.py'] + options.args(('${PREUPLOAD_FILES}',), filtered)
    return _check_cmd(project, commit, cmd)


def check_gofmt(project, commit, _desc, diff, options=None):
    """Checks that Go files are formatted with gofmt."""
    filtered = _filter_diff(diff, [r'\.go$'])
    if not filtered:
        return

    cmd = ['gofmt', '-l'] + options.args((), filtered)
    ret = []
    for d in filtered:
        data = rh.git.get_file_content(commit, d.file)
        result = _run_command(cmd, input=data)
        if result.output:
            ret.append(rh.results.HookResult(
                'gofmt', project, commit, error=result.output,
                files=(d.file,)))
    return ret


def check_json(project, commit, _desc, diff, options=None):
    """Verify json files are valid."""
    if options.args():
        raise ValueError('json check takes no options')

    filtered = _filter_diff(diff, [r'\.json$'])
    if not filtered:
        return

    ret = []
    for d in filtered:
        data = rh.git.get_file_content(commit, d.file)
        try:
            json.loads(data)
        except ValueError as e:
            ret.append(rh.results.HookResult(
                'json', project, commit, error=str(e),
                files=(d.file,)))
    return ret


def check_pylint(project, commit, _desc, diff, options=None):
    """Run pylint."""
    filtered = _filter_diff(diff, [r'\.py$'])
    if not filtered:
        return

    pylint = get_helper_path('pylint.py')
    cmd = [pylint] + options.args(('${PREUPLOAD_FILES}',), filtered)
    return _check_cmd(project, commit, cmd)


def check_xmllint(project, commit, _desc, diff, options=None):
    """Run xmllint."""
    # XXX: Should we drop most of these and probe for <?xml> tags?
    extensions = frozenset((
        'dbus-xml',  # Generated DBUS interface.
        'dia',       # File format for Dia.
        'dtd',       # Document Type Definition.
        'fml',       # Fuzzy markup language.
        'form',      # Forms created by IntelliJ GUI Designer.
        'fxml',      # JavaFX user interfaces.
        'glade',     # Glade user interface design.
        'grd',       # GRIT translation files.
        'iml',       # Android build modules?
        'kml',       # Keyhole Markup Language.
        'mxml',      # Macromedia user interface markup language.
        'nib',       # OS X Cocoa Interface Builder.
        'plist',     # Property list (for OS X).
        'pom',       # Project Object Model (for Apache Maven).
        'rng',       # RELAX NG schemas.
        'sgml',      # Standard Generalized Markup Language.
        'svg',       # Scalable Vector Graphics.
        'uml',       # Unified Modeling Language.
        'vcproj',    # Microsoft Visual Studio project.
        'vcxproj',   # Microsoft Visual Studio project.
        'wxs',       # WiX Transform File.
        'xhtml',     # XML HTML.
        'xib',       # OS X Cocoa Interface Builder.
        'xlb',       # Android locale bundle.
        'xml',       # Extensible Markup Language.
        'xsd',       # XML Schema Definition.
        'xsl',       # Extensible Stylesheet Language.
    ))

    filtered = _filter_diff(diff, [r'\.(%s)$' % '|'.join(extensions)])
    if not filtered:
        return

    # TODO: Figure out how to integrate schema validation.
    # XXX: Should we use python's XML libs instead?
    cmd = ['xmllint'] + options.args(('${PREUPLOAD_FILES}',), filtered)

    return _check_cmd(project, commit, cmd)


# Hooks that projects can opt into.
# Note: Make sure to keep the top level README.md up to date when adding more!
BUILTIN_HOOKS = {
    'checkpatch': check_checkpatch,
    'clang_format': check_clang_format,
    'commit_msg_bug_field': check_commit_msg_bug_field,
    'commit_msg_changeid_field': check_commit_msg_changeid_field,
    'commit_msg_test_field': check_commit_msg_test_field,
    'cpplint': check_cpplint,
    'gofmt': check_gofmt,
    'jsonlint': check_json,
    'pylint': check_pylint,
    'xmllint': check_xmllint,
}

# Additional tools that the hooks can call with their default values.
# Note: Make sure to keep the top level README.md up to date when adding more!
TOOL_PATHS = {
    'clang-format': 'clang-format',
    'git-clang-format': 'git-clang-format',
}
