import os
import json
import tempfile
import time

import mock
from zmq.eventloop import ioloop

from circus.stats.collector import SocketStatsCollector
from circus.tests.support import TestCircus
from circus.stats.streamer import StatsStreamer
from circus import util
from circus import client


class _StatsStreamer(StatsStreamer):

    msgs = []

    def handle_recv(self, data):
        self.msgs.append(data)
        super(_StatsStreamer, self).handle_recv(data)


class FakeStreamer(StatsStreamer):
    def __init__(self, *args, **kwargs):
        self._initialize()


class TestStatsStreamer(TestCircus):

    def setUp(self):
        self.old = client.CircusClient.call
        client.CircusClient.call = self._call
        fd, self._unix = tempfile.mkstemp()
        os.close(fd)

    def tearDown(self):
        client.CircusClient.call = self.old
        os.remove(self._unix)

    def _call(self, cmd):
        what = cmd['command']
        if what == 'list':
            name = cmd['properties'].get('name')
            if name is None:
                return {'watchers': ['one', 'two', 'three']}
            return {'pids': [123, 456]}
        elif what == 'dstats':
            return {'info': {'pid': 789}}
        elif what == 'listsockets':
            return {'status': u'ok',
                    'sockets': [{'path': self._unix,
                                'fd': 5,
                                'name': u'XXXX',
                                'backlog': 2048}],
                    'time': 1369647058.967524}

        raise NotImplementedError(cmd)

    def test_socketstats(self):

        endpoint = util.DEFAULT_ENDPOINT_DEALER
        pubsub = util.DEFAULT_ENDPOINT_SUB
        statspoint = util.DEFAULT_ENDPOINT_STATS

        loop = ioloop.IOLoop()
        streamer = _StatsStreamer(endpoint, pubsub, statspoint,
                                  loop=loop)

        # now the stats collector
        self._collector = SocketStatsCollector(streamer, 'sockets',
                                               callback_time=0.1,
                                               io_loop=loop)

        self._collector.start()
        loop.add_callback(streamer._init)

        # events
        def _events():
            msg = 'one.spawn.two', json.dumps({'process_pid': 187})
            for i in range(5):
                streamer.handle_recv(msg)

        deadline = time.time() + 0.5
        loop.add_timeout(deadline, _events)

        def _stop():
            self._collector.stop()
            streamer.stop()

        deadline = time.time() + 0.5
        loop.add_timeout(deadline, _stop)
        streamer.start()

        # let's see what we got
        try:
            self.assertTrue(len(streamer.msgs) > 1)
        finally:
            streamer.stop()

    def test_get_pids_circus(self):
        streamer = FakeStreamer()
        streamer.circus_pids = {1234: 'circus-top', 1235: 'circusd'}
        self.assertEquals(streamer.get_pids('circus'), [1234, 1235])

    def test_get_pids(self):
        streamer = FakeStreamer()
        streamer._pids['foobar'] = [1234, 1235]
        self.assertEquals(streamer.get_pids('foobar'), [1234, 1235])

    def test_get_all_pids(self):
        streamer = FakeStreamer()
        streamer._pids['foobar'] = [1234, 1235]
        streamer._pids['barbaz'] = [1236, 1237]
        self.assertEquals(set(streamer.get_pids()),
                          set([1234, 1235, 1236, 1237]))

    @mock.patch('os.getpid', lambda: 2222)
    def test_get_circus_pids(self):
        def _send_message(message, name=None):
            if message == 'list':
                if name == 'circushttpd':
                    return {'pids': [3333]}
                return {'watchers': ['circushttpd']}

            if message == 'dstats':
                return {'info': {'pid': 1111}}

        streamer = FakeStreamer()
        streamer.client = mock.MagicMock()
        streamer.client.send_message = _send_message

        self.assertEquals(
            streamer.get_circus_pids(),
            {1111: 'circusd', 2222: 'circusd-stats',
             3333: 'circushttpd'})

    def test_remove_pid(self):
        streamer = FakeStreamer()
        streamer._callbacks['foobar'] = mock.MagicMock()
        streamer._pids = {'foobar': [1234, 1235]}
        streamer.remove_pid('foobar', 1234)
        self.assertFalse(streamer._callbacks['foobar'].stop.called)

        streamer.remove_pid('foobar', 1235)
        self.assertTrue(streamer._callbacks['foobar'].stop.called)
