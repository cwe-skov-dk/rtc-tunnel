import asyncio
import json
import sys
import os
import requests
import websockets
from json import JSONDecodeError

from aiortc import RTCSessionDescription, RTCIceCandidate
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp

import pprint

class UnixSocketSignaling:
    def __init__(self, path):
        self._path = path
        self._server = None
        self._reader = None
        self._writer = None

    async def connect(self):
        if self._writer is not None:
            return

        connected = asyncio.Event()

        def client_connected(reader, writer):
            self._reader = reader
            self._writer = writer
            connected.set()

        self._server = await asyncio.start_unix_server(
            client_connected, path=self._path
        )
        await connected.wait()

    async def close(self):
        print(f'unix close')
        if self._writer is not None:
            await self.send(BYE)
            self._writer.close()
            self._reader = None
            self._writer = None
        if self._server is not None:
            self._server.close()
            self._server = None
            os.unlink(self._path)

    async def receive(self):
        try:
            data = await self._reader.readuntil()
            print(f'unix receive: {data}')
        except asyncio.IncompleteReadError:
            print(f'unix receive: IncompleteReadError')
            return
        return object_from_string(data.decode("utf8"))

    async def send(self, descr):
        data = object_to_string(descr).encode("utf8")
        print(f'unix send: {data}')
        self._writer.write(data + b"\n")


try:
    from azure.iot.hub import IoTHubRegistryManager
    from azure.iot.hub.models import CloudToDeviceMethod

    class IoTSignaling:
        def __init__(self, iothub_connection_string: str, device_id: str, method_name: str, id_rsa_pub=None, id_dsa_pub=None):
            self._iothub_connection_string = iothub_connection_string
            self._device_id = device_id
            self._method_name = method_name
            self._registry_manager = None
            self._response = None
            self._id_rsa_pub = id_rsa_pub
            self._id_dsa_pub = id_dsa_pub
    
        async def connect(self):
            self._registry_manager = IoTHubRegistryManager.from_connection_string(self._iothub_connection_string)
            return 0
    
        async def close(self):
            self._registry_manager = None
    
        async def send(self, descr):
            print('descr: ')
            pprint.pp(descr);
            message = object_to_dict(descr, self._id_rsa_pub, self._id_dsa_pub)
            print('message: ')
            pprint.pp(message);
            method = CloudToDeviceMethod(method_name=self._method_name, payload=message)
            print('method: ')
            pprint.pp(method);
            response = self._registry_manager.invoke_device_method(self._device_id, method).as_dict()
            print('response: ')
            pprint.pp(response)
            if response['status'] == 200:
                self._response = response['payload']
    
        async def receive(self):
            print('response: ')
            pprint.pp(self._response)
            if self._response:
                print('obj: ')
                obj = object_from_dict(self._response)
                pprint.pp(obj)
                self._response = None
                return obj
            return None
except:
    pass


def object_to_string(obj):
    if isinstance(obj, RTCSessionDescription):
        message = { 'sdp': obj.sdp, 'type': obj.type }
    elif isinstance(obj, RTCIceCandidate):
        message = {
            'candidate': 'candidate:' + candidate_to_sdp(obj),
            'id': obj.sdpMid,
            'label': obj.sdpMLineIndex,
            'type': 'candidate',
        }
    else:
        assert obj is BYE
        message = { 'type': 'bye' }
    return json.dumps(message, sort_keys=True)

def object_to_dict(obj, id_rsa_pub=None, id_dsa_pub=None):
    if isinstance(obj, RTCSessionDescription):
        dict = { 'sdp': obj.sdp, 'type': obj.type }
    elif isinstance(obj, RTCIceCandidate):
        dict = {
            'candidate': 'candidate:' + candidate_to_sdp(obj),
            'id': obj.sdpMid,
            'label': obj.sdpMLineIndex,
            'type': 'candidate',
        }
    else:
        assert obj is BYE
        dict = { 'type': 'bye' }
    if id_rsa_pub:
        dict['id_rsa_pub'] = id_rsa_pub
    if id_dsa_pub:
        dict['id_dsa_pub'] = id_dsa_pub
    return dict

def add_authorized_key(pub_key):
    with open(os.path.join(os.path.expanduser('~'), '.ssh', 'authorized_keys'), 'r+') as f:
        for line in f:
            if pub_key in line:
                break
        else:
            f.write(pub_key)

def object_from_string(message):
    data = json.loads(message)

    if 'id_rsa_pub' in data:
        add_authorized_key(data['id_rsa_pub'])
    if 'id_dsa_pub' in data:
        add_authorized_key(data['id_dsa_pub'])

    if data['type'] in ['answer', 'offer']:
        return RTCSessionDescription(sdp=data['sdp'], type=data['type'])
    elif data['type'] == 'candidate':
        candidate = candidate_from_sdp(data['candidate'].split(':', 1)[1])
        candidate.sdpMid = data['id']
        candidate.sdpMLineIndex = data['label']
        return candidate
    elif message['type'] == 'bye':
        return BYE

def object_from_dict(message):
    if message['type'] in ['answer', 'offer']:
        return RTCSessionDescription(sdp=message['sdp'], type=message['type'])
    elif message['type'] == 'candidate' and message['candidate']:
        candidate = candidate_from_sdp(message['candidate'].split(':', 1)[1])
        candidate.sdpMid = message['id']
        candidate.sdpMLineIndex = message['label']
        return candidate
    elif message['type'] == 'bye':
        return BYE
