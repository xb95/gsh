#!/usr/bin/env python

import gevent
from gevent.pool import Pool
from gevent.queue import Queue, Empty

from gevent_subprocess import Popen, PIPE


__author__ = "Gary M. Josack <gary@byoteki.com>"
__version__ = 0.1


class Error(Exception):
    pass


class RemotePopen(object):

    QUEUED = 0
    RUNNING = 1
    FAILED = 2
    SUCCESS = 3

    def __init__(self, hostname, command, timeout=None, hooks=None):
        self.hostname = hostname
        self.command = command
        self.timeout = timeout

        if hooks is None: hooks = []
        self.hooks = hooks

        self.status = RemotePopen.QUEUED
        self.rc = None

        self._output_queue = Queue()
        self._proc = None

    @staticmethod
    def stream_fd(fd, queue):
        for line in iter(fd.readline, b""):
            queue.put_nowait((fd, line))

    def consume(self, queue, hostname, names):
        while True:
            try:
                output = queue.get()
            except Empty:
                continue

            # None is explicitly sent to shutdown the consumer
            if output is None:
                return

            fd, line = output
            for hook in self.hooks:
                hook.update_host(hostname, names[fd], line)

    def run(self):
        self.status = RemotePopen.RUNNING
        self._proc = Popen(["ssh", self.hostname] + self.command, stdout=PIPE, stderr=PIPE)

        names = {
            self._proc.stdout: "stdout",
            self._proc.stderr: "stderr",
        }

        out_worker = gevent.spawn(self.stream_fd, self._proc.stdout, self._output_queue)
        err_worker = gevent.spawn(self.stream_fd, self._proc.stderr, self._output_queue)
        consumer = gevent.spawn(self.consume, self._output_queue, self.hostname, names)

        gevent.joinall([out_worker, err_worker])
        self._output_queue.put_nowait(None)
        consumer.join()
        self.rc = self._proc.wait()


class Gsh(object):
    def __init__(self, hosts, command, fork_limit=1, timeout=None, hooks=None):
        self.hosts = set(hosts)
        self.command = command
        self.fork_limit = fork_limit
        self.timeout = timeout

        if hooks is None: hooks = []
        self.hooks = hooks

        self._pool = Pool(max(self.fork_limit, 1))
        self._greenlets = []
        self._remotes = []

    def run_async(self):
        for host in self.hosts:
            remote_command = RemotePopen(host, self.command, hooks=self.hooks)
            self._remotes.append(remote_command)
            self._greenlets.append(self._pool.apply_async(remote_command.run))

    def wait(self, timeout=None):
        rc = 0
        gevent.joinall(self._greenlets, timeout=timeout, raise_error=True)
        for remote in self._remotes:
            if remote.rc:
                return remote.rc
        return rc
