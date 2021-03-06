#!/usr/bin/env python
# -*- coding: utf-8 -*-#
#
# Copyright (C) 2016 University of Zurich. All rights reserved.
#
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not,

__docformat__ = 'reStructuredText'
__author__ = 'Riccardo Murri <riccardo.murri@gmail.com>'

# stdlib imports
from contextlib import contextmanager
import functools
import os
import re
import signal
import sys
import time

# 3rd party imports
import click
import netaddr


def confirm_or_abort(prompt, exitcode=os.EX_TEMPFAIL, msg=None, **extra_args):
    """
    Prompt user for confirmation and exit on negative reply.

    Arguments `prompt` and `extra_args` will be passed unchanged to
    `click.confirm`:ref: (which is used for actual prompting).

    :param str prompt: Prompt string to display.
    :param int exitcode: Program exit code if negative reply given.
    :param str msg: Message to display before exiting.
    """
    if click.confirm(prompt, **extra_args):
        return True
    else:
        # abort
        if msg:
            sys.stderr.write(msg)
            sys.stderr.write('\n')
        sys.exit(exitcode)


def has_nested_keys(mapping, k1, *more):
    """
    Return ``True`` if `mapping[k1][k2]...[kN]` is valid.

    Example::

      >>> D = {
      ...   'a': {
      ...     'x':0,
      ...     'y':{
      ...       'z': 1,
      ...     },
      ...   },
      ...   'b': 3
      ... }
      >>> has_nested_keys(D, 'a', 'x')
      True
      >>> has_nested_keys(D, 'a', 'y', 'z')
      True
      >>> has_nested_keys(D, 'a', 'q')
      False

    When a single key is passed, this is just another way of writing ``k1 in
    mapping``::

      >>> has_nested_keys(D, 'b')
      True
    """
    if k1 in mapping:
        if more:
            return has_nested_keys(mapping[k1], *more)
        else:
            return True
    else:
        return False


class memoize(object):
    """
    Cache a function's return value each time it is called within a TTL.

    If called within the TTL and the same arguments, the cached value is
    returned, If called outside the TTL or a different value, a fresh value is
    returned (and cached for future occurrences).

    .. warning::

      Only works on functions that take *no keyword arguments*.

    Originally taken from: http://jonebird.com/2012/02/07/python-memoize-decorator-with-ttl-argument/
    """
    def __init__(self, ttl):
        self.cache = {}
        self.ttl = ttl

    def __call__(self, f):
        @functools.wraps(f)
        def wrapped_f(*args):
            now = time.time()
            try:
                value, last_update = self.cache[args]
                if self.ttl > 0 and now - last_update > self.ttl:
                    raise AttributeError
                return value
            except (KeyError, AttributeError):
                value = f(*args)
                self.cache[args] = (value, now)
                return value
            except TypeError:
                # uncachable -- for instance, passing a list as an argument.
                # Better to not cache than to blow up entirely.
                return f(*args)
        return wrapped_f


# this is very liberal, in that it will accept malformed address
# strings like `0:::1` or '0::1::2', but we are going to do validation
# with `netaddr.IPAddress` later on so there is little advantage in
# being strict here
_IPV6_FRAG = r'[0-9a-z:]+'

# likewise
_IPV4_FRAG = r'[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'

# should match a network interface name (for which there is no
# standard, so let's just assume it's alphanumeric)
_IFACE_FRAG = r'[a-z][0-9a-z]*'

# XXX: order is important! IPv4 must come before IPv6 otherwise the
# _IPV6_FRAG will match a *part* of an IPv4 adress....
_IP_ADDRESS_RE = [
    # IPv4 literal, optionally with port
    re.compile(
        r'(?P<ip_addr>{0})(?P<port>:\d+)?'
        .format(_IPV4_FRAG), re.I),

    # the kind of IPv6 literals returned by Azure, e.g., `[fe80::dead:beef%eth0]:2222`
    re.compile(
        r'\[(?P<ip_addr>{0})(?P<iface>%{1})?\](?P<port>:\d+)?'
        .format(_IPV6_FRAG, _IFACE_FRAG), re.I),

    # IPv6 literal possibly with interface spec (note this cannot provide any port)
    re.compile(
        r'(?P<ip_addr>{0})(?P<iface>%{1})?'
        .format(_IPV6_FRAG, _IFACE_FRAG), re.I),
]

def parse_ip_address_and_port(addr, default_port=22):
    """
    Return a pair (IP address, port) extracted from string `addr`.

    Different formats are accepted for the address/port string:

    * IPv6 literals in square brackets, with or without an optional
      port specification, as used in URLs::

        >>> parse_ip_address_and_port('[fe80::dead:beef]:1234')
        (IPAddress('fe80::dead:beef'), 1234)

        >>> parse_ip_address_and_port('[fe80::dead:beef]')
        (IPAddress('fe80::dead:beef'), 22)

    * IPv6 literals with a "local interface" specification::

        >>> parse_ip_address_and_port('[fe80::dead:beef%eth0]')
        (IPAddress('fe80::dead:beef'), 22)

        >>> parse_ip_address_and_port('fe80::dead:beef%eth0')
        (IPAddress('fe80::dead:beef'), 22)

    * bare IPv6 addresses::

        >>> parse_ip_address_and_port('fe80::dead:beef')
        (IPAddress('fe80::dead:beef'), 22)

        >>> parse_ip_address_and_port('2001:db8:5ca1:1f0:f816:3eff:fe05:f40f')
        (IPAddress('2001:db8:5ca1:1f0:f816:3eff:fe05:f40f'), 22)

    * IPv4 addresses, with or without an additional port specification::

        >>> parse_ip_address_and_port('192.0.2.123')
        (IPAddress('192.0.2.123'), 22)

        >>> parse_ip_address_and_port('192.0.2.123:999')
        (IPAddress('192.0.2.123'), 999)

    Note that the default port can be changed by passing an additional parameter::

        >>> parse_ip_address_and_port('192.0.2.123', 987)
        (IPAddress('192.0.2.123'), 987)

        >>> parse_ip_address_and_port('fe80::dead:beef', 987)
        (IPAddress('fe80::dead:beef'), 987)

    :raise netaddr.AddrFormatError: Upon parse failure, e.g., syntactically incorrect IP address.
    """
    # we assume one and only one of the regexps will match
    for regexp in _IP_ADDRESS_RE:
        match = regexp.search(addr)
        if not match:
            continue
        # can raise netaddr.AddrFormatError
        ip_addr = netaddr.IPAddress(match.group('ip_addr'))
        try:
            port = match.group('port')
        except IndexError:
            port = None
        if port is not None:
            port = int(port[1:])  # skip leading `:`
        else:
            port = default_port
        return ip_addr, port
    # parse failed
    raise netaddr.AddrFormatError(
        "Could not extract IP address and port from `{1}`"
        .format(addr))
    
    

@contextmanager
def sighandler(signum, handler):
    """
    Context manager to run code with UNIX signal `signum` bound to `handler`.

    The existing handler is saved upon entering the context and restored upon
    exit.

    The `handler` argument may be anything that can be passed to Python's
    `signal.signal <https://docs.python.org/2/library/signal.html#signal.signal>`_
    standard library call.
    """
    prev_handler = signal.getsignal(signum)
    signal.signal(signum, handler)
    yield
    signal.signal(signum, prev_handler)


@contextmanager
def timeout(delay, handler=None):
    """
    Context manager to run code and deliver a SIGALRM signal after `delay` seconds.

    Note that `delay` must be a whole number; otherwise it is converted to an
    integer by Python's `int()` built-in function. For floating-point numbers,
    that means rounding off to the nearest integer from below.

    If the optional argument `handler` is supplied, it must be a callable that
    is invoked if the alarm triggers while the code is still running. If no
    `handler` is provided (default), then a `RuntimeError` with message
    ``Timeout`` is raised.
    """
    delay = int(delay)
    if handler is None:
        def default_handler(signum, frame):
            raise RuntimeError("{:d} seconds timeout expired".format(delay))
        handler = default_handler
    prev_sigalrm_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, handler)
    signal.alarm(delay)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, prev_sigalrm_handler)


## Warnings redirection
#
# This is a modified version of the `logging.captureWarnings()` code from
# the Python 2.7 standard library:
#
# - backport the code to Python 2.6
# - make the logger configurable
#
# The original copyright notice is reproduced below:
#
#   Copyright 2001-2014 by Vinay Sajip. All Rights Reserved.
#
#   Permission to use, copy, modify, and distribute this software and its
#   documentation for any purpose and without fee is hereby granted,
#   provided that the above copyright notice appear in all copies and that
#   both that copyright notice and this permission notice appear in
#   supporting documentation, and that the name of Vinay Sajip
#   not be used in advertising or publicity pertaining to distribution
#   of the software without specific, written prior permission.
#   VINAY SAJIP DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE, INCLUDING
#   ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL
#   VINAY SAJIP BE LIABLE FOR ANY SPECIAL, INDIRECT OR CONSEQUENTIAL DAMAGES OR
#   ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER
#   IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT
#   OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
#

import logging
# ensure that `logging.NullHandler` is defined on Python 2.6 as well;
# see: http://stackoverflow.com/questions/33175763/how-to-use-logging-nullhandler-in-python-2-6
try:
    logging.NullHandler
except AttributeError:
    class _NullHandler(logging.Handler):
        def emit(self, record):
            pass
    logging.NullHandler = _NullHandler

import warnings

_warnings_showwarning = None


class _WarningsLogger(object):
    """
    Redirect warning messages to a chosen logger.

    This is a callable object that implements a compatible interface
    to `warnings.showwarning` (which it is supposed to replace).
    """

    def __init__(self, logger_name, format_warning=warnings.formatwarning):
        self._logger = logging.getLogger(logger_name)
        if not self._logger.handlers:
            self._logger.addHandler(logging.NullHandler())
        self._format_warning = format_warning

    def __call__(self, message, category, filename, lineno, file=None, line=None):
        """
        Implementation of showwarnings which redirects to logging, which will first
        check to see if the file parameter is None. If a file is specified, it will
        delegate to the original warnings implementation of showwarning. Otherwise,
        it will call warnings.formatwarning and will log the resulting string to a
        warnings logger named "py.warnings" with level logging.WARNING.
        """
        if file is not None:
            assert _warnings_showwarning is not None
            _warnings_showwarning(message, category, filename, lineno, file, line)
        else:
            self._logger.warning(
                self._format_warning(message, category, filename, lineno))


def format_warning_oneline(message, category, filename, lineno,
                           file=None, line=None):
    """
    Format a warning for logging.

    The returned value should be a single-line string, for better
    logging style (although this is not enforced by the code).

    This methods' arguments have the same meaning of the
    like-named arguments from `warnings.formatwarning`.
    """
    # `warnings.formatwarning` produces multi-line output that does
    # not look good in a log file, so let us replace it with something
    # simpler...
    return ('{category}: {message}'
            .format(message=message, category=category.__name__))


def redirect_warnings(capture=True, logger='py.warnings'):
    """
    If capture is true, redirect all warnings to the logging package.
    If capture is False, ensure that warnings are not redirected to logging
    but to their original destinations.
    """
    global _warnings_showwarning
    if capture:
        assert _warnings_showwarning is None
        _warnings_showwarning = warnings.showwarning
        # `warnings.showwarning` must be a function, a generic
        # callable object is not accepted ...
        warnings.showwarning = _WarningsLogger(logger, format_warning_oneline).__call__
    else:
        assert _warnings_showwarning is not None
        warnings.showwarning = _warnings_showwarning
        _warnings_showwarning = None
