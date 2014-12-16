#!/usr/bin/env python3

# apt-local - manage local apt cache and repository
# Copyright © 2014  J. Victor Martins <jvdm@sdf.org>.
#
# This file is distributed under the "Do What the Fuck You Want To"
# Public License, Version 2 (http://www.wtfpl.net/).


import sys
import os
import errno

import apt_pkg
import apt

from apt.cache import Filter, FilteredCache
from apt.progress.text import OpProgress, AcquireProgress


def init(arch, cache_dir):
    # Clear APT and Dir config trees to avoid system configuration:
    del apt_pkg.config['APT']
    del apt_pkg.config['Dir']
    del apt_pkg.config['Dpkg']

    # Initialize apt configuration, use our local apt cache hierarchy:
    apt_opts = []
    for opt, val in { 'APT::Architecture': arch,
                      'APT::Architectures::': '',
                      'Dir': cache_dir,
                      'Dir::State::Status': 'dpkg.status',
                      'Acquire::Languages': 'none',
                    }.items():
        apt_pkg.config.set(opt, val)
        apt_opts.append('%s=%s' % (opt, val))

    apt_pkg.init_config()
    apt_pkg.init_system()


def cmd_update(opts):
    """Create the APT database hierarchy, and resynchronize it."""

    def mkdir_p(path):
        try:
            os.makedirs(path)
        except OSError as exc:
            if exc.errno == errno.EEXIST and os.path.isdir(path):
                pass

    # Create the apt cache directories:
    for diropt in ('Dir::Etc::sourceparts',
                   'Dir::State::lists',
                   'Dir::State::mirrors',
                   'Dir::Cache::archives',):
        mkdir_p(apt_pkg.config.find_dir(diropt))

    # Initialize some required files:
    import shutil

    for fileopt, content in {
            'Dir::State::Status': '',
            'Dir::Etc::sourcelist': opts.sourcelist.read()
    }.items():
        filepath = apt_pkg.config.find_file(fileopt)
        with open(filepath, 'w') as stream:
            if content:
                stream.write(content + '\n')

    cache = apt.Cache(progress=OpProgress(outfile=sys.stderr))
    cache.update(AcquireProgress(outfile=sys.stderr))

    return 0


def iter_pkg_versions(cache, pkg_names):
    for name in pkg_names:
        for sep in ('=', '_'):
            if sep in name:
                name, version = name.split(sep)
                pkg = cache[name]
                pkg.candidate = cache[name].versions[version]
                break
        else:
            pkg = cache[name]

        yield pkg


def cmd_install(opts):
    class InstallFilter(Filter):
        def apply(self, pkg):
            return pkg.marked_install

    cache = apt.Cache(progress=OpProgress(outfile=sys.stderr))

    with cache.actiongroup():
        # Install all essential and required by default:
        for pkg in cache:
            if pkg.essential or pkg.candidate.priority == 'required':
                pkg.mark_install()

        if opts.file:
            for stream in opts.file:
                pkgs = [l.strip() for l in list(stream)]
        else:
            pkgs = opts.packages

        for pkg in iter_pkg_versions(cache, pkgs):
            pkg.mark_install()

    fcache = FilteredCache(cache)
    fcache.set_filter(InstallFilter())

    data = []
    for pkg in fcache:
        print('%s=%s' % (pkg.name, pkg.candidate.version), file=opts.output)

    return 0


def cmd_fetch(opts):
    cache = apt.Cache(progress=OpProgress())
    if opts.file:
        pkgs = [l.strip() for l in list(open(opts.packages[0], 'r'))]
    else:
        pkgs = opts.packages

    for pkg in iter_pkg_versions(cache, pkgs):
        pkg.candidate.fetch_binary(destdir=opts.dest)

    return 0


def cmd_show(opts):
    cache = apt.Cache()
    for pkg in iter_pkg_versions(cache, opts.packages):
        if opts.format:
            print(opts.format % pkg.candidate.record)
        else:
            print(pkg.candidate.record)

    return 0


def parse_args(args):
    import argparse
    import subprocess
    import re

    class CustomFormatter(argparse.ArgumentDefaultsHelpFormatter):
        def _format_action_invocation(self, action):
            if not action.option_strings:
                default = self._get_default_metavar_for_positional(action)
                metavar, = self._metavar_formatter(action, default)(1)
                return metavar
            else:
                parts = []

                if action.nargs == 0:
                    parts.extend(action.option_strings)
                else:
                    default = self._get_default_metavar_for_optional(action)
                    args_string = self._format_args(action, default)
                    for optstr in action.option_strings:
                        parts.append(optstr)
                    parts[-1] += ' %s' % args_string
                return '|'.join(parts)


    parser = argparse.ArgumentParser(
        description='APT cache querying and searching for local databases',
        formatter_class=CustomFormatter,
    )

    # Global options:
    parser.add_argument('-a', '--arch',
        help='use <arch> instead of APT default', choices=('armhf','amd64'),
        metavar='ARCH',
        default=apt_pkg.config.get('APT::Architecture'))
    parser.add_argument('-c', '--cache',
        help='cache directory', metavar='CACHE-DIR',
        default=os.path.join(os.environ['HOME'], '.cache/apt-local'))

    # Subparsers for commands:
    subparser = parser.add_subparsers(
        title='Actions',
        metavar='ACTION',
    )
    # http://bugs.python.org/issue9253#msg186387
    subparser.required=True

    parser_update = subparser.add_parser(
        'update',
        formatter_class=CustomFormatter,
        help='Update the APT database',
    )
    parser_install = subparser.add_parser(
        'install',
        formatter_class=CustomFormatter,
        help='Return a list of packages to install'
    )
    parser_fetch = subparser.add_parser(
        'fetch',
        formatter_class=CustomFormatter,
        help='Fetch binary packages',
    )
    parser_show = subparser.add_parser(
        'show',
        formatter_class=CustomFormatter,
        help='Show package information',
    )

    # Update command:
    parser_update.add_argument(
        'sourcelist',
        type=argparse.FileType('r'),
        help='Sourcelist to use instead of the system default',
    )
    parser_update.set_defaults(func=cmd_update)

    # Install command:
    parser_install.add_argument('packages', nargs='*')
    parser_install.add_argument(
        '-o', '--output',
        type=argparse.FileType(mode='w'),
        default='-')
    parser_install.add_argument(
        '-f', '--file',
        action='append',
        type=argparse.FileType('r'))

    parser_install.set_defaults(func=cmd_install)

    # Fetch command:
    parser_fetch.add_argument('dest')
    parser_fetch.add_argument('packages', nargs='+')
    parser_fetch.add_argument('-f', '--file', action='store_true')
    parser_fetch.set_defaults(func=cmd_fetch)

    # Show command:
    parser_show.add_argument('packages', nargs='+')
    parser_show.add_argument('-f', '--format')
    parser_show.set_defaults(func=cmd_show)

    return parser.parse_args(args)


def main(args):
    opts = parse_args(args)
    init(opts.arch, opts.cache)
    return opts.func(opts)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
