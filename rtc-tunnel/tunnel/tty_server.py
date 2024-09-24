import asyncio
import logging
import traceback
import os

from aiortc import RTCSessionDescription, RTCPeerConnection, RTCDataChannel, RTCIceServer, RTCConfiguration
from aiortc.contrib.signaling import BYE

from .util import now
from .tasks import Tasks
from .socket_client import SocketClient

import pprint

class TtyServer:
    def __init__(self, signal_server):
        self._signal_server = signal_server
        self._tasks = Tasks()
        self._running = asyncio.Event()

    async def run_async(self):
        while not self._running.is_set():
            logging.info('[INIT] Connecting with signaling server')
            try:
                await self._signal_server.connect()
            except Exception as e:
                pprint.pp(e)
                await asyncio.sleep(300)
                continue

            logging.info('[INIT] Awaiting offers from signaling server')
            while True:
                try:
                    obj = await self._signal_server.receive()
                except Exception as e:
                    print(f'exception: {e}')
                    break
                if isinstance(obj, RTCSessionDescription) and obj.type == 'offer':
                    await self._handle_new_client_async(obj)
                else:
                    logging.info('[WARNING] Unknown request from signaling server, ignoring')
            logging.info('[EXIT] Connection with signaling server broken')


    async def _handle_new_client_async(self, obj: RTCSessionDescription):
        logging.info('[CLIENT] Creating RTC Connection')
        stun_server = RTCIceServer('stun:skovturn.northeurope.cloudapp.azure.com')
        pprint.pp(stun_server)
        turn_server = RTCIceServer('turn:skovturn.northeurope.cloudapp.azure.com', username='no', credential='bfn')
        pprint.pp(turn_server)
        rtc_config = RTCConfiguration([stun_server, turn_server])
        pprint.pp(rtc_config)
        peer_connection = RTCPeerConnection(rtc_config)
        pprint.pp(peer_connection)
        await peer_connection.setRemoteDescription(obj)
        await peer_connection.setLocalDescription(await peer_connection.createAnswer())

        logging.info('[CLIENT] Sending local descriptor to signaling server')
        await self._signal_server.send(peer_connection.localDescription)

        @peer_connection.on('datachannel')
        async def on_datachannel(channel: RTCDataChannel):
            if channel.label == 'healthcheck':
                self._configure_healthcheck(channel, peer_connection)
                logging.info('[CLIENT] Established RTC connection')
                return

            try:
                (master, slave) = os.openpty()
            except OSError as e:
                logging.info(f'[CLIENT] pty.openpty() failed: {e}')
                return

            logging.info('[CLIENT]: master %s', os.ttyname(master))
            logging.info('[CLIENT]: slave %s', os.ttyname(slave))

            async def connect_master(master):
                loop = asyncio.get_event_loop()
                reader = asyncio.StreamReader()
                proto = asyncio.StreamReaderProtocol(reader)
                await loop.connect_read_pipe(lambda: proto, master)
                w_trans, w_proto = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, master)
                writer = asyncio.StreamWriter(w_trans, w_proto, reader, loop)
                return reader, writer

            mf = os.fdopen(master, 'a+b', buffering=0)
            reader, writer = await connect_master(mf)
            logging.info('[CLIENT] opened master reader/writer from fd %d', master)

            sr = os.fdopen(slave, 'r+b', buffering=0)
            sw = os.fdopen(slave, 'w+b', buffering=0)
            logging.info('[CLIENT] opened slave reader/writer from fd %d', slave)

            p = await asyncio.create_subprocess_exec('-sh', executable='/bin/sh', stdin=sr, stdout=sw, stderr=sw, start_new_session=True)
            print(f'subprocess: stdin {p.stdin} stdout {p.stdout} stderr {p.stderr}')
            self._configure_channel(channel, reader, writer, p)
            print(f'subprocess: _configure_channel done')
            await p.wait()
            print(f'subprocess: wait() done')

            try:
                channel.close()
                logging.info('[CLIENT] channel.close() done')
            except:
                pass
            try:
                await peer_connection.close()
                logging.info('[CLIENT] _peer_connection.close() done')
            except:
                pass
            try:
                os.close(master)
                logging.info('[CLIENT] pty master close() done')
            except:
                pass
            try:
                os.close(slave)
                logging.info('[CLIENT] pty slave close() done')
            except:
                pass


    def _configure_healthcheck(self, channel: RTCDataChannel, peer_connection: RTCPeerConnection):
        outer = {'last_healthcheck': now()}

        @channel.on('message')
        def on_message(message):
            outer['last_healthcheck'] = now()

        async def healthcheck_loop_async():
            while now() - outer['last_healthcheck'] < 7000:
                try:
                    channel.send('ping')
                    await asyncio.sleep(3)
                except Exception:
                    break
            logging.info('[HEALTH CHECK] Datachannel timeout')
            await peer_connection.close()
        self._tasks.start_cancellable_task(healthcheck_loop_async())


    def _configure_channel(self, channel: RTCDataChannel, reader, writer, p):
        @channel.on('message')
        async def on_message(message):
            logging.info('[CLIENT %d] on_message(%s)', p.pid, message)
            writer.write(message)

        @channel.on('close')
        def on_close():
            logging.info('[CLIENT %d] Datachannel closed', p.pid)
            try:
                writer.write_eof()
                p.terminate()
            except:
                pass

        async def receive_loop_async():
            logging.info('[CLIENT %d] receive_loop_async()', p.pid)
            while True:
                try:
                    logging.info('[CLIENT %d] try read()', p.pid)
                    data = await reader.read(1024)
                except Exception:
                    traceback.print_exc()
                    break
                if not data:
                    logging.info('[CLIENT %d] read(): None -> break', p.pid)
                    break
                logging.info('[CLIENT %d] read(): %s', p.pid, data)
                channel.send(data)
            logging.info('[CLIENT %d] Socket connection closed', p.pid)
            channel.close()
            try:
                p.terminate()
            except:
                pass

        self._tasks.start_cancellable_task(receive_loop_async())
        logging.info('[CLIENT %d] Datachannel configured', p.pid)


    async def close_async(self):
        logging.info('[EXIT] Closing signalling server')
        self._running.set()
        if self._signal_server is not None:
            await self._signal_server.close()
        logging.info('[EXIT] Waiting for all tasks to finish')
        await self._tasks.close_async()
        logging.info('[EXIT] Closed tunneling server')
