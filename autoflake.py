#!/usr/bin/env python

# Copyright (C) 2012-2017 Steven Myint
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""Removes unused imports and unused variables as reported by pyflakes."""

from __future__ import print_function
from __future__ import unicode_literals

import ast
import difflib
import collections
import distutils.sysconfig
import io
import os
import re
import signal
import sys
import tokenize

import pyflakes.api
import pyflakes.messages
import pyflakes.reporter


__version__ = '0.7'


ATOMS = frozenset([tokenize.NAME, tokenize.NUMBER, tokenize.STRING])

EXCEPT_REGEX = re.compile(r'^\s*except [\s,()\w]+ as \w+:$')
FROM_IMPORT_REGEX = re.compile(r'^\s*from\s')


try:
    unicode
except NameError:
    unicode = str


try:
    RecursionError
except NameError:
    # Python before 3.5.
    RecursionError = RuntimeError


def standard_paths():
    """Yield paths to standard modules."""
    path = distutils.sysconfig.get_python_lib(standard_lib=True)

    for name in os.listdir(path):
        yield name

    try:
        for name in os.listdir(os.path.join(path, 'lib-dynload')):
            yield name
    except OSError:  # pragma: no cover
        pass


def standard_package_names():
    """Yield standard module names."""
    for name in standard_paths():
        if name.startswith('_') or '-' in name:
            continue

        if '.' in name and name.rsplit('.')[-1] not in ['so', 'py', 'pyc']:
            continue

        yield name.split('.')[0]


IMPORTS_WITH_SIDE_EFFECTS = {'antigravity', 'rlcompleter', 'this'}

# In case they are built into CPython.
BINARY_IMPORTS = {'datetime', 'grp', 'io', 'json', 'math', 'multiprocessing',
                  'parser', 'pwd', 'string', 'operator', 'os', 'sys', 'time'}

SAFE_IMPORTS = (frozenset(standard_package_names()) -
                IMPORTS_WITH_SIDE_EFFECTS |
                BINARY_IMPORTS)


def unused_import_line_numbers(messages):
    """Yield line numbers of unused imports."""
    for message in messages:
        if isinstance(message, pyflakes.messages.UnusedImport):
            yield message.lineno


def unused_import_module_name(messages):
    """Yield line number and module name of unused imports."""
    pattern = r'\'(.+?)\''
    for message in messages:
        if isinstance(message, pyflakes.messages.UnusedImport):
            module_name = re.search(pattern, str(message))
            module_name = module_name.group()[1:-1]
            if module_name:
                yield (message.lineno, module_name)


def unused_variable_line_numbers(messages):
    """Yield line numbers of unused variables."""
    for message in messages:
        if isinstance(message, pyflakes.messages.UnusedVariable):
            yield message.lineno


def check(source):
    """Return messages from pyflakes."""
    if sys.version_info[0] == 2 and isinstance(source, unicode):
        # Convert back to original byte string encoding, otherwise pyflakes
        # call to compile() will complain. See PEP 263. This only affects
        # Python 2.
        try:
            source = source.encode('utf-8')
        except UnicodeError:  # pragma: no cover
            return []

    reporter = ListReporter()
    try:
        pyflakes.api.check(source, filename='<string>', reporter=reporter)
    except (AttributeError, RecursionError, UnicodeDecodeError):
        pass
    return reporter.messages


class StubFile(object):
    """Stub out file for pyflakes."""

    def write(self, *_):
        """Stub out."""


class ListReporter(pyflakes.reporter.Reporter):
    """Accumulate messages in messages list."""

    def __init__(self):
        """Initialize.

        Ignore errors from Reporter.

        """
        ignore = StubFile()
        pyflakes.reporter.Reporter.__init__(self, ignore, ignore)
        self.messages = []

    def flake(self, message):
        """Accumulate messages."""
        self.messages.append(message)


def extract_package_name(line):
    """Return package name in import statement."""
    assert '\\' not in line
    assert '(' not in line
    assert ')' not in line
    assert ';' not in line

    if line.lstrip().startswith(('import', 'from')):
        word = line.split()[1]
    else:
        # Ignore doctests.
        return None

    package = word.split('.')[0]
    assert ' ' not in package

    return package


def multiline_import(line, previous_line=''):
    """Return True if import is spans multiples lines."""
    for symbol in '()':
        if symbol in line:
            return True

    # Ignore doctests.
    if line.lstrip().startswith('>'):
        return True

    return multiline_statement(line, previous_line)


def multiline_statement(line, previous_line=''):
    """Return True if this is part of a multiline statement."""
    for symbol in '\\:;':
        if symbol in line:
            return True

    sio = io.StringIO(line)
    try:
        list(tokenize.generate_tokens(sio.readline))
        return previous_line.rstrip().endswith('\\')
    except (SyntaxError, tokenize.TokenError):
        return True


def filter_from_import(line, unused_module):
    """Parse and filter ``from something import a, b, c``.

    Return line without unused import modules, or `pass` if all of the
    module in import is unused.

    """
    (indentation, imports) = re.split(pattern=r'\bimport\b',
                                      string=line, maxsplit=1)
    base_module = re.search(pattern=r'\bfrom\s+([^ ]+)',
                            string=indentation).group(1)

    # Create an imported module list with base module name
    # ex ``from a import b, c as d`` -> ``['a.b', 'a.c as d']``
    imports = re.split(pattern=r',', string=imports.strip())
    imports = [base_module + '.' + x.strip() for x in imports]

    # We compare full module name (``a.module`` not `module`) to
    # guarantee the exact same module as detected from pyflakes.
    filtered_imports = [x.replace(base_module + '.', '')
                        for x in imports if x not in unused_module]

    # All of the import in this statement is unused
    if not filtered_imports:
        return get_indentation(line) + 'pass' + get_line_ending(line)

    indentation += 'import '

    return (
        indentation +
        ', '.join(sorted(filtered_imports)) +
        get_line_ending(line))


def break_up_import(line):
    """Return line with imports on separate lines."""
    assert '\\' not in line
    assert '(' not in line
    assert ')' not in line
    assert ';' not in line
    assert '#' not in line
    assert not re.match(FROM_IMPORT_REGEX, line)

    newline = get_line_ending(line)
    if not newline:
        return line

    (indentation, imports) = re.split(pattern=r'\bimport\b',
                                      string=line, maxsplit=1)

    indentation += 'import '
    assert newline

    return ''.join([indentation + i.strip() + newline
                    for i in sorted(imports.split(','))])


def filter_code(source, additional_imports=None,
                remove_all_unused_imports=False,
                remove_unused_variables=False):
    """Yield code with unused imports removed."""
    imports = SAFE_IMPORTS
    if additional_imports:
        imports |= frozenset(additional_imports)
    del additional_imports

    messages = check(source)

    marked_import_line_numbers = frozenset(
        unused_import_line_numbers(messages))
    marked_unused_module = collections.defaultdict(lambda: [])
    for line_number, module_name in unused_import_module_name(messages):
        marked_unused_module[line_number].append(module_name)

    if remove_unused_variables:
        marked_variable_line_numbers = frozenset(
            unused_variable_line_numbers(messages))
    else:
        marked_variable_line_numbers = frozenset()

    sio = io.StringIO(source)
    previous_line = ''
    for line_number, line in enumerate(sio.readlines(), start=1):
        if '#' in line:
            yield line
        elif line_number in marked_import_line_numbers:
            yield filter_unused_import(
                line,
                unused_module=marked_unused_module[line_number],
                remove_all_unused_imports=remove_all_unused_imports,
                imports=imports,
                previous_line=previous_line)
        elif line_number in marked_variable_line_numbers:
            yield filter_unused_variable(line)
        else:
            yield line

        previous_line = line


def filter_unused_import(line, unused_module, remove_all_unused_imports,
                         imports, previous_line=''):
    """Return line if used, otherwise return None."""
    if multiline_import(line, previous_line):
        return line

    is_from_import = re.match(FROM_IMPORT_REGEX, line)

    if ',' in line and not is_from_import:
        return break_up_import(line)

    package = extract_package_name(line)
    if not remove_all_unused_imports and package not in imports:
        return line

    if ',' in line:
        assert is_from_import
        return filter_from_import(line, unused_module)
    else:
        # We need to replace import with "pass" in case the import is the
        # only line inside a block. For example,
        # "if True:\n    import os". In such cases, if the import is
        # removed, the block will be left hanging with no body.
        return (get_indentation(line) +
                'pass' +
                get_line_ending(line))


def filter_unused_variable(line, previous_line=''):
    """Return line if used, otherwise return None."""
    if re.match(EXCEPT_REGEX, line):
        return re.sub(r' as \w+:$', ':', line, count=1)
    elif multiline_statement(line, previous_line):
        return line
    elif line.count('=') == 1:
        split_line = line.split('=')
        assert len(split_line) == 2
        value = split_line[1].lstrip()
        if ',' in split_line[0]:
            return line

        if is_literal_or_name(value):
            # Rather than removing the line, replace with it "pass" to avoid
            # a possible hanging block with no body.
            value = 'pass' + get_line_ending(line)

        return get_indentation(line) + value
    else:
        return line


def is_literal_or_name(value):
    """Return True if value is a literal or a name."""
    try:
        ast.literal_eval(value)
        return True
    except (SyntaxError, ValueError):
        pass

    if value.strip() in ['dict()', 'list()', 'set()']:
        return True

    # Support removal of variables on the right side. But make sure
    # there are no dots, which could mean an access of a property.
    return re.match(r'^\w+\s*$', value)


def useless_pass_line_numbers(source):
    """Yield line numbers of unneeded "pass" statements."""
    sio = io.StringIO(source)
    previous_token_type = None
    last_pass_row = None
    last_pass_indentation = None
    previous_line = ''
    for token in tokenize.generate_tokens(sio.readline):
        token_type = token[0]
        start_row = token[2][0]
        line = token[4]

        is_pass = (token_type == tokenize.NAME and line.strip() == 'pass')

        # Leading "pass".
        if (start_row - 1 == last_pass_row and
                get_indentation(line) == last_pass_indentation and
                token_type in ATOMS and
                not is_pass):
            yield start_row - 1

        if is_pass:
            last_pass_row = start_row
            last_pass_indentation = get_indentation(line)

        # Trailing "pass".
        if (is_pass and
                previous_token_type != tokenize.INDENT and
                not previous_line.rstrip().endswith('\\')):
            yield start_row

        previous_token_type = token_type
        previous_line = line


def filter_useless_pass(source):
    """Yield code with useless "pass" lines removed."""
    try:
        marked_lines = frozenset(useless_pass_line_numbers(source))
    except (SyntaxError, tokenize.TokenError):
        marked_lines = frozenset()

    sio = io.StringIO(source)
    for line_number, line in enumerate(sio.readlines(), start=1):
        if line_number not in marked_lines:
            yield line


def get_indentation(line):
    """Return leading whitespace."""
    if line.strip():
        non_whitespace_index = len(line) - len(line.lstrip())
        return line[:non_whitespace_index]
    else:
        return ''


def get_line_ending(line):
    """Return line ending."""
    non_whitespace_index = len(line.rstrip()) - len(line)
    if not non_whitespace_index:
        return ''
    else:
        return line[non_whitespace_index:]


def fix_code(source, additional_imports=None, remove_all_unused_imports=False,
             remove_unused_variables=False):
    """Return code with all filtering run on it."""
    if not source:
        return source

    # pyflakes does not handle "nonlocal" correctly.
    if 'nonlocal' in source:
        remove_unused_variables = False

    filtered_source = None
    while True:
        filtered_source = ''.join(
            filter_useless_pass(''.join(
                filter_code(
                    source,
                    additional_imports=additional_imports,
                    remove_all_unused_imports=remove_all_unused_imports,
                    remove_unused_variables=remove_unused_variables))))

        if filtered_source == source:
            break
        source = filtered_source

    return filtered_source


def fix_file(filename, args, standard_out):
    """Run fix_code() on a file."""
    encoding = detect_encoding(filename)
    with open_with_encoding(filename, encoding=encoding) as input_file:
        source = input_file.read()

    original_source = source

    filtered_source = fix_code(
        source,
        additional_imports=args.imports.split(',') if args.imports else None,
        remove_all_unused_imports=args.remove_all_unused_imports,
        remove_unused_variables=args.remove_unused_variables)

    if original_source != filtered_source:
        if args.in_place:
            with open_with_encoding(filename, mode='w',
                                    encoding=encoding) as output_file:
                output_file.write(filtered_source)
        else:
            diff = get_diff_text(
                io.StringIO(original_source).readlines(),
                io.StringIO(filtered_source).readlines(),
                filename)
            standard_out.write(''.join(diff))


def open_with_encoding(filename, encoding, mode='r'):
    """Return opened file with a specific encoding."""
    return io.open(filename, mode=mode, encoding=encoding,
                   newline='')  # Preserve line endings


def detect_encoding(filename):
    """Return file encoding."""
    try:
        with open(filename, 'rb') as input_file:
            encoding = _detect_encoding(input_file.readline)

            # Check for correctness of encoding.
            with open_with_encoding(filename, encoding) as input_file:
                input_file.read()

        return encoding
    except (LookupError, SyntaxError, UnicodeDecodeError):
        return 'latin-1'


def _detect_encoding(readline):
    """Return file encoding."""
    try:
        from lib2to3.pgen2 import tokenize as lib2to3_tokenize
        encoding = lib2to3_tokenize.detect_encoding(readline)[0]
        return encoding
    except (LookupError, SyntaxError, UnicodeDecodeError):
        return 'latin-1'


def get_diff_text(old, new, filename):
    """Return text of unified diff between old and new."""
    newline = '\n'
    diff = difflib.unified_diff(
        old, new,
        'original/' + filename,
        'fixed/' + filename,
        lineterm=newline)

    text = ''
    for line in diff:
        text += line

        # Work around missing newline (http://bugs.python.org/issue2142).
        if not line.endswith(newline):
            text += newline + r'\ No newline at end of file' + newline

    return text


def _main(argv, standard_out, standard_error):
    """Return exit status.

    0 means no error.

    """
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, prog='autoflake')
    parser.add_argument('-i', '--in-place', action='store_true',
                        help='make changes to files instead of printing diffs')
    parser.add_argument('-r', '--recursive', action='store_true',
                        help='drill down directories recursively')
    parser.add_argument('--imports',
                        help='by default, only unused standard library '
                             'imports are removed; specify a comma-separated '
                             'list of additional modules/packages')
    parser.add_argument('--remove-all-unused-imports', action='store_true',
                        help='remove all unused imports (not just those from '
                             'the standard library')
    parser.add_argument('--remove-unused-variables', action='store_true',
                        help='remove unused variables')
    parser.add_argument('--version', action='version',
                        version='%(prog)s ' + __version__)
    parser.add_argument('files', nargs='+', help='files to format')

    args = parser.parse_args(argv[1:])

    if args.remove_all_unused_imports and args.imports:
        print('Using both --remove-all and --imports is redundant',
              file=standard_error)
        return 1

    filenames = list(set(args.files))
    while filenames:
        name = filenames.pop(0)
        if args.recursive and os.path.isdir(name):
            for root, directories, children in os.walk(unicode(name)):
                filenames += [os.path.join(root, f) for f in children
                              if f.endswith('.py') and
                              not f.startswith('.')]
                directories[:] = [d for d in directories
                                  if not d.startswith('.')]
        else:
            try:
                fix_file(name, args=args, standard_out=standard_out)
            except IOError as exception:
                print(unicode(exception), file=standard_error)


def main():
    """Command-line entry point."""
    try:
        # Exit on broken pipe.
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except AttributeError:  # pragma: no cover
        # SIGPIPE is not available on Windows.
        pass

    try:
        return _main(sys.argv,
                     standard_out=sys.stdout,
                     standard_error=sys.stderr)
    except KeyboardInterrupt:  # pragma: no cover
        return 2  # pragma: no cover


if __name__ == '__main__':
    sys.exit(main())
