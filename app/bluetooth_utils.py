# -*- coding: utf-8 -*-
# This file is adapted from https://github.com/colin-guyon/py-bluetooth-utils
# published under MIT License.

from __future__ import absolute_import

import array
import fcntl
import socket
import struct
import sys
from errno import EALREADY, EBADF, EINTR

import bluetooth._bluetooth as bluez

LE_META_EVENT = 0x3E
LE_PUBLIC_ADDRESS = 0x00

OGF_LE_CTL = 0x08
OCF_LE_SET_SCAN_PARAMETERS = 0x000B
OCF_LE_SET_SCAN_ENABLE = 0x000C

SCAN_TYPE_PASSIVE = 0x00
SCAN_FILTER_DUPLICATES = 0x01
SCAN_DISABLE = 0x00
SCAN_ENABLE = 0x01

EVT_LE_ADVERTISING_REPORT = 0x02

FILTER_POLICY_NO_WHITELIST = 0x00


def toggle_device(dev_id, enable):
    hci_sock = socket.socket(
        socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI
    )
    print(f"Power {'ON' if enable else 'OFF'} bluetooth device {dev_id}")
    req_str = struct.pack("H", dev_id)
    request = array.array("b", req_str)
    try:
        fcntl.ioctl(
            hci_sock.fileno(),
            bluez.HCIDEVUP if enable else bluez.HCIDEVDOWN,
            request[0],
        )
    except IOError as exc:
        if exc.errno == EALREADY:
            print(
                f"Bluetooth device {dev_id} is already "
                f"{'enabled' if enable else 'disabled'}"
            )
        else:
            raise
    finally:
        hci_sock.close()


def raw_packet_to_str(pkt):
    if sys.version_info > (3, 0):
        return "".join("%02x" % struct.unpack("B", bytes([x]))[0] for x in pkt)
    return "".join("%02x" % struct.unpack("B", x)[0] for x in pkt)


def enable_le_scan(
    sock,
    interval=0x0800,
    window=0x0800,
    filter_policy=FILTER_POLICY_NO_WHITELIST,
    filter_duplicates=True,
):
    print("Enable LE scan")
    own_bdaddr_type = LE_PUBLIC_ADDRESS
    cmd_pkt = struct.pack(
        "<BHHBB", SCAN_TYPE_PASSIVE, interval, window, own_bdaddr_type, filter_policy
    )
    bluez.hci_send_cmd(sock, OGF_LE_CTL, OCF_LE_SET_SCAN_PARAMETERS, cmd_pkt)
    print(
        "scan params: interval=%.3fms window=%.3fms own_bdaddr=%s whitelist=%s"
        % (
            interval * 0.625,
            window * 0.625,
            "public" if own_bdaddr_type == LE_PUBLIC_ADDRESS else "random",
            "yes" if filter_policy != FILTER_POLICY_NO_WHITELIST else "no",
        )
    )
    cmd_pkt = struct.pack(
        "<BB", SCAN_ENABLE, SCAN_FILTER_DUPLICATES if filter_duplicates else 0x00
    )
    bluez.hci_send_cmd(sock, OGF_LE_CTL, OCF_LE_SET_SCAN_ENABLE, cmd_pkt)


def disable_le_scan(sock):
    print("Disable LE scan")
    cmd_pkt = struct.pack("<BB", SCAN_DISABLE, 0x00)
    bluez.hci_send_cmd(sock, OGF_LE_CTL, OCF_LE_SET_SCAN_ENABLE, cmd_pkt)


def parse_le_advertising_events(
    sock, mac_addr=None, packet_length=None, handler=None, debug=False
):
    if not debug and handler is None:
        raise ValueError("You must either enable debug or give a handler!")

    old_filter = sock.getsockopt(bluez.SOL_HCI, bluez.HCI_FILTER, 14)
    flt = bluez.hci_filter_new()
    bluez.hci_filter_set_ptype(flt, bluez.HCI_EVENT_PKT)
    bluez.hci_filter_set_event(flt, LE_META_EVENT)
    sock.setsockopt(bluez.SOL_HCI, bluez.HCI_FILTER, flt)

    print("socket filter set to ptype=HCI_EVENT_PKT event=LE_META_EVENT")
    print("Listening ...")

    try:
        while True:
            try:
                pkt = full_pkt = sock.recv(255)
            except Exception as exc:
                err_no = getattr(exc, "errno", None)
                if err_no in (EBADF, EINTR):
                    break
                try:
                    if sock.fileno() < 0:
                        break
                except Exception:
                    break
                raise
            _ptype, event, plen = struct.unpack("BBB", pkt[:3])

            if event != LE_META_EVENT:
                continue

            sub_event = struct.unpack("B", pkt[3:4])[0]
            if sub_event != EVT_LE_ADVERTISING_REPORT:
                continue

            pkt = pkt[4:]
            adv_type = struct.unpack("b", pkt[1:2])[0]
            mac_addr_str = bluez.ba2str(pkt[3:9])

            if packet_length and plen != packet_length:
                if debug:
                    print(
                        "packet with non-matching length: mac=%s adv_type=%02x plen=%s"
                        % (mac_addr_str, adv_type, plen)
                    )
                continue

            data = pkt[9:-1]
            rssi = struct.unpack("b", full_pkt[-1:])[0]

            if mac_addr and mac_addr_str not in mac_addr:
                continue

            if debug:
                print(
                    "LE advertisement: mac=%s adv_type=%02x data=%s RSSI=%d"
                    % (mac_addr_str, adv_type, raw_packet_to_str(data), rssi)
                )

            if handler is not None:
                try:
                    handler(mac_addr_str, adv_type, data, rssi)
                except Exception as exc:
                    print(
                        "Exception when calling handler with a BLE advertising event: %r"
                        % (exc,)
                    )
    finally:
        print("\nRestore previous socket filter")
        try:
            sock.setsockopt(bluez.SOL_HCI, bluez.HCI_FILTER, old_filter)
        except Exception:
            pass
