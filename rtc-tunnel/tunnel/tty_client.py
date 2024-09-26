import asyncio
import logging
import random
import string
import traceback
import os
import sys
import termios

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCIceServer, RTCConfiguration
from aiortc.contrib.signaling import BYE

import socket
import asyncssh 

from .util import now
from .tasks import Tasks
from .socket_connection import SocketConnection

import pprint

async def consume_signaling(pc, signaling):
    while True:
        obj = await signaling.receive()
        pprint.pp(obj)

        if isinstance(obj, RTCSessionDescription):
            if obj.type != 'answer':
                logging.info('[ERROR] Unexpected answer from signaling server')
                return
            await pc.setRemoteDescription(obj)
            return
        elif isinstance(obj, RTCIceCandidate):
            await pc.addIceCandidate(obj)
        elif obj is BYE:
            print("Exiting")
            break


class TtyClient:
    def __init__(self, hostname: str, username: str, destination_port: int, signal_server):
        self._hostname = hostname
        self._username = username
        self._destination_port = destination_port
        self._signal_server = signal_server
        self._running = asyncio.Event()
        self._tasks = Tasks()
        self._peer_connection = None


    async def run_async(self):
        logging.info('[CLIENT] Creating RTC Connection')
        stun_server = RTCIceServer('stun:skovturn.northeurope.cloudapp.azure.com')
        pprint.pp(stun_server)
        # turn_server = RTCIceServer('turn:skovturn.northeurope.cloudapp.azure.com', username='no', credential='bfn')
        # pprint.pp(turn_server)
        rtc_config = RTCConfiguration([stun_server])
        pprint.pp(rtc_config)
        logging.info('[INIT] Creating RTC Connection')
        self._peer_connection = RTCPeerConnection(rtc_config)

        @self._peer_connection.on('icegatheringstatechange')
        def on_IceGatheringStateChange():
            logging.info(f'[ICE] GatheringState changed to {self._peer_connection.iceGatheringState}')
            if self._peer_connection.iceGatheringState == 'complete':
                candidates = self._peer_connection.sctp.transport.transport.iceGatherer.getLocalCandidates()
                for c in candidates:
                    logging.info(f'[ICE] LocalCandidate: {c}')

        self._create_healthcheck_channel()
        await self._peer_connection.setLocalDescription(await self._peer_connection.createOffer())

        logging.info('[INIT] Connecting with signaling server')
        await self._signal_server.connect()

        logging.info('[INIT] Sending local descriptor to signaling server')
        await self._signal_server.send(self._peer_connection.localDescription)

        logging.info('[INIT] Awaiting answer from signaling server')
        await consume_signaling(self._peer_connection, self._signal_server)
        logging.info('[INIT] Established RTC connection')

        await self._signal_server.close()
        logging.info('[INIT] Closed signaling server')

        logging.info('[INIT] Starting tty')
        await self._handle_new_client()
        logging.info('[INIT] tty started')

        await self._running.wait()
        logging.info('[EXIT] Tunneling client main loop closing')


    def _create_healthcheck_channel(self):
        channel = self._peer_connection.createDataChannel('healthcheck')
        print(f'_create_healthcheck_channel: channel {channel}')

        @channel.on('open')
        def on_open():
            outer = {'last_healthcheck': now()}

            @channel.on('close')
            def on_close():
                logging.info('[HEALTH CHECK] Datachannel closed')
                self._running.set()

            @channel.on('message')
            def on_message(message):
                outer['last_healthcheck'] = now()

            async def healthcheck_loop_async():
                while now() - outer['last_healthcheck'] < 20000:
                    try:
                        channel.send('ping')
                        try:
                            await asyncio.sleep(3)
                        except asyncio.CancelledError:
                            pass
                    except Exception:
                        break
                logging.info('[HEALTH CHECK] Datachannel timeout')
                self._running.set()
            self._tasks.start_cancellable_task(healthcheck_loop_async())


    async def _handle_new_client(self):
        client_id = ''.join(random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(8))
        logging.info('[CLIENT %s] New client connected', client_id)

        channel = self._peer_connection.createDataChannel('tunnel-%s-%s' % (client_id, self._destination_port))
        logging.info('[CLIENT %s] Datachannel %s created', client_id, channel.label)

        async def connect_stdin_stdout():
            loop = asyncio.get_event_loop()
            reader = asyncio.StreamReader()
            proto = asyncio.StreamReaderProtocol(reader)
            await loop.connect_read_pipe(lambda: proto, sys.stdin)
            w_trans, w_proto = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
            writer = asyncio.StreamWriter(w_trans, w_proto, reader, loop)
            return reader, writer

        @channel.on('open')
        async def on_open():
            reader, writer = await connect_stdin_stdout()
            self._configure_channel(channel, reader, writer, client_id)


    def _configure_channel(self, channel: RTCDataChannel, reader, writer, client_id: str):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)

        @channel.on('message')
        def on_message(message):
            writer.write(message)

        @channel.on('close')
        def on_close():
            termios.tcsetattr(fd, termios.TCSANOW, old)
            logging.info('[CLIENT %s] Datachannel %s closed', client_id, channel.label)
            self._running.set()

        print('Inside _configure_channel')

        async def receive_loop_async():
            fd = sys.stdin.fileno()

            new = termios.tcgetattr(fd)
            new[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
            new[1] &= ~(termios.OPOST)
            new[2] &= ~(termios.CSIZE | termios.PARENB)
            new[2] |= termios.CS8
            new[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
            new[6][termios.VMIN] = b'\x01'
            new[6][termios.VTIME] = b'\x00'
            termios.tcsetattr(fd, termios.TCSANOW, new)

            while True:
                try:
                    data = await reader.read(1024)
                except Exception:
                    traceback.print_exc()
                    break
                if not data:
                    break
                channel.send(data)
            logging.info('[CLIENT %s] Socket connection closed', client_id)
            channel.close()

        self._tasks.start_task(receive_loop_async())
        logging.info('[CLIENT %s] Datachannel %s configured', client_id, channel.label)

    async def close_async(self):
        self._running.set()
        logging.info('[EXIT] Closing signalling server')
        if self._signal_server is not None:
            await self._signal_server.close()
        logging.info('[EXIT] Closing RTC connection')
        if self._peer_connection is not None:
            await self._peer_connection.close()
        logging.info('[EXIT] Waiting for all tasks to finish')
        await self._tasks.close_async()
        logging.info('[EXIT] Closed tunneling client')
