#!/usr/bin/env python
"""Test that autoflake runs without crashing on various Python files."""

import os
import shlex
import sys
import subprocess


ROOT_PATH = os.path.abspath(os.path.dirname(__file__))
AUTOFLAKE_BIN = os.path.join(ROOT_PATH, 'autoflake')

import autoflake

if sys.stdout.isatty():
    YELLOW = '\x1b[33m'
    END = '\x1b[0m'
else:
    YELLOW = ''
    END = ''

try:
    unicode
except NameError:
    unicode = str


def colored(text, color):
    """Return color coded text."""
    return color + text + END


def pyflakes_count(filename):
    """Return pyflakes error count."""
    with autoflake.open_with_encoding(
            filename,
            encoding=autoflake.detect_encoding(filename)) as f:
        return len(list(autoflake.check(f.read())))


def readlines(filename):
    """Return contents of file as a list of lines."""
    with autoflake.open_with_encoding(
            filename,
            encoding=autoflake.detect_encoding(filename)) as f:
        return f.readlines()


def diff(before, after):
    """Return diff of two files."""
    import difflib
    return unicode().join(difflib.unified_diff(
        readlines(before),
        readlines(after),
        before,
        after))


def run(filename, command, verbose=False, options=None):
    """Run autoflake on file at filename.

    Return True on success.

    """
    if not options:
        options = []

    import test_autoflake
    with test_autoflake.temporary_directory() as temp_directory:
        temp_filename = os.path.join(temp_directory,
                                     os.path.basename(filename))
        import shutil
        shutil.copyfile(filename, temp_filename)

        if 0 != subprocess.call(shlex.split(command) +
                                ['--in-place', temp_filename] +
                                options):
            sys.stderr.write('autoflake crashed on ' + filename + '\n')
            return False

        try:
            file_diff = diff(filename, temp_filename)
            if verbose:
                sys.stderr.write(file_diff)

            if check_syntax(filename):
                try:
                    check_syntax(temp_filename, raise_error=True)
                except (SyntaxError, TypeError,
                        UnicodeDecodeError) as exception:
                    sys.stderr.write('autoflake broke ' + filename + '\n' +
                                     str(exception) + '\n')
                    return False

            before_count = pyflakes_count(filename)
            after_count = pyflakes_count(temp_filename)

            if verbose:
                print('(before, after):', (before_count, after_count))

            if file_diff and after_count > before_count:
                sys.stderr.write('autoflake made ' + filename + ' worse\n')
                return False
        except IOError as exception:
            sys.stderr.write(str(exception) + '\n')

    return True


def check_syntax(filename, raise_error=False):
    """Return True if syntax is okay."""
    with autoflake.open_with_encoding(
            filename,
            encoding=autoflake.detect_encoding(filename)) as input_file:
        try:
            compile(input_file.read(), '<string>', 'exec')
            return True
        except (SyntaxError, TypeError, UnicodeDecodeError):
            if raise_error:
                raise
            else:
                return False


def process_args():
    """Return processed arguments (options and positional arguments)."""
    import argparse
    parser = argparse.ArgumentParser()

    parser.add_argument('--command', default=AUTOFLAKE_BIN,
                        help='autoflake command (default: %(default)s)')

    parser.add_argument(
        '--timeout',
        default=-1.,
        type=float,
        help='stop testing additional files after this amount of time '
             '(default: %(default)s)')

    parser.add_argument('--imports',
                        help='pass to the autoflake "--imports" option')

    parser.add_argument('--remove-all', action='store_true',
                        help='pass to the autoflake "--remove-all" option')

    parser.add_argument('-v', '--verbose', action='store_true',
                        help='print verbose messages')

    parser.add_argument('files', nargs='*', help='files to format')

    return parser.parse_args()


class TimeoutException(Exception):

    """Timeout exception."""


def timeout(_, __):
    raise TimeoutException()


def check(args):
    """Run recursively run autoflake on directory of files.

    Return False if the fix results in broken syntax.

    """
    if args.files:
        dir_paths = args.files
    else:
        dir_paths = [path for path in sys.path
                     if os.path.isdir(path)]

    options = []
    if args.imports:
        options.append('--imports=' + args.imports)

    if args.remove_all:
        options.append('--remove-all')

    filenames = dir_paths
    completed_filenames = set()

    try:
        import signal
        if args.timeout > 0:
            signal.signal(signal.SIGALRM, timeout)
            signal.alarm(int(args.timeout))

        while filenames:
            name = os.path.realpath(filenames.pop(0))
            if not os.path.exists(name):
                # Invalid symlink.
                continue

            if name in completed_filenames:
                sys.stderr.write(
                    colored('--->  Skipping previously tested ' + name + '\n',
                            YELLOW))
                continue
            else:
                completed_filenames.update(name)

            try:
                is_directory = os.path.isdir(name)
            except UnicodeEncodeError:
                continue

            if is_directory:
                for root, directories, children in os.walk(name):
                    filenames += [os.path.join(root, f) for f in children
                                  if f.endswith('.py') and
                                  not f.startswith('.')]

                    directories[:] = [d for d in directories
                                      if not d.startswith('.')]
            else:
                verbose_message = '--->  Testing with '
                try:
                    verbose_message += name
                except UnicodeEncodeError:
                    verbose_message += '...'
                sys.stderr.write(colored(verbose_message + '\n', YELLOW))

                if not run(os.path.join(name),
                           command=args.command,
                           verbose=args.verbose,
                           options=options):
                    return False
    except TimeoutException:
        sys.stderr.write('Timed out\n')
    finally:
        if args.timeout > 0:
            signal.alarm(0)

    return True


def main():
    """Run main."""
    return 0 if check(process_args()) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
