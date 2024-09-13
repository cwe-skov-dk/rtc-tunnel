#!/usr/bin/env python3

import argparse
import asyncio
import logging.handlers
import sys
import os

from tunnel import SshClient
from tunnel.signaling import IoTSignaling

from azure.iot.hub import IoTHubRegistryManager
from azure.iot.hub.models import QuerySpecification, QueryResult, Twin

import pprint


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[
        logging.handlers.TimedRotatingFileHandler('/tmp/rtc-client.log', when="midnight", backupCount=3),
        logging.StreamHandler(sys.stdout)
    ])


def resolve_device_id_from_mac(iothub_connection_string, mac_address):
    registry_manager = IoTHubRegistryManager.from_connection_string(iothub_connection_string)
    query_spec = QuerySpecification(query=f"SELECT * FROM devices WHERE properties.reported.[[macAddress]] = '{mac_address}'")
    query = registry_manager.query_iot_hub(query_spec, None, 1)
    for twin in query.items:
        return twin.device_id
    return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='RTC Tunneling client')
    parser.add_argument('--destination-port', '-d', help='Destination port', default=22)
    parser.add_argument('--device-id', '-i', help='Device id', default='')
    parser.add_argument('--mac-address', '-m', help='MAC address', default='')
    parser.add_argument('--user-name', '-u', help='User name', default='')
    # add_signaling_arguments(parser)

    args = parser.parse_args()

    iothub_connection_string = os.environ.get('IOTHUB_CONNECTION_STRING')
    method_name = 'webrtc_signaling'

    id_rsa_pub=None
    try:
        with open(os.path.join(os.path.expanduser('~'), '.ssh', 'id_rsa.pub'), 'r') as f:
            id_rsa_pub = f.read()
    except:
        pass
    id_dsa_pub=None
    try:
        with open(os.path.join(os.path.expanduser('~'), '.ssh', 'id_dsa.pub'), 'r') as f:
            id_dsa_pub = f.read()
    except:
        pass

    if args.mac_address:
       device_id = resolve_device_id_from_mac(iothub_connection_string, args.mac_address)
    else:
       device_id = args.device_id
    signal_server = IoTSignaling(iothub_connection_string, device_id, method_name, id_rsa_pub, id_dsa_pub)
    client = SshClient(device_id, args.user_name, args.destination_port, signal_server)

    loop = asyncio.get_event_loop()
    try:
        print('loop.run_until_complete')
        loop.run_until_complete(client.run_async())
        print('loop.run_until_complete: done')
    except KeyboardInterrupt:  # CTRL+C pressed
        pass
    finally:
        loop.run_until_complete(client.close_async())
        loop.close()
