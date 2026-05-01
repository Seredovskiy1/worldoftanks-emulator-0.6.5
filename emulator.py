import socket
import struct
import select
import os
import time
import threading
import pickle
import zlib
import builtins
import math
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP, Blowfish


_raw_print = print


def _fix_mojibake_text(value):
    if not isinstance(value, str):
        return value
    if not any(marker in value for marker in ('Р', 'С', 'вЂ', 'в”')):
        return value
    try:
        return value.encode('cp1251').decode('utf-8')
    except UnicodeError:
        return value


def print(*args, **kwargs):
    _raw_print(*[_fix_mojibake_text(arg) for arg in args], **kwargs)


builtins.print = print

LOGIN_PORT  = 20016
BASEAPP_PORT = 20017

# { token_bytes : { 'bf_key': bytes, 'addr': (ip,port) } }
active_sessions = {}

print("[*] Р—Р°РІР°РЅС‚Р°Р¶СѓС”РјРѕ RSA РєР»СЋС‡С–...")
try:
    with open("private_key.pem", "rb") as f:
        private_key = RSA.import_key(f.read())
except Exception as e:
    print(f"[!] РџРћРњРР›РљРђ: РќРµРјР°С” private_key.pem! {e}"); exit()

# в”Ђв”Ђ RSA / parse в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def rsa_decrypt_oaep(blob, key):
    cipher = PKCS1_OAEP.new(key)
    try: return cipher.decrypt(blob)
    except: pass
    try: return cipher.decrypt(blob[::-1])
    except: return None

def parse_logon_params(data):
    try:
        p = 0
        p += 1                                      # flags byte
        ul = data[p]; p += 1
        user = data[p:p+ul].decode('ascii','ignore'); p += ul
        pl = data[p]; p += 1
        pwd  = data[p:p+pl].decode('ascii','ignore'); p += pl
        kl = data[p]; p += 1
        key  = data[p:p+kl]
        return user, pwd, key
    except: return None, None, None

def find_rsa_block(data):
    for start in range(15, 30):
        if start + 256 > len(data): continue
        dec = rsa_decrypt_oaep(data[start:start+256], private_key)
        if dec and dec[0] in (0x00, 0x01):
            return dec
    return None

# в”Ђв”Ђ BigWorld Blowfish (XOR-chain ECB) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def bw_bf_encrypt(plaintext, key):
    """BigWorld custom Blowfish: block[i] = BF( plain[i] XOR plain[i-1] )"""
    BS = 8
    pad = (BS - len(plaintext) % BS) % BS
    data = plaintext + b'\x00' * pad
    c = Blowfish.new(key, Blowfish.MODE_ECB)
    prev = None; out = b''
    for i in range(0, len(data), BS):
        blk = data[i:i+BS]
        mod = blk if prev is None else bytes(a^b for a,b in zip(blk, prev))
        out += c.encrypt(mod)
        prev = blk
    return out

def bw_bf_decrypt_alt(ciphertext, key):
    """Alternate chaining attempt: XOR with previous ciphertext block."""
    BS = 8
    if len(ciphertext) % BS != 0:
        return None
    c = Blowfish.new(key, Blowfish.MODE_ECB)
    prev_cipher = None
    out = b''
    for i in range(0, len(ciphertext), BS):
        enc_blk = ciphertext[i:i+BS]
        mod = c.decrypt(enc_blk)
        plain_blk = mod if prev_cipher is None else bytes(a ^ b for a, b in zip(mod, prev_cipher))
        out += plain_blk
        prev_cipher = enc_blk
    return out

def bw_bf_decrypt(ciphertext, key):
    """Reverse of BigWorld custom Blowfish XOR-chain ECB."""
    BS = 8
    if len(ciphertext) % BS != 0:
        return None
    c = Blowfish.new(key, Blowfish.MODE_ECB)
    prev_plain = None
    out = b''
    for i in range(0, len(ciphertext), BS):
        enc_blk = ciphertext[i:i+BS]
        mod = c.decrypt(enc_blk)
        plain_blk = mod if prev_plain is None else bytes(a ^ b for a, b in zip(mod, prev_plain))
        out += plain_blk
        prev_plain = plain_blk
    return out

ENCRYPTION_MAGIC = 0xdeadbeef

# Mercury Packet flags (little-endian on this client build)
PACKET_FLAG_HAS_REQUESTS = 0x0001
PACKET_FLAG_HAS_ACKS = 0x0004
PACKET_FLAG_ON_CHANNEL = 0x0008
PACKET_FLAG_IS_FRAGMENT = 0x0020
PACKET_FLAG_HAS_SEQUENCE_NUMBER = 0x0040
PACKET_FLAG_HAS_CUMULATIVE_ACK = 0x0400
PACKET_SEQ_MASK = 0x0FFFFFFF

def bw_encrypt_packet(packet: bytes, key: bytes) -> bytes:
    """Encrypt full Mercury packet like BigWorld EncryptionFilter::send()."""
    BS = 8
    base_len = len(packet) + 4  # + ENCRYPTION_MAGIC
    wastage = ((BS - ((base_len + 1) % BS)) % BS) + 1

    # footer: [pad bytes][magic uint32][wastage uint8]
    footer = (b'\x00' * (wastage - 1)) + struct.pack('<I', ENCRYPTION_MAGIC) + bytes([wastage])
    plain = packet + footer
    return bw_bf_encrypt(plain, key)

def bw_decrypt_packet(packet: bytes, key: bytes):
    """Decrypt full Mercury packet and strip BigWorld encryption footer."""
    for clear in (bw_bf_decrypt(packet, key), bw_bf_decrypt_alt(packet, key)):
        if not clear or len(clear) < 5:
            continue

        wastage = clear[-1]
        footer_size = wastage + 4  # wastage includes wastage byte itself, + magic(4)
        if wastage < 1 or wastage > 8 or footer_size > len(clear):
            continue

        # In BigWorld packet footer layout, magic is always right before the
        # final wastage byte (independent of wastage value): [...][magic(4)][w]
        magic_pos = len(clear) - 5
        magic_le = struct.unpack('<I', clear[magic_pos:magic_pos + 4])[0]
        magic_be = struct.unpack('>I', clear[magic_pos:magic_pos + 4])[0]
        if magic_le != ENCRYPTION_MAGIC and magic_be != ENCRYPTION_MAGIC:
            continue

        return clear[:-footer_size]

    return None

def split_packet_body(packet: bytes):
    """Split Mercury packet into message-body part and parsed footer info."""
    if len(packet) < 2:
        return packet, 0, None, None

    flags = struct.unpack('<H', packet[:2])[0]
    idx = len(packet)
    seq = None
    cumulative_ack = None

    # Footer strip order must match PacketReceiver::processFilteredPacket()
    if flags & PACKET_FLAG_HAS_CUMULATIVE_ACK:
        if idx < 4:
            return packet, flags, None, None
        cumulative_ack = struct.unpack('<I', packet[idx - 4:idx])[0]
        idx -= 4

    if flags & PACKET_FLAG_HAS_ACKS:
        if idx < 1:
            return packet, flags, None, cumulative_ack
        ack_count = packet[idx - 1]
        idx -= 1
        ack_bytes = ack_count * 4
        if idx < ack_bytes:
            return packet, flags, None, cumulative_ack
        idx -= ack_bytes

    if flags & PACKET_FLAG_HAS_SEQUENCE_NUMBER:
        if idx < 4:
            return packet, flags, None, cumulative_ack
        seq = struct.unpack('<I', packet[idx - 4:idx])[0]
        idx -= 4

    if flags & PACKET_FLAG_HAS_REQUESTS:
        if idx < 2:
            return packet, flags, seq, cumulative_ack
        idx -= 2

    if flags & PACKET_FLAG_IS_FRAGMENT:
        if idx < 8:
            return packet, flags, seq, cumulative_ack
        idx -= 8

    return packet[:idx], flags, seq, cumulative_ack

def update_in_seq_state(sess: dict, seq: int):
    """Track next expected incoming seq for cumulative ACK generation."""
    if seq is None:
        return

    in_seq = sess.setdefault('in_seq_at', 0)
    buffered = sess.setdefault('in_seq_buffered', set())

    if seq == in_seq:
        in_seq = (in_seq + 1) & PACKET_SEQ_MASK
        while in_seq in buffered:
            buffered.remove(in_seq)
            in_seq = (in_seq + 1) & PACKET_SEQ_MASK
        sess['in_seq_at'] = in_seq
    elif seq > in_seq:
        buffered.add(seq)

def build_channel_packet(messages: bytes, sess: dict, reliable=False) -> bytes:
    # BigWorld С‚СЂРёРјР°С” Р”Р’Рђ РѕРєСЂРµРјРёС… Р»С–С‡РёР»СЊРЅРёРєРё sequence:
    #   - reliable РїР°РєРµС‚Рё    в†’ channel.useNextSequenceID()
    #   - non-reliable       в†’ networkInterface.getNextSequenceID()
    # РљР»С–С”РЅС‚СЃСЊРєРёР№ reliable-window РїРµСЂРµРІС–СЂСЏС” РїРѕСЃР»С–РґРѕРІРЅС–СЃС‚СЊ РўР†Р›Р¬РљР РґР»СЏ reliable.
    # РЇРєС‰Рѕ Р·РјС–С€Р°С‚Рё вЂ” reliable РїР°РєРµС‚Рё Р№РґСѓС‚СЊ Р· РїСЂРѕРїСѓСЃРєР°РјРё С– РєР»С–С”РЅС‚ С—С… Р±СѓС„РµСЂРёР·СѓС”
    # РЅР°Р·Р°РІР¶РґРё, РЅРµ РІРёРєРѕРЅСѓСЋС‡Рё. РўСЂРёРјР°С”РјРѕ РґРІР° РѕРєСЂРµРјРёС… counter'Р°.
    if reliable:
        out_seq = sess.setdefault('out_channel_seq', 0)
        sess['out_channel_seq'] = (out_seq + 1) & PACKET_SEQ_MASK
    else:
        out_seq = sess.setdefault('out_nub_seq', 0)
        sess['out_nub_seq'] = (out_seq + 1) & PACKET_SEQ_MASK

    in_seq = sess.setdefault('in_seq_at', 0)

    flags = (PACKET_FLAG_ON_CHANNEL |
             PACKET_FLAG_HAS_SEQUENCE_NUMBER |
             PACKET_FLAG_HAS_CUMULATIVE_ACK)
    if reliable:
        flags |= 0x0010

    pkt = struct.pack('<H', flags)
    pkt += messages
    pkt += struct.pack('<I', out_seq)
    pkt += struct.pack('<I', in_seq)
    return pkt

def build_channel_ack_packet(sess: dict) -> bytes:
    return build_channel_packet(b'', sess, reliable=False)

BASEAPP_EXT_FIXED_LENGTHS = {
    1: 4,    # authenticate
    2: 16,   # avatarUpdateImplicit: Coord + YawPitchRoll + refNum
    3: 25,   # avatarUpdateExplicit: spaceID + vehicleID + Coord + YPR + onGround + refNum
    4: 19,   # avatarUpdateWardImplicit: wardID + Coord + YPR
    5: 28,   # avatarUpdateWardExplicit: wardID + spaceID + vehicleID + Coord + YPR + onGround
    6: 1,    # ackPhysicsCorrection
    7: 5,    # ackWardPhysicsCorrection
    8: 0,
    10: 1,
    11: 4,
    12: 1,
}

def iter_baseapp_ext_messages(body: bytes):
    pos = 2
    while pos < len(body):
        msg = body[pos]
        start = pos
        pos += 1

        if msg == 9 or msg >= 128:
            if pos + 2 > len(body):
                yield msg, body[pos:], start
                return
            size = struct.unpack('<H', body[pos:pos + 2])[0]
            pos += 2
            payload = body[pos:pos + size]
            pos += size
            yield msg, payload, start
            continue

        size = BASEAPP_EXT_FIXED_LENGTHS.get(msg)
        if size is None:
            yield msg, body[pos:], start
            return

        payload = body[pos:pos + size]
        pos += size
        yield msg, payload, start

# в”Ђв”Ђ Mercury packet helpers в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def make_reply(reply_id: bytes, payload: bytes) -> bytes:
    """0xFF reply: flags(2) + 0xFF(1) + len(4) + reply_id(4) + payload"""
    body = reply_id + payload
    return b'\x00\x00\xff' + struct.pack('<I', len(body)) + body

def make_bundle(messages: bytes) -> bytes:
    """Plain serverв†’client bundle (no requests, no encryption)"""
    return b'\x00\x00' + messages

def msg_fixed(msg_id: int, payload: bytes) -> bytes:
    return bytes([msg_id]) + payload

def msg_varlen(msg_id: int, payload: bytes) -> bytes:
    return bytes([msg_id]) + struct.pack('<H', len(payload)) + payload

# в”Ђв”Ђ ClientInterface MsgIDs в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
#  0x00 authenticate          fixed  4 B  (uint32 key)
#  0x01 bandwidthNotification fixed  4 B  (uint32 bps)
#  0x02 updateFrequencyNotif  fixed  1 B  (uint8  hertz)
#  0x03 setGameTime           fixed  4 B  (uint32 gameTime)
#  0x04 resetEntities         fixed  1 B  (bool   keepPlayer)
#  0x05 createBasePlayer      varlen 2 B  EntityID(4)+EntityTypeID(2)+props
#  0x80+N entityMessage       varlen 2 B  EntityID(4)+methodArgs

ACCOUNT_ENTITY_TYPE = 0   # Account вЂ” 1-Р№ С‚РёРї Сѓ entities.xml (idx=0)
AVATAR_ENTITY_TYPE  = 1   # Avatar  вЂ” 2-Р№ С‚РёРї Сѓ entities.xml (idx=1).
                          # Р’РђР–Р›РР’Рћ: parse_def.py РґР°С” Р‘Р•Р— alphasort:
                          # 0=Account 1=Avatar 2=Admin 3=Arena 4=ArenaMgr
                          # 5=Vehicle 6=Projectile 7=AreaDestructibles ...
                          # РџРѕРјРёР»РєРѕРІРёР№ typeID=6 Р»Р°РјР°РІ createBasePlayer:
                          # РєР»С–С”РЅС‚ РЅР°РјР°РіР°РІСЃСЏ СЃС‚РІРѕСЂРёС‚Рё Projectile Р·
                          # Avatar-property stream в†’ C++ segfault.
VEHICLE_ENTITY_TYPE = 2   # CLIENT-SIDE clientIndex! РќР• server idx=5.
                          # entity_type.cpp:209 РІРёРєРѕСЂРёСЃС‚РѕРІСѓС” clientIndex,
                          # СЏРєРёР№ РљРћРњРџР Р•РЎРЈР„ С–РЅРґРµРєСЃРё (server-only entities
                          # Р±РµР· .pyc РЅР° РєР»С–С”РЅС‚С– вЂ” РїСЂРѕРїСѓСЃРєР°СЋС‚СЊСЃСЏ).
                          # РЈ WoT 0.6.5 РєР»С–С”РЅС‚ РјР°С” .pyc РґР»СЏ:
                          # Account, Avatar, Vehicle, AreaDestructibles,
                          # Flock, OfflineEntity в†’ clientIndex 0..5.
                          # Vehicle (server idx=5) в†’ clientIndex=2.
PLAYER_ENTITY_ID    = 1   # any non-zero EntityID for our player (Account)
AVATAR_ENTITY_ID    = 100 # РѕРєСЂРµРјРёР№ EntityID РґР»СЏ Avatar entity Сѓ Р±РѕСЋ
PLAYER_VEHICLE_ID   = 200 # EntityID РґР»СЏ Vehicle entity, РЅР° СЏРєРѕРјСѓ РіСЂР°С” player
SPACE_ID            = 1   # arbitrary SpaceID for hangar

# в”Ђв”Ђв”Ђ Account.def ClientMethods (РїРѕСЂСЏРґРѕРє Р· PackedSection РїР°СЂСЃРµСЂР°) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
#   idx=0  version_eu6501      (РјР°СЂker, 0x81)
#   idx=1  onCmdResponse       0x82  (INT16 reqID, INT16 resultID)
#   idx=2  onCmdResponseExt    0x83  (INT16 reqID, INT16 resultID, STRING ext)
#   idx=3  onKickedFromServer  0x84
#   idx=4  onEnqueued          0x85  (no args)
#   idx=5  onEnqueueFailure    0x86  (INT8 errorCode)
#   idx=6  onDequeued          0x87  (no args)
#   idx=7  onArenaCreated      0x88  (no args)
#   idx=8  onArenaJoinFailure  0x89  (INT8 errorCode)
#   idx=12 onKickedFromQueue   0x8d  (no args)
#   idx=13 onKickedFromArena   0x8e  (INT8 reasonCode)
#   idx=15 showGUI             0x90  (STRING ctx)
#   idx=20 update              0x95  (STRING diff)
# Р’РђР–Р›РР’Рћ: СЂРµР°Р»СЊРЅРёР№ msgID = 0x81 + def_index (Mercury РїСЂРѕРїСѓСЃРєР°С” idx=0).
ACCOUNT_SHOWGUI_MSG_ID          = 0x90
ACCOUNT_ONCMDRESPONSE_MSG_ID    = 0x82
ACCOUNT_ONCMDRESPONSEEXT_MSG_ID = 0x83
ACCOUNT_UPDATE_MSG_ID           = 0x95
ACCOUNT_ONENQUEUED_MSG_ID       = 0x85
ACCOUNT_ONDEQUEUED_MSG_ID       = 0x87
ACCOUNT_ONARENACREATED_MSG_ID   = 0x88
ACCOUNT_ONKICKEDFROMQUEUE_MSG_ID = 0x8d
AVATAR_UPDATEARENA_MSG_ID        = 0x92  # Avatar.updateArena(updateType, argStr)
AVATAR_UPDATE_TARGETING_INFO_MSG_ID = 0x87
AVATAR_UPDATE_GUN_MARKER_MSG_ID     = 0x88
AVATAR_UPDATE_OWN_POSITION_MSG_ID   = 0x89
CLIENT_DETAILED_POSITION_MSG_ID     = 0x31
CLIENT_FORCED_POSITION_MSG_ID       = 0x32
CLIENT_CONTROL_ENTITY_MSG_ID        = 0x33
CLIENT_SET_VEHICLE_MSG_ID           = 0x10
CLIENT_AVATAR_UPDATE_NOALIAS_FULLPOS_YPR_MSG_ID = 0x11

# Account exposed BaseMethods (client в†’ server):
#   idx=3 msgID=0xc3  doCmdStr      (INT16 reqID, INT16 cmd, STRING)
#   idx=4 msgID=0xc4  doCmdInt3     (INT16 reqID, INT16 cmd, INT32, INT32, INT32)
#   idx=5 msgID=0xc5  doCmdInt4     (INT16 reqID, INT16 cmd, 4 x INT32)
#   idx=6 msgID=0xc6  doCmdInt2Str  (INT16 reqID, INT16 cmd, INT32, INT32, STRING)
#   idx=7 msgID=0xc7  doCmdIntArr   (INT16 reqID, INT16 cmd, INT32 array)
ACCOUNT_DOCMD_MSG_IDS = {0xc3, 0xc4, 0xc5, 0xc6, 0xc7}

# BigWorld native streaming (lib/connection/client_interface.hpp + common):
#   #55 = 0x37  resourceHeader   varlen2 B  (uint16 id, STRING desc)
#   #56 = 0x38  resourceFragment varlen2 B  (uint16 rid, uint8 seq,
#                                             uint8 flags, raw data)
# flags=1 в†’ final fragment (sets DataDownload.hasLast_=true, see
# server_connection.cpp:2168 вЂ” `pData->insert(seg, args.flags == 1)`).
# РћРґРёРЅ fragment Р· flags=1 + header в†’ onStreamComplete fires РѕРґСЂР°Р·Сѓ.
RESOURCE_HEADER_MSG_ID   = 0x37
RESOURCE_FRAGMENT_MSG_ID = 0x38

# WoT AccountCommands (Р· res/scripts/common/AccountCommands.py):
CMD_SYNC_DATA    = 100      # cmd=100 в†’ РґС–С„С„ (РЅР°С€ full_sync) Сѓ ext
CMD_SYNC_SHOP    = 300      # cmd=300 в†’ Р’Р•Р›РРљРР™ shop dict С‡РµСЂРµР· STREAM
CMD_SYNC_DOSSIERS = 600
CMD_SET_LANGUAGE = 1000
CMD_REQ_SERVER_STATS = 501
CMD_ENQUEUE_FOR_ARENA = 700  # vehInvID, arenaTypeID, queueType
CMD_DEQUEUE      = 701
RES_SUCCESS      = 0
RES_STREAM       = 1
RES_CACHE        = 2


def bw_pack_int(value: int) -> bytes:
    if value >= 0xFF:
        return b'\xff' + bytes((value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF))
    return bytes((value,))


def bw_pack_string(data: bytes) -> bytes:
    return bw_pack_int(len(data)) + data

def build_init_bundle(session_key_int: int) -> bytes:
    msgs = b''

    # 0x00 authenticate вЂ“ server proves it knows the session key
    msgs += msg_fixed(0x00, struct.pack('<I', session_key_int))

    # 0x01 bandwidthNotification вЂ“ 64 KB/s
    msgs += msg_fixed(0x01, struct.pack('<I', 64000))

    # 0x02 updateFrequencyNotification вЂ“ 10 Hz
    msgs += msg_fixed(0x02, struct.pack('<B', 10))

    # 0x03 setGameTime вЂ“ tick 0
    msgs += msg_fixed(0x03, struct.pack('<I', 0))

    # 0x04 resetEntities вЂ“ don't keep player
    msgs += msg_fixed(0x04, struct.pack('<B', 0))

    # 0x05 createBasePlayer
    #   EntityID(4 LE) + EntityTypeID(2 LE) + base+client property stream
    #   Account РјР°С” 3 BASE_AND_CLIENT РІР»Р°СЃС‚РёРІРѕСЃС‚С– (РїРѕСЂСЏРґРѕРє СЏРє Сѓ Account.def):
    #     1. name           (STRING)
    #     2. normalizedName (STRING)
    #     3. serverSettings (PYTHON  в†’  pickled dict)
    #   serverSettings РџРћР’РРќР•Рќ РјС–СЃС‚РёС‚Рё vivoxDomain/vivoxIssuer/voipDomain вЂ”
    #   С–РЅР°РєС€Рµ Account.onBecomePlayer РїР°РґР°С” Р· 'NoneType is unsubscriptable'
    #   (РґРёРІ. World_of_Tanks/res/scripts/client/Account.py:142).
    #   captchaKey РќР• РїРѕС‚СЂС–Р±РµРЅ: CaptchaUI.isCaptchaRequired() Р±Р°Р·СѓС”С‚СЊСЃСЏ
    #   Р»РёС€Рµ РЅР° _battlesTillCaptcha (Stats _CACHE_STATS), СЏРєРµ РјРё Р·Р°РґР°С”РјРѕ
    #   РІ diff['cache']['battlesTillCaptcha'] = 999.
    server_settings_pickle = pickle.dumps({
        'vivoxDomain':  '',
        'vivoxIssuer':  '',
        'voipDomain':   '',
        'serverUTC':    0,
    }, protocol=0)
    props = b''
    props += bw_pack_string(b'qwerty')               # name
    props += bw_pack_string(b'qwerty')               # normalizedName
    props += bw_pack_string(server_settings_pickle)  # serverSettings (PYTHON)

    cbp_payload = struct.pack('<I', PLAYER_ENTITY_ID) + \
                  struct.pack('<H', ACCOUNT_ENTITY_TYPE) + \
                  props
    msgs += msg_varlen(0x05, cbp_payload)

    # Account.showGUI(ctx)  вЂ” entityMessage, msgID=0x90
    # РўСЂРёРіРµСЂРёС‚СЊ WindowsManager.showLobby в†’ CommonPage.processLobby.
    showgui_ctx = b"(dp0\nS'databaseID'\np1\nL1L\nsS'serverUTC'\np2\nL0L\ns."
    em = struct.pack('<I', PLAYER_ENTITY_ID) + bw_pack_string(showgui_ctx)
    msgs += msg_varlen(ACCOUNT_SHOWGUI_MSG_ID, em)

    return msgs


def build_oncmdrespext(req_id: int, result_id: int, ext_pickle: bytes) -> bytes:
    """Account.onCmdResponseExt(reqID, resultID, ext) вЂ” entityMessage 0x83."""
    em = struct.pack('<I', PLAYER_ENTITY_ID)
    em += struct.pack('<hh', req_id, result_id)  # INT16, INT16
    em += bw_pack_string(ext_pickle)             # STRING (PYTHON pickle)
    return msg_varlen(ACCOUNT_ONCMDRESPONSEEXT_MSG_ID, em)


def parse_doCmd_request(msg_id: int, payload: bytes):
    """Р’РёС‚СЏРіРЅСѓС‚Рё (reqID, cmd) Р· doCmd* РїР°РєРµС‚Сѓ. Args РїРѕС‡РёРЅР°СЋС‚СЊСЃСЏ Р· INT16+INT16."""
    if len(payload) < 4:
        return None, None
    req_id, cmd = struct.unpack_from('<hh', payload, 0)
    return req_id, cmd


def parse_doCmd_int3(payload: bytes):
    if len(payload) < 16:
        return None
    return struct.unpack_from('<iii', payload, 4)


MAX_VEHICLES_INLINE = None  # None = all vehicles; full sync is streamed.


def load_all_vehicles():
    """Р§РёС‚Р°С” _vehicles.json (РіРµРЅРµСЂСѓС”С‚СЊСЃСЏ _dump_vehicles.py) С– РїРѕРІРµСЂС‚Р°С”
    СЃРїРёСЃРѕРє (invID, compactDescr_bytes, name) РґР»СЏ СѓСЃС–С… С‚Р°РЅРєС–РІ РіСЂРё.
    РћР±РјРµР¶СѓС” РґРѕ MAX_VEHICLES_INLINE С‰РѕР± pickle РІРјС–СЃС‚РёРІСЃСЏ РІ РѕРґРёРЅ UDP packet."""
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '_vehicles.json')
    if not os.path.exists(json_path):
        print(f"[!] _vehicles.json РЅРµ Р·РЅР°Р№РґРµРЅРѕ вЂ” Р°РЅРіР°СЂ Р±СѓРґРµ РїРѕСЂРѕР¶РЅС–Рј")
        return []
    import json
    with open(json_path, 'r') as f:
        data = json.load(f)
    out = []
    vehicles_data = data['vehicles']
    if MAX_VEHICLES_INLINE is not None:
        vehicles_data = vehicles_data[:MAX_VEHICLES_INLINE]
    for inv_id, v in enumerate(vehicles_data, start=1):
        cd = bytes.fromhex(v['compactDescr_hex'])
        crew_size = v.get('crewSize', 4)
        out.append({
            'inv_id': inv_id,
            'compactDescr': cd,
            'name': v['name'],
            'crewSize': crew_size,
            'turretCompactDescr': v.get('turretCompactDescr', 0),
            'gunCompactDescr': v.get('gunCompactDescr', 0),
            'defaultAmmo': list(v.get('defaultAmmo', [])),
            'shells': list(v.get('shells', [])),
        })
    return out


def collect_shell_prices(veh_list):
    """Return {nationID: {shellCompactDescr: priceTuple}} for loaded vehicles."""
    shell_prices = {}
    for vehicle in veh_list:
        for shell in vehicle.get('shells', []):
            compact = int(shell.get('compactDescr', 0))
            if not compact:
                continue
            nation_id = (compact >> 4) & 15
            price = shell.get('price', (0, 0))
            shell_prices.setdefault(nation_id, {})[compact] = tuple(price)
    return shell_prices


def get_vehicle_by_inventory_id(inv_id: int):
    for vehicle in load_all_vehicles():
        if vehicle['inv_id'] == inv_id:
            return vehicle
    return None


def get_vehicle_compact_descr(inv_id: int = None) -> bytes:
    vehicle = get_vehicle_by_inventory_id(inv_id) if inv_id else None
    if vehicle is not None:
        return vehicle['compactDescr']
    veh_list = load_all_vehicles()
    if veh_list:
        return veh_list[0]['compactDescr']
    return b'\x00' * 22


def make_full_sync_pickle() -> bytes:
    """РџРѕРІРЅРёР№ (full-sync) diff РґР»СЏ Account._update. Р‘РµР· 'prevRev' в†’
    isFullSync=True в†’ РєР»С–С”РЅС‚ С‡РёСЃС‚РёС‚СЊ __cache С– Р·Р°СЃС‚РѕСЃРѕРІСѓС” РЅР°С€ diff.

    inventory[itemTypeIdx] РњРЈРЎРРўР¬ РјС–СЃС‚РёС‚Рё sub-dicts, С–РЅР°РєС€Рµ
    InventoryParser.parseVehicles РєСЂР°С€РёС‚СЊ Р· 'NoneType is unsubscriptable'
    РЅР° data['compDescr'] / data['crew'] etc.
    ITEM_TYPE_INDICES (Р· res/scripts/common/items/__init__.py):
      1=vehicle, 2=vehicleChassis, 3=vehicleTurret, 4=vehicleGun,
      5=vehicleEngine, 6=vehicleFuelTank, 7=vehicleRadio, 8=tankman,
      9=optionalDevice, 10=shell, 11=equipment.
    """
    veh_list = load_all_vehicles()

    # InventoryParser.parseVehicles С‡РёС‚Р°С” С‚Р°РєС– sub-dicts:
    #   compDescr[id]    в†’ vehCompDescr (raw bytes)
    #   shellsLayout[id] в†’ dict
    #   shells[id]       в†’ list
    #   crew[id]         в†’ list (tankman invIDs)
    #   repair[id]       в†’ tuple (repairCost, health)
    #   eqsLayout[id]    в†’ list[3]
    #   eqs[id]          в†’ list[3]
    #   settings[id]     в†’ int
    #   lock[id]         в†’ int
    veh_compDescr = {}
    veh_shellsLayout = {}
    veh_shells = {}
    veh_crew = {}
    veh_repair = {}
    veh_eqsLayout = {}
    veh_eqs = {}
    veh_settings = {}
    veh_lock = {}
    # CurrentVehicle.isCrewFull():
    #   None not in vehicle.crew  AND  vehicle.crew != []
    # Hangar.__updateTankmen С–С‚РµСЂСѓС” `for i in range(len(crew))` С– РґР»СЏ
    # РєРѕР¶РЅРѕРіРѕ i С‡РёС‚Р°С” `crewRoles[i]`, С‚РѕРјСѓ len(crew) РјР°С” == crewSize.
    # invID=0 вЂ” "РїРѕР·Р° С–РЅРІРµРЅС‚Р°СЂРµРј" tankman: Hangar С€СѓРєР°С” Р№РѕРіРѕ РІ `tankmen`,
    # РЅРµ Р·РЅР°С…РѕРґРёС‚СЊ в†’ tman=None в†’ РїСЂРѕРїСѓСЃРєР°С” СЃРєС–Р»-СЃРµРєС†С–СЋ (Р±РµР· РєСЂР°С€Сѓ),
    # РђР›Р• СѓРјРѕРІР° isCrewFull() РїСЂРѕР№РґРµ.
    FAKE_TANKMAN_INVID = 0
    shell_inventory = {}
    for vehicle in veh_list:
        inv_id = vehicle['inv_id']
        cd = vehicle['compactDescr']
        crew_size = vehicle['crewSize']
        default_ammo = list(vehicle.get('defaultAmmo') or [])
        shells_layout = {}
        turret_cd = vehicle.get('turretCompactDescr', 0)
        gun_cd = vehicle.get('gunCompactDescr', 0)
        if turret_cd and gun_cd and default_ammo:
            shells_layout[(turret_cd, gun_cd)] = list(default_ammo)

        veh_compDescr[inv_id] = cd
        veh_shellsLayout[inv_id] = shells_layout
        veh_shells[inv_id] = list(default_ammo)
        veh_crew[inv_id] = [FAKE_TANKMAN_INVID] * crew_size
        veh_repair[inv_id] = (0, 1.0)        # 0 cost, full health
        veh_eqsLayout[inv_id] = [0, 0, 0]
        veh_eqs[inv_id] = [0, 0, 0]
        veh_settings[inv_id] = 0
        veh_lock[inv_id] = 0
        for i in range(0, len(default_ammo), 2):
            compact = default_ammo[i]
            count = default_ammo[i + 1]
            shell_inventory[compact] = shell_inventory.get(compact, 0) + count

    print(f"[*] Inventory: {len(veh_list)} vehicles Р·Р°РІР°РЅС‚Р°Р¶РµРЅРѕ")
    slots_count = max(200, len(veh_list) + 10)

    diff = {
        'rev': 0,
        'inventory': {
            1: {  # vehicle
                'compDescr': veh_compDescr,
                'shellsLayout': veh_shellsLayout,
                'shells': veh_shells,
                'crew': veh_crew,
                'repair': veh_repair,
                'eqsLayout': veh_eqsLayout,
                'eqs': veh_eqs,
                'settings': veh_settings,
                'lock': veh_lock,
            },
            2: {}, 3: {}, 4: {}, 5: {}, 6: {}, 7: {},
            8: {'compDescr': {}, 'vehicle': {}},   # tankman
            9: {}, 10: shell_inventory, 11: {},
        },
        # Stats._CACHE_STATS = ('battlesTillCaptcha',) в†’ СЃР°РјРµ Р·РІС–РґСЃРё
        # CaptchaUI С‡РёС‚Р°С” battlesTillCaptcha. РЇРєС‰Рѕ РЅРµ РїРѕРєР»Р°СЃС‚Рё, Stats.get
        # РїРѕРІРµСЂС‚Р°С” None, _battlesTillCaptcha=None в†’ `None <= 0` == True Сѓ
        # Python 2 в†’ isCaptchaRequired=True в†’ CAPTCHA Р·Р°РїСѓСЃРєР°С”С‚СЊСЃСЏ.
        'cache': {'vehsLock': {}, 'battlesTillCaptcha': 999},
        # Stats.synchronize в†’ diff['stats']:
        #   _SIMPLE_VALUE_STATS: credits, gold, slots, berths, freeXP, dossier,
        #     clanInfo, accOnline, accOffline, freeTMenLeft, freeVehiclesLeft,
        #     captchaTriesLeft, hasFinPassword, tkillIsSuspected
        #   _GROWING_SET_STATS: unlocks, eliteVehicles, doubleXPVehs (set update)
        # РЇРєС‰Рѕ РЅРµ РґР°С‚Рё 'slots'/'doubleXPVehs' вЂ” Stats.get('slots') РїРѕРІРµСЂС‚Р°С” None
        # в†’ Hangar.__updateVehicles РїР°РґР°С” Р· 'NoneType is unsubscriptable'.
        'stats': {
            'credits': 1000000000, 'gold': 1000000, 'freeXP': 1000000,
            'slots': slots_count, 'berths': 50,
            'vehTypeXP': {}, 'tankmen': {},
            'unlocks': set(), 'eliteVehicles': set(), 'doubleXPVehs': set(),
            'dossier': '', 'clanInfo': None,
            'accOnline': 0, 'accOffline': 0,
            'freeTMenLeft': 100, 'freeVehiclesLeft': slots_count - len(veh_list),
            'captchaTriesLeft': 5, 'hasFinPassword': False,
            'tkillIsSuspected': False,
        },
        'shop': {'rev': 0},
        # _ACCOUNT_STATS: clanDBID, attrs, premiumExpiryTime, autoBanTime,
        # restrictions в†’ РєРѕРїС–СЋСЋС‚СЊСЃСЏ РІ Stats.__cache.
        'account': {
            'attrs': 0, 'premiumExpiryTime': 0,
            'clanDBID': 0, 'autoBanTime': 0, 'restrictions': {},
        },
    }
    # protocol=2 (binary) СЃРєРѕСЂРѕС‡СѓС” pickle ~30% РїРѕСЂС–РІРЅСЏРЅРѕ Р· protocol=0 в†’
    # 1655B в†’ 1197B, РїРѕРјС–С‰Р°С”С‚СЊСЃСЏ Сѓ 1472B BigWorld Packet::MAX_SIZE.
    # cPickle.loads РЅР° РєР»С–С”РЅС‚С– Р°РІС‚РѕРјР°С‚РёС‡РЅРѕ РІРёР·РЅР°С‡Р°С” protocol.
    return pickle.dumps(diff, protocol=2)


_CACHED_SYNC_PICKLE = None
_CACHED_SYNC_BLOB = None
_CACHED_SHOP_BLOB = None


def get_sync_pickle() -> bytes:
    global _CACHED_SYNC_PICKLE
    if _CACHED_SYNC_PICKLE is None:
        _CACHED_SYNC_PICKLE = make_full_sync_pickle()
    return _CACHED_SYNC_PICKLE


def get_sync_blob() -> bytes:
    global _CACHED_SYNC_BLOB
    if _CACHED_SYNC_BLOB is None:
        _CACHED_SYNC_BLOB = zlib.compress(get_sync_pickle())
    return _CACHED_SYNC_BLOB


def make_empty_sync_pickle(prev_rev=0) -> bytes:
    return pickle.dumps({'rev': prev_rev, 'prevRev': prev_rev}, protocol=2)


def make_empty_ext_pickle() -> bytes:
    return pickle.dumps({}, protocol=0)


def make_shop_pickle() -> bytes:
    """Shop.__cache РїРѕРІРЅРёР№ СЃР»РѕРІРЅРёРє, С‰Рѕ Р±СѓРґРµ Р·Р°РїРёСЃР°РЅРёР№ РєР»С–С”РЅС‚РѕРј Сѓ
    `Shop.__cache = data` РїС–СЃР»СЏ onSyncStreamComplete (zlib + cPickle).
    requesters.py Р·Р°РїРёС‚СѓС” items РґР»СЏ РІСЃС–С… nation x itemType РїР°СЂ; СЏРєС‰Рѕ
    `__cache['items'][nation][itemType]` = None в†’ ShopParser.parseModules
    РІРїР°РґРµ Р· 'NoneType' is unsubscriptable."""
    # nation IDs Сѓ WoT 0.6.5 (res/scripts/common/nations.py):
    #   0=ussr, 1=germany, 2=usa, 15=NONE_INDEX (Р°СЂС‚РµС„Р°РєС‚Рё: optDev, equipment).
    # itemTypeID 1..11 (ITEM_TYPE_NAMES: vehicle, vehicleChassis ... equipment).
    NATION_IDS  = (0, 1, 2, 15)
    ITEM_TYPES  = tuple(range(1, 12))
    items = {n: {it: ({}, set()) for it in ITEM_TYPES} for n in NATION_IDS}
    for nation_id, prices in collect_shell_prices(load_all_vehicles()).items():
        if nation_id in items:
            items[nation_id][10] = (prices, set())

    shop = {
        'rev': 1,
        'slotsPrices':       (5, [300, 600, 900, 1200, 1500, 1800]),
        'berthsPrices':      (10, 8, [200, 400, 600, 800]),
        'exchangeRate':      400,
        'freeXPConversion':  (200, 25),
        'premiumCost':       {1: 250, 3: 600, 7: 1250, 14: 2500, 30: 4500},
        'tradeFees':         (0.0, 0.0),
        'tankmanCost':       [(0, 0, 0), (500, 0, 1), (0, 200, 2)],
        'paidRemovalCost':   5,
        'camouflageCost':    {0: [], 1: [], 2: []},
        'hornCost':          (5, 0),
        'passportChangeCost': 50,
        'items':             items,
        'sellPriceModif':    0.5,
    }
    return pickle.dumps(shop, protocol=2)


def get_shop_blob() -> bytes:
    """zlib(pickle(shop)) вЂ” СЏРє С‚РѕРіРѕ С‡РµРєР°С” SyncController.__onSyncStreamComplete."""
    global _CACHED_SHOP_BLOB
    if _CACHED_SHOP_BLOB is None:
        _CACHED_SHOP_BLOB = zlib.compress(make_shop_pickle())
    return _CACHED_SHOP_BLOB


def build_resource_header(stream_id: int, desc: bytes = b'') -> bytes:
    """resourceHeader (msgID 0x37): uint16 id + STRING desc."""
    payload = struct.pack('<H', stream_id) + bw_pack_string(desc)
    return msg_varlen(RESOURCE_HEADER_MSG_ID, payload)


def build_resource_fragment(stream_id: int, seq: int, flags: int,
                            data: bytes) -> bytes:
    """resourceFragment (msgID 0x38):
       uint16 rid + uint8 seq + uint8 flags + raw data (rest of packet)."""
    payload = struct.pack('<HBB', stream_id, seq, flags) + data
    return msg_varlen(RESOURCE_FRAGMENT_MSG_ID, payload)


def send_zlib_pickle_stream(sock, addr, sess, req_id: int, blob: bytes,
                            desc: bytes, label: str,
                            ext_pickle: bytes = None):
    if ext_pickle is None:
        ext_pickle = pickle.dumps({}, protocol=0)

    msgs = build_oncmdrespext(req_id, RES_STREAM, ext_pickle)
    msgs += build_resource_header(req_id, desc)
    pkt = build_channel_packet(msgs, sess, reliable=True)
    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
    try:
        sock.sendto(pkt, addr)
    except Exception:
        return

    max_frag = 1200
    chunks = [blob[i:i + max_frag] for i in range(0, len(blob), max_frag)] or [b'']
    for i, chunk in enumerate(chunks):
        flags = 1 if i == len(chunks) - 1 else 0
        msg = build_resource_fragment(req_id, i, flags, chunk)
        pkt = build_channel_packet(msg, sess, reliable=True)
        pkt = bw_encrypt_packet(pkt, sess['bf_key'])
        try:
            sock.sendto(pkt, addr)
        except Exception:
            return

    print(f"    [>] {label}: req={req_id}, blob={len(blob)}B, "
          f"fragments={len(chunks)}")


def send_sync_stream(sock, addr, sess, req_id: int):
    send_zlib_pickle_stream(sock, addr, sess, req_id, get_sync_blob(),
                            b'syncData', 'syncData STREAM')


def send_dossiers_stream(sock, addr, sess, req_id: int):
    blob = zlib.compress(pickle.dumps((1, []), protocol=2))
    send_zlib_pickle_stream(sock, addr, sess, req_id, blob,
                            b'dossiers', 'Dossiers STREAM')


def send_shop_stream(sock, addr, sess, req_id: int):
    """Р РµР°Р»С–Р·СѓС” BigWorld stream protocol РґР»СЏ Shop sync:
       1. cmd response Р· resultID=RES_STREAM (1) в†’ РєР»С–С”РЅС‚ РІРёРєР»РёРєР°С”
          `_subscribeForStream(requestID, callback)`.
       2. resourceHeader(streamID=req_id, desc='') вЂ” Р·Р°РґР°С” pDesc_.
       3. resourceFragment(rid=req_id, seq=0, flags=1, data=zlib(pickle(shop)))
          вЂ” flags=1 СЃС‚Р°РІРёС‚СЊ hasLast_=true в†’ DataDownload.complete()=true в†’
          Account.onStreamComplete(req_id, blob) в†’ SyncController.__onSync
          StreamComplete в†’ Shop.__onSyncComplete(syncID, data) в†’ cache=data."""
    ext = pickle.dumps({'shopRev': 1}, protocol=0)
    send_zlib_pickle_stream(sock, addr, sess, req_id, get_shop_blob(),
                            b'shop', 'Shop STREAM', ext)
    return
    blob = get_shop_blob()

    # 1. cmd response RES_STREAM
    ext = pickle.dumps({'shopRev': 1}, protocol=0)
    msgs = build_oncmdrespext(req_id, RES_STREAM, ext)
    # 2. resource header (РѕРїРёСЃ РїРѕСЂРѕР¶РЅС–Р№ вЂ” РєР»С–С”РЅС‚СЃСЊРєРёР№ onStreamComplete РЅРµ
    # РІРёРєРѕСЂРёСЃС‚РѕРІСѓС” desc РґР»СЏ Shop sync, Р°Р»Рµ pDesc_ != NULL РїРѕС‚СЂС–Р±РµРЅ РґР»СЏ
    # complete()=true).
    msgs += build_resource_header(req_id, b'shop')
    # 3. С”РґРёРЅРёР№ С„СЂР°РіРјРµРЅС‚ (flags=1 в†’ final). РЇРєС‰Рѕ blob > ~1300 Р±Р°Р№С‚ С‚СЂРµР±Р°
    # СЂРѕР·Р±РёС‚Рё РЅР° РєС–Р»СЊРєР° fragments (seq=0,1,2,..., РѕСЃС‚Р°РЅРЅС–Р№ Р· flags=1).
    MAX_FRAG = 1300
    chunks = [blob[i:i + MAX_FRAG] for i in range(0, len(blob), MAX_FRAG)] or [b'']
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        flags = 1 if is_last else 0
        msgs += build_resource_fragment(req_id, i, flags, chunk)

    pkt = build_channel_packet(msgs, sess, reliable=True)
    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
    try:
        sock.sendto(pkt, addr)
    except Exception:
        return
    print(f"    [>] Shop STREAM: req={req_id}, blob={len(blob)}B "
          f"in {len(chunks)} fragment(s)")


def build_account_event_noargs(msg_id: int) -> bytes:
    """Account.<event>() Р±РµР· Р°СЂРіСѓРјРµРЅС‚С–РІ вЂ” entityMessage Р· Р»РёС€Рµ EntityID(4B)."""
    em = struct.pack('<I', PLAYER_ENTITY_ID)
    return msg_varlen(msg_id, em)


# в”Ђв”Ђ Arena/Battle constants в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
# Arena GUI types (Р· res/scripts/common/constants.py):
#   0=UNKNOWN, 1=RANDOM, 2=TRAINING, 3=COMPANY, 4=TOURNAMENT.
ARENA_GUI_TYPE_RANDOM = 1
ARENA_PERIOD_PREBATTLE = 2
ARENA_PERIOD_BATTLE = 3
ARENA_UPDATE_VEHICLE_LIST = 1
ARENA_UPDATE_PERIOD = 3
ARENA_UPDATE_STATISTICS = 4
ARENA_UPDATE_AVATAR_READY = 7
# Arena type IDs Р· res/scripts/arena_defs/_list_.xml:
#   1=01_karelia, 2=02_malinovka, 4=04_himmelsdorf, 5=05_prohorovka,
#   6=06_ensk, 7=07_lakeville, 8=08_ruinberg, 10=10_hills, 11=11_murovanka,
#   13=13_erlenberg, 15=15_komarin, 18=18_cliff, 19=19_monastery,
#   23=23_westfeld, 28=28_desert, 29=29_el_hallouf, 34=34_redshire,
#   35=35_steppes, 37=37_caucasus, 38=38_mannerheim_line.
ARENA_TYPE_KARELIA   = 1
PREBATTLE_TIMER_SECONDS = 10
BATTLE_TIMER_SECONDS = 15 * 60
BATTLE_MOTION_TICK = 0.05
BATTLE_MAX_FORWARD_SPEED = 10.0
BATTLE_MAX_BACKWARD_SPEED = 4.0
BATTLE_MAX_ROTATION_SPEED = 1.15
BATTLE_PIVOT_ROTATION_SPEED = 1.35
BATTLE_ACCELERATION = 7.0
BATTLE_DECELERATION = 10.0
BATTLE_ROT_ACCELERATION = 4.0
BATTLE_ROT_DECELERATION = 5.5
BATTLE_MIN_TURN_FACTOR = 0.7

# Arena typeID в†’ geometry path (РёР· res/scripts/arena_defs/<typeName>.xml)
# РїР°СЂР°РјРµС‚СЂ <geometry>. Р‘РµР· trailing slash РєР»С–С”РЅС‚ СЃР°Рј РґРѕРґР°С”.
ARENA_GEOMETRY_PATH = {
    1:  b'spaces/01_karelia/',
    2:  b'spaces/02_malinovka/',
    4:  b'spaces/04_himmelsdorf/',
    5:  b'spaces/05_prohorovka/',
    6:  b'spaces/06_ensk/',
    7:  b'spaces/07_lakeville/',
    8:  b'spaces/08_ruinberg/',
    10: b'spaces/10_hills/',
    11: b'spaces/11_murovanka/',
    13: b'spaces/13_erlenberg/',
    15: b'spaces/15_komarin/',
    18: b'spaces/18_cliff/',
    19: b'spaces/19_monastery/',
    23: b'spaces/23_westfeld/',
    28: b'spaces/28_desert/',
    29: b'spaces/29_el_hallouf/',
    34: b'spaces/34_redshire/',
    35: b'spaces/35_steppes/',
    37: b'spaces/37_caucasus/',
    38: b'spaces/38_mannerheim_line/',
}

# 01_karelia spawn markers extracted from the original packed chunk files:
# res/spaces/01_karelia/*.chunk -> SpawnPoint/team=1/Transform.
# BigWorld chunk transform stores local coordinates; world position is
# chunkCoord * 100 + transform translation.
KARELIA_TEAM1_SPAWNS = [
    (-331.25, 41.75, -319.18),
    (-326.91, 41.16, -307.59),
    (-319.87, 40.48, -326.62),
    (-316.40, 39.80, -307.24),
    (-307.20, 39.12, -276.21),
    (-303.43, 38.88, -292.84),
    (-295.63, 38.48, -272.45),
    (-287.53, 38.22, -296.13),
    (-281.74, 38.11, -277.32),
]

ARENA_SPAWN_POS = {
    1:  KARELIA_TEAM1_SPAWNS[0],
    2:  (-350.0, 80.0, -350.0),
    4:  (-330.0, 80.0, -330.0),
    5:  (-360.0, 80.0, -360.0),
    6:  (-260.0, 80.0, -260.0),
    7:  (-350.0, 80.0, -350.0),
    8:  (-330.0, 80.0, -330.0),
    10: (-350.0, 80.0, -350.0),
    11: (-360.0, 80.0, -360.0),
    13: (-350.0, 80.0, -350.0),
    15: (-330.0, 80.0, -330.0),
    18: (-350.0, 80.0, -350.0),
    19: (-350.0, 80.0, -350.0),
    23: (-350.0, 80.0, -350.0),
    28: (-360.0, 80.0, -360.0),
    29: (-360.0, 80.0, -360.0),
    34: (-360.0, 80.0, -360.0),
    35: (-360.0, 80.0, -360.0),
    37: (-350.0, 80.0, -350.0),
    38: (-350.0, 80.0, -350.0),
}

# spaceData keys (Р· common/space_data_types.hpp)
SPACE_DATA_TOD_KEY                  = 0    # SpaceData_ToDData (8B: 2 floats)
SPACE_DATA_MAPPING_KEY_CLIENT_SERVER = 1   # 4x4 matrix + path в†’ addMapping
SPACE_DATA_MAPPING_KEY_CLIENT_ONLY   = 2


def build_space_data_message(space_id: int, key: int, data: bytes,
                             entry_id: bytes = b'\x00' * 8) -> bytes:
    """spaceData (msgID 0x07, varlen 2B). Р¤РѕСЂРјР°С‚ (server_connection.cpp:1845):
       SpaceID(4) + SpaceEntryID(8 = ip4+port2+salt2) + key(2) + data(rest).

       SpaceEntryID вЂ” С†Рµ Mercury::Address; РґР»СЏ РЅР°С€РѕС— "СЃРёРјСѓР»СЊРѕРІР°РЅРѕС—" РјР°РїРё
       РјРѕР¶РЅР° 8 РЅСѓР»СЊРѕРІРёС… Р±Р°Р№С‚ (СѓРЅС–РєР°Р»СЊРЅРёР№ key РґРѕСЃС‚Р°С‚РЅСЊРѕ С‚СЂРёРјР°С‚Рё РїРѕ key)."""
    payload = struct.pack('<I', space_id)
    payload += entry_id
    payload += struct.pack('<H', key)
    payload += data
    return msg_varlen(0x07, payload)


def build_geometry_mapping_data(path: bytes,
                                matrix=None) -> bytes:
    """Р¤РѕСЂРјР°С‚ SpaceData_MappingData (common/space_data_types.hpp):
       float matrix[4][4] (16 floats = 64B, identity РґР»СЏ РЅР°С€РѕРіРѕ РІРёРїР°РґРєСѓ)
       + char path[] (raw bytes Р±РµР· length prefix вЂ” read РґРѕ РєС–РЅС†СЏ stream)."""
    if matrix is None:
        # Identity matrix 4x4
        matrix = (1.0, 0.0, 0.0, 0.0,
                  0.0, 1.0, 0.0, 0.0,
                  0.0, 0.0, 1.0, 0.0,
                  0.0, 0.0, 0.0, 1.0)
    return struct.pack('<16f', *matrix) + path


def build_avatar_update_arena(update_type: int, data) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<B', update_type)
    em += bw_pack_string(pickle.dumps(data, protocol=2))
    return msg_varlen(AVATAR_UPDATEARENA_MSG_ID, em)


def build_avatar_update_own_vehicle_position(pos, yaw: float,
                                             speed: float = 0.0,
                                             rspeed: float = 0.0) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<fff', *pos)            # position VECTOR3
    em += struct.pack('<fff', yaw, 0.0, 0.0)   # direction VECTOR3: yaw,pitch,roll
    em += struct.pack('<ff', speed, rspeed)
    return msg_varlen(AVATAR_UPDATE_OWN_POSITION_MSG_ID, em)


def pack_direction3d(yaw: float, pitch: float = 0.0,
                     roll: float = 0.0) -> bytes:
    return struct.pack('<fff', roll, pitch, yaw)


def build_detailed_position(entity_id: int, pos, yaw: float) -> bytes:
    payload = struct.pack('<I', entity_id)
    payload += struct.pack('<fff', *pos)
    payload += pack_direction3d(yaw)
    return msg_fixed(CLIENT_DETAILED_POSITION_MSG_ID, payload)


def build_forced_position(entity_id: int, pos, yaw: float,
                          space_id: int = SPACE_ID,
                          vehicle_id: int = 0) -> bytes:
    payload = struct.pack('<I', entity_id)
    payload += struct.pack('<I', space_id)
    payload += struct.pack('<I', vehicle_id)
    payload += struct.pack('<fff', *pos)
    payload += pack_direction3d(yaw)
    return msg_fixed(CLIENT_FORCED_POSITION_MSG_ID, payload)


def build_control_entity(entity_id: int, enabled: bool) -> bytes:
    payload = struct.pack('<I', entity_id)
    payload += struct.pack('<B', 1 if enabled else 0)
    return msg_fixed(CLIENT_CONTROL_ENTITY_MSG_ID, payload)


def build_set_vehicle(passenger_id: int, vehicle_id: int) -> bytes:
    return msg_fixed(CLIENT_SET_VEHICLE_MSG_ID,
                     struct.pack('<II', passenger_id, vehicle_id))


def build_avatar_vehicle_bind(pos, yaw: float) -> bytes:
    return (
        build_set_vehicle(AVATAR_ENTITY_ID, PLAYER_VEHICLE_ID) +
        build_detailed_position(AVATAR_ENTITY_ID, (0.0, 0.0, 0.0), yaw) +
        build_detailed_position(PLAYER_VEHICLE_ID, pos, yaw)
    )


def build_battle_motion_sync(pos, yaw: float,
                             speed: float = 0.0,
                             rspeed: float = 0.0,
                             bind_avatar: bool = False) -> bytes:
    msgs = b''
    if bind_avatar:
        msgs += build_set_vehicle(AVATAR_ENTITY_ID, PLAYER_VEHICLE_ID)
    msgs += (
        build_forced_position(AVATAR_ENTITY_ID, (0.0, 0.0, 0.0), yaw,
                              space_id=SPACE_ID,
                              vehicle_id=PLAYER_VEHICLE_ID) +
        build_avatar_update_own_vehicle_position(pos, yaw, speed, rspeed) +
        build_vehicle_motion_update(pos, yaw)
    )
    return msgs


def build_battle_motion_tick(pos, yaw: float) -> bytes:
    return build_vehicle_motion_update(pos, yaw)


def angle_to_int8(angle: float) -> int:
    value = int(math.floor((angle * 128.0) / math.pi + 0.5))
    return ((value + 128) % 256) - 128


def half_angle_to_int8(angle: float) -> int:
    value = int(math.floor((angle * 254.0) / math.pi + 0.5))
    return max(-128, min(127, value))


def int8_to_angle(value: int) -> float:
    if value >= 128:
        value -= 256
    return (float(value) * math.pi) / 128.0


def int8_to_half_angle(value: int) -> float:
    if value >= 128:
        value -= 256
    return (float(value) * math.pi) / 254.0


def pack_yaw_pitch_roll(yaw: float, pitch: float = 0.0,
                        roll: float = 0.0) -> bytes:
    return struct.pack('<bbb',
                       angle_to_int8(yaw),
                       half_angle_to_int8(pitch),
                       angle_to_int8(roll))


def _pack_bw_3(value: int) -> bytes:
    # BigWorld stores PackedXZ internally with BW_PACK3 (high byte first), then
    # streams it through BW_HTON3_ASSIGN on little-endian clients. On the wire
    # that is low, middle, high byte order.
    return bytes((value & 0xff, (value >> 8) & 0xff, (value >> 16) & 0xff))


def pack_packed_xz(x_value: float, z_value: float) -> bytes:
    def bits(f):
        return struct.unpack('<I', struct.pack('<f', f))[0]

    add_values = (2.0, -2.0)
    x_uint = bits(x_value + add_values[1 if x_value < 0.0 else 0])
    z_uint = bits(z_value + add_values[1 if z_value < 0.0 else 0])

    result = 0
    if ((x_uint & 0x7c000000) != 0x40000000) or \
       ((x_uint & 0x03ffc000) == 0x03ffc000):
        result |= 0x7ff000
    if ((z_uint & 0x7c000000) != 0x40000000) or \
       ((z_uint & 0x03ffc000) == 0x03ffc000):
        result |= 0x0007ff

    result |= ((x_uint >> 3) & 0x7ff000) + ((x_uint & 0x4000) >> 2)
    result |= ((z_uint >> 15) & 0x0007ff) + ((z_uint & 0x4000) >> 14)
    result &= 0x7ff7ff
    result |= (x_uint >> 8) & 0x800000
    result |= (z_uint >> 20) & 0x000800
    return _pack_bw_3(result)


def pack_packed_y(y_value: float) -> bytes:
    y = y_value + (-2.0 if y_value < 0.0 else 2.0)
    y_uint = struct.unpack('<I', struct.pack('<f', y))[0]
    packed = (y_uint >> 12) & 0x7fff
    packed |= (y_uint >> 16) & 0x8000
    return struct.pack('>H', packed)


def pack_packed_xyz(pos) -> bytes:
    return pack_packed_xz(pos[0], pos[2]) + pack_packed_y(pos[1])


def build_vehicle_avatar_update(entity_id: int, pos, yaw: float) -> bytes:
    payload = struct.pack('<I', entity_id)
    payload += pack_packed_xyz(pos)
    payload += pack_yaw_pitch_roll(yaw, 0.0, 0.0)
    return msg_fixed(CLIENT_AVATAR_UPDATE_NOALIAS_FULLPOS_YPR_MSG_ID, payload)


def build_vehicle_motion_update(pos, yaw: float) -> bytes:
    return build_detailed_position(PLAYER_VEHICLE_ID, pos, yaw)


def decode_client_coord_ypr(payload: bytes, offset: int = 0):
    if len(payload) < offset + 15:
        return None
    pos = struct.unpack_from('<fff', payload, offset)
    yaw_b, pitch_b, roll_b = struct.unpack_from('<BBB', payload, offset + 12)
    return (
        pos,
        int8_to_angle(yaw_b),
        int8_to_half_angle(pitch_b),
        int8_to_angle(roll_b),
    )


def build_own_vehicle_motion_update(pos, yaw: float,
                                    speed: float = 0.0,
                                    rspeed: float = 0.0,
                                    force: bool = False) -> bytes:
    """Hard-sync the own camera matrix and the actual Vehicle entity.

    This is not used for every motion tick. updateOwnVehiclePosition resets the
    camera matrix provider before it reattaches to vehicle.matrix, so spamming it
    makes movement look stepped.
    """
    msgs = (
        build_avatar_update_own_vehicle_position(pos, yaw, speed, rspeed) +
        build_vehicle_motion_update(pos, yaw)
    )
    if force:
        msgs += build_forced_position(PLAYER_VEHICLE_ID, pos, yaw,
                                      space_id=SPACE_ID, vehicle_id=0)
    return msgs


def build_avatar_update_targeting_info(turret_yaw: float = 0.0,
                                       gun_pitch: float = 0.0,
                                       shot_mult: float = 1.0,
                                       aiming_time: float = 1.0) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<ffffff',
                      turret_yaw, gun_pitch,
                      8.0, 8.0,       # max turret/gun rotation speed
                      shot_mult, aiming_time)
    return msg_varlen(AVATAR_UPDATE_TARGETING_INFO_MSG_ID, em)


def build_avatar_update_gun_marker(shot_pos, shot_vec,
                                   dispersion_angle: float = 0.03) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<fff', *shot_pos)
    em += struct.pack('<fff', *shot_vec)
    em += struct.pack('<f', dispersion_angle)
    return msg_varlen(AVATAR_UPDATE_GUN_MARKER_MSG_ID, em)


def build_avatar_player_bundle(arena_type_id: int = ARENA_TYPE_KARELIA,
                               arena_gui_type: int = ARENA_GUI_TYPE_RANDOM,
                               weather_preset_id: int = 0,
                               vehicle_compact_descr: bytes = None,
                               spawn_pos=None,
                               initial_period: int = ARENA_PERIOD_PREBATTLE,
                               period_end_time: int = PREBATTLE_TIMER_SECONDS,
                               period_length: int = PREBATTLE_TIMER_SECONDS) -> bytes:
    """РџРµСЂРµС…С–Рґ Account в†’ Avatar РґР»СЏ РІС…РѕРґСѓ РІ Р±С–Р№.

    Bundle:
      1. resetEntities(keepPlayer=False) вЂ” Р·РІС–Р»СЊРЅСЏС” РїРѕС‚РѕС‡РЅСѓ Account player.
      2. createBasePlayer(Avatar entity) Р· 5 BASE_AND_CLIENT properties
         (РїРѕСЂСЏРґРѕРє Р· Avatar.def):
           name (STRING), arenaTypeID (INT32), arenaGuiType (UINT8),
           arenaExtraData (PYTHON), weatherPresetID (UINT8).
      3. createCellPlayer(Avatar) вЂ” РїРѕС‚СЂС–Р±РµРЅ Р±Рѕ Avatar РјР°С” CellMethods +
         Volatile.position. Р‘РµР· РЅСЊРѕРіРѕ РєР»С–С”РЅС‚ Р·Р°РІРёСЃР°С” / РїР°РґР°С” РІ C++
         Р±Рѕ РЅРµ РјР°С” cell counterpart РґРѕ СЏРєРѕРіРѕ РїСЂРёС…РѕРґСЏС‚СЊ РѕРЅРѕРІР»РµРЅРЅСЏ.
         Р¤РѕСЂРјР°С‚ (lib/connection/server_connection.cpp:1810):
           SpaceID(4) + vehicleID(4) + Position3D(3Г—float) +
           Direction3D(3Г—float yaw,pitch,roll) + cell+ownClient props.
         Avatar cell+ownClient props (РІ def order):
           state (UINT16) вЂ” РїСЂРёРїСѓСЃРєР°С”РјРѕ CELL_PUBLIC,
           team (UINT8, OWN_CLIENT),
           playerVehicleID (OBJECT_ID=uint32, OWN_CLIENT).

    Avatar.onBecomePlayer (scripts/client/Avatar.py:36) РѕРґСЂР°Р·Сѓ Р¶ СЂРѕР±РёС‚СЊ
       self.arena = ClientArena(arenaTypeID, arenaGuiType,
                                arenaExtraData, weatherPresetID)
    РґРµ ClientArena.__init__ РІРёРєР»РёРєР°С” ArenaType.g_cache.get(arenaTypeID),
    С‚РѕРјСѓ arenaTypeID РјСѓСЃРёС‚СЊ С–СЃРЅСѓРІР°С‚Рё РІ _list_.xml.
    """
    msgs = b''
    if spawn_pos is None:
        spawn_pos = ARENA_SPAWN_POS.get(arena_type_id, (-360.0, 80.0, -360.0))

    # вљ пёЏ resetEntities(False) РџР РР‘Р РђРќРћ вЂ” РІРёРєР»РёРєР°Р»Рѕ C++ crash (Р±РµР· python
    # traceback, РіСЂР° РїР°РґР°Р»Р° РїРѕРІРЅС–СЃС‚СЋ). РЈ WoT 0.6.5 createBasePlayer Р±РµР·
    # РїРѕРїРµСЂРµРґРЅСЊРѕРіРѕ reset РїСЂРѕСЃС‚Рѕ РїРµСЂРµРјРёРєР°С” player entity РЅР° РЅРѕРІСѓ; СЃС‚Р°СЂР°
    # Account entity (id=1) Р·Р°Р»РёС€Р°С”С‚СЊСЃСЏ РІ client entities map СЏРє non-player.

    # 2. createBasePlayer(Avatar)
    #    arenaExtraData РІРёРєРѕСЂРёСЃС‚РѕРІСѓС”С‚СЊСЃСЏ BattleLoadingPage С‚Р° СЂС–Р·РЅРёРјРё UI;
    #    РїРѕРєРё С‰Рѕ РїСѓСЃС‚РёР№ dict вЂ” РЅРµ РїРѕРІРёРЅРµРЅ Р»Р°РјР°С‚Рё Loading.
    arena_extra = pickle.dumps({}, protocol=0)

    props = b''
    props += bw_pack_string(b'qwerty')                  # name (STRING)
    props += struct.pack('<i', arena_type_id)           # arenaTypeID (INT32)
    props += struct.pack('<B', arena_gui_type)          # arenaGuiType (UINT8)
    props += bw_pack_string(arena_extra)                # arenaExtraData (PYTHON)
    props += struct.pack('<B', weather_preset_id)       # weatherPresetID (UINT8)

    cbp = struct.pack('<I', AVATAR_ENTITY_ID) + \
          struct.pack('<H', AVATAR_ENTITY_TYPE) + \
          props
    msgs += msg_varlen(0x05, cbp)

    # 2.5 spaceData(SPACE_DATA_MAPPING_KEY_CLIENT_SERVER) вЂ” РјР°РїРёС‚СЊ geometry
    #     РґРёСЂРµРєС‚РѕСЂС–СЋ (РЅР°РїСЂРёРєР»Р°Рґ "spaces/01_karelia/") Сѓ РЅР°С€ SpaceID. Р‘РµР· С†СЊРѕРіРѕ
    #     РєР»С–С”РЅС‚ РЅРµ РїРѕС‡РёРЅР°С” Р·Р°РІР°РЅС‚Р°Р¶СѓРІР°С‚Рё С‡Р°РЅРєРё, С‚РѕРјСѓ BigWorld.wg_prefetchSpaceZip
    #     callback `Avatar.onSpaceLoaded` РќР• СЃРїСЂР°С†СЊРѕРІСѓС” в†’ __stepsTillInit
    #     Р·Р°СЃС‚СЂСЏРіР°С” РЅР° 1 С– loading screen РЅРµ Р·Р°РєСЂРёРІР°С”С‚СЊСЃСЏ.
    #     РњР°С” Р№С‚Рё Р”Рћ createCellPlayer Р±Рѕ Avatar.onBecomePlayer РѕРґСЂР°Р·Сѓ С‡РёС‚Р°С”
    #     arena.typeDescriptor С– РїРѕС‡РёРЅР°С” prefetchSpaceZip.
    geometry_path = ARENA_GEOMETRY_PATH.get(
        arena_type_id, b'spaces/01_karelia/')
    mapping_data = build_geometry_mapping_data(geometry_path)
    # РЈРЅС–РєР°Р»СЊРЅРёР№ SpaceEntryID (Mercury::Address) вЂ” С‰РѕР± GeometryMapping РЅРµ
    # РґРµРґСѓРїР»С–РєСѓРІР°РІСЃСЏ РїСЂРё reuse. РўСЂРёРјР°С”РјРѕ РґРµС‚РµСЂРјС–РЅРѕРІР°РЅРѕ РЅР° РѕСЃРЅРѕРІС– space_id+key.
    entry_id = struct.pack('<IHH', SPACE_ID, 0, 1)   # ip=spaceID port=0 salt=1
    msgs += build_space_data_message(SPACE_ID,
                                     SPACE_DATA_MAPPING_KEY_CLIENT_SERVER,
                                     mapping_data,
                                     entry_id=entry_id)

    # 3. createCellPlayer(Avatar) вЂ” С‰РѕР± РєР»С–С”РЅС‚ РїРµСЂРµР№С€РѕРІ Р· Р·Р°СЃС‚Р°РІРєРё
    # Р·Р°РІР°РЅС‚Р°Р¶РµРЅРЅСЏ РІ СЂРµР°Р»СЊРЅРёР№ Р±С–Р№ (Avatar.onEnterWorld в†’ onEnterWorld).
    # Р¤РѕСЂРјР°С‚ (server_connection.cpp:1810):
    #   SpaceID(4) + vehicleID(4) + Position3D(3*float)
    #   + Direction3D(yaw,pitch,roll = 3*float) + cell_props.
    # cell_props (def-РїРѕСЂСЏРґРѕРє, Р»РёС€Рµ OWN_CLIENT РїРѕР»СЏ Avatar):
    #   team (UINT8) + playerVehicleID (OBJECT_ID=uint32).
    # Р’РђР–Р›РР’Рћ: playerVehicleID != 0 в†’ Avatar.onEnterWorld РІРёРєР»РёРєР°С”
    # set_playerVehicleID(0) (init step #1) + СЏРєС‰Рѕ Vehicle entity
    # PLAYER_VEHICLE_ID С–СЃРЅСѓС” С– inWorld вЂ” vehicle_onEnterWorld(own)
    # РїС–Р·РЅС–С€Рµ С‚РµР¶ РґР°С” init step #2. РўРѕРґС– Р· 4 РєСЂРѕРєС–РІ (1, 2, onEnterWorld,
    # onSpaceLoaded) Р·Р°РіСЂСѓР·РєР° Р·Р°РІРµСЂС€СѓС”С‚СЊСЃСЏ С– РїРѕС‡РёРЅР°С”С‚СЊСЃСЏ СЂРµР°Р»СЊРЅРёР№ Р±С–Р№.
    cell_props = b''
    cell_props += struct.pack('<B', 1)              # team  (UINT8)
    cell_props += struct.pack('<I', PLAYER_VEHICLE_ID)  # playerVehicleID

    ccp = struct.pack('<I', SPACE_ID)
    ccp += struct.pack('<I', 0)               # vehicleID РЅР° СЏРєРѕРјСѓ СЃС‚РѕС—С‚СЊ Avatar вЂ” 0
    ccp += struct.pack('<fff', *spawn_pos)    # position (x, y, z)
    ccp += struct.pack('<fff', 0.0, 0.0, 0.0) # direction (yaw, pitch, roll)
    ccp += cell_props
    msgs += msg_varlen(0x06, ccp)

    veh_info = (
        PLAYER_VEHICLE_ID, vehicle_compact_descr or get_vehicle_compact_descr(),
        'qwerty', 1, True, False, False, 1, '', 0, 0)
    msgs += build_avatar_update_arena(ARENA_UPDATE_VEHICLE_LIST, [veh_info])
    msgs += build_avatar_update_arena(ARENA_UPDATE_STATISTICS,
                                      [(PLAYER_VEHICLE_ID, 0)])
    msgs += build_avatar_update_arena(ARENA_UPDATE_PERIOD,
                                      (initial_period, period_end_time,
                                       period_length, None))

    # 4. enterAoI(Vehicle) Р”Рћ createEntity!
    #    EntityManager::onEntityCreate РїРµСЂРµРІС–СЂСЏС” unknownEntities_[id].count С–
    #    СЏРєС‰Рѕ С†Рµ 0 в†’ ERROR_MSG "didn't 'enter' before 'create'".
    #    РљРѕРјРµРЅС‚Р°СЂ Сѓ entity_manager.cpp:1869 РєР°Р¶Рµ:
    #      "we should call onEntityEnter before onEntityCreate, since the
    #       entities are not pre cached."
    #    Р¤РѕСЂРјР°С‚ (client_interface.hpp:93): EntityID(4) + IDAlias(uint8).
    msgs += msg_fixed(0x0A,
                      struct.pack('<IB', PLAYER_VEHICLE_ID, 0))

    # 5. createEntity(Vehicle) вЂ” СЃС‚РІРѕСЂСЋС” Vehicle entity Сѓ РєР»С–С”РЅС‚СЃСЊРєРѕРјСѓ
    #    entities map. Р¤РѕСЂРјР°С‚ (server_connection.cpp:1944, WoT 0.6.5 Р’РРљРћР РРЎРўРћР’РЈР„
    #    CompressionIStream вЂ” РїС–РґС‚РІРµСЂРґР¶РµРЅРѕ СЂСЏРґРєРѕРј
    #    "CompressionIStream::CompressionIStream: Invalid compression type: %d"
    #    Сѓ WorldOfTanks.exe). РўРѕРјСѓ payload РїРѕС‡РёРЅР°С”С‚СЊСЃСЏ Р· uint8 compressionType
    #    (0 = BW_COMPRESSION_NONE), С–РЅР°РєС€Рµ РєР»С–С”РЅС‚ РІРїР°РґРµ С‡РµСЂРµР· CRITICAL_MSG:
    #      uint8 compressionType (= 0 NONE)
    #      EntityID(4) + EntityTypeID(2) + Position3D(3*float)
    #      + yaw(int8) + pitch(int8) + roll(int8)
    #      + ALL_CLIENTS+CELL_PUBLIC properties (def order, pass 2).
    veh_compact_descr = vehicle_compact_descr or get_vehicle_compact_descr()
    msgs += build_vehicle_create_message(PLAYER_VEHICLE_ID,
                                         VEHICLE_ENTITY_TYPE,
                                         veh_compact_descr,
                                         pos=spawn_pos,
                                         team=1)
    return msgs


def _get_first_vehicle_compact_descr() -> bytes:
    """РџРѕРІРµСЂС‚Р°С” compactDescr РїРµСЂС€РѕРіРѕ С‚Р°РЅРєР° Р· _vehicles.json вЂ” С‰РѕР±
    Vehicle.publicInfo.compDescr РІС–РґРїРѕРІС–РґР°РІ СЂРµР°Р»СЊРЅРѕРјСѓ С‚Р°РЅРєСѓ РіСЂР°РІС†СЏ."""
    return get_vehicle_compact_descr()


def build_vehicle_create_message(vehicle_id: int, type_id: int,
                                 compact_descr: bytes,
                                 pos=(0.0, 0.0, 0.0),
                                 team: int = 1) -> bytes:
    """createEntity (msgID 0x08, varlen2). Р”РёРІРёСЃСЊ server_connection.cpp:1944.
    Property stream вЂ” С†Рµ 12 ALL_CLIENTS+CELL_PUBLIC РїРѕР»С–РІ Vehicle.def
    Сѓ def-РїРѕСЂСЏРґРєСѓ:
      1. publicInfo  (PUBLIC_VEHICLE_INFO FIXED_DICT: name, compDescr, team, prebattleID)
      2. health      (INT16)                ALL_CLIENTS
      3. isCrewActive(BOOL=UINT8)           ALL_CLIENTS
      4. engineMode  (ARRAY[2] UINT8)       ALL_CLIENTS
      5. damageStickers (ARRAY var UINT64)  ALL_CLIENTS
      6. publicStateModifiers (ARRAY var UINT8/EXTRA_ID)  ALL_CLIENTS
      7. status      (ARRAY[2] UINT8)       CELL_PUBLIC
      8. speeds      (ARRAY[2] FLOAT32)     CELL_PUBLIC
      9. invisibility(FLOAT32)              CELL_PUBLIC
     10. radioDistance(FLOAT32)             CELL_PUBLIC
     11. lastDamageTime(FLOAT64)            CELL_PUBLIC
     12. detectedVehicles(ARRAY var OBJECT_ID)  CELL_PUBLIC
    """
    # createEntity TAGGED stream (entity_type.cpp:427 newDictionary,
    # contents=TAGGED_CELL_ENTITY_DATA, allowOwnClientData=false):
    #   uint8 size
    #   { uint8 index + value-bytes } Г— size
    # index вЂ” С–РЅРґРµРєСЃ Сѓ СЃРїРёСЃРєСѓ clientServerProperty entity (С‚С–Р»СЊРєРё props
    # Р· ALL_CLIENTS / OTHER_CLIENTS РїСЂР°РїРѕСЂР°РјРё; CELL_PUBLIC С– OWN_CLIENT
    # С‚СѓС‚ РќР• РїСЂРёР№РјР°СЋС‚СЊСЃСЏ вЂ” Р»РёС€Рµ isOtherClientData=True).
    # Vehicle.def ALL_CLIENTS Сѓ def-РїРѕСЂСЏРґРєСѓ:
    #   idx=0 publicInfo (PUBLIC_VEHICLE_INFO FIXED_DICT)
    #   idx=1 health (INT16)
    #   idx=2 isCrewActive (BOOL)
    #   idx=3 engineMode (ARRAY[2] UINT8)
    #   idx=4 damageStickers (ARRAY var UINT64)
    #   idx=5 publicStateModifiers (ARRAY var UINT8)
    # Vehicle.prerequisites() РњРђР„ self.publicInfo.compDescr в†’ Р±РµР·
    # publicInfo Р±СѓРґРµ AttributeError Сѓ Python в†’ C++ crash. РўРѕРјСѓ С€Р»РµРјРѕ
    # С…РѕС‡Р° Р± publicInfo.

    # FIXED_DICT serialization: РїРѕР»СЏ Р±РµР· length prefix Сѓ def-order.
    # PUBLIC_VEHICLE_INFO = name(STRING) + compDescr(STRING) + team(UINT8)
    #                       + prebattleID(OBJECT_ID=uint32)
    public_info = b''
    public_info += bw_pack_string(b'qwerty')
    public_info += bw_pack_string(compact_descr)
    public_info += struct.pack('<B', team)
    public_info += struct.pack('<I', 0)       # prebattleID

    props = b''
    props += struct.pack('<B', 0)             # idx=0 (publicInfo)
    props += public_info
    props += struct.pack('<B', 1)             # idx=1 (health)
    props += struct.pack('<h', 1000)
    props += struct.pack('<B', 2)             # idx=2 (isCrewActive)
    props += struct.pack('<B', 1)
    props += struct.pack('<B', 3)             # idx=3 (engineMode)
    props += struct.pack('<BB', 1, 0)         # idle/started, no movement flags

    # CompressionIStream wrapper С‡РёС‚Р°С” 1-Р№ Р±Р°Р№С‚ СЏРє compression type.
    # 0 = BW_COMPRESSION_NONE (РґР°Р»С– raw stream Р±РµР· РґРµРєРѕРјРїСЂРµСЃС–С—).
    payload = struct.pack('<B', 0)            # compressionType = NONE
    payload += struct.pack('<I', vehicle_id)
    payload += struct.pack('<H', type_id)
    payload += struct.pack('<fff', *pos)
    payload += struct.pack('<bbb', 0, 0, 0)   # yaw, pitch, roll
    payload += struct.pack('<B', 4)           # publicInfo, health, crew, engineMode
    payload += props
    return msg_varlen(0x08, payload)


def pick_spawn_pos(arena_type_id: int, sess: dict):
    if arena_type_id == ARENA_TYPE_KARELIA:
        return KARELIA_TEAM1_SPAWNS[0]
    return ARENA_SPAWN_POS.get(arena_type_id, ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])


def current_server_time(sess: dict) -> int:
    zero_wall_time = sess.get('server_time_zero_wall')
    if zero_wall_time is None:
        return 0
    return max(0, int(time.time() - zero_wall_time))


def send_avatar_arena_update(sock, addr, sess, update_type: int, data, label: str):
    msg = build_avatar_update_arena(update_type, data)
    pkt = build_channel_packet(msg, sess, reliable=True)
    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
    try:
        sock.sendto(pkt, addr)
    except Exception:
        return
    print(f"    [>] Avatar.updateArena({label})")


def send_avatar_messages(sock, addr, sess, msgs: bytes, label: str,
                         reliable: bool = True):
    pkt = build_channel_packet(msgs, sess, reliable=reliable)
    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
    try:
        sock.sendto(pkt, addr)
    except Exception:
        return False
    if label:
        print(f"    [>] {label}")
    return True


def init_battle_state(sess: dict, spawn_pos):
    sess['battle_generation'] = sess.get('battle_generation', 0) + 1
    sess['battle_pos'] = tuple(float(v) for v in spawn_pos)
    sess['battle_yaw'] = 0.0
    sess['battle_speed'] = 0.0
    sess['battle_rspeed'] = 0.0
    sess['battle_target_speed'] = 0.0
    sess['battle_target_rspeed'] = 0.0
    sess['battle_last_motion_time'] = time.time()
    sess['battle_motion_flags'] = 0
    sess['battle_motion_loop_started'] = False
    sess['battle_client_control_enabled'] = False
    sess['server_vehicle_authoritative'] = True
    sess['battle_period_active'] = False
    sess['battle_turret_yaw'] = 0.0
    sess['battle_gun_pitch'] = 0.0
    sess['avatar_ready_sent'] = False
    sess['battle_period_timer_started'] = False


def approach(current: float, target: float, rate: float, dt: float) -> float:
    if current < target:
        return min(target, current + rate * dt)
    if current > target:
        return max(target, current - rate * dt)
    return current


def battle_motion_targets(flags: int):
    forward = bool(flags & 1)
    backward = bool(flags & 2)
    left = bool(flags & 4)
    right = bool(flags & 8)
    movement_dir = 1 if forward and not backward else -1 if backward and not forward else 0
    rotation_dir = -1 if left and not right else 1 if right and not left else 0
    speed_scale = 1.0
    if flags & 16:
        speed_scale = 0.5
    elif flags & 32:
        speed_scale = 0.25

    if movement_dir > 0:
        target_speed = BATTLE_MAX_FORWARD_SPEED * speed_scale
    elif movement_dir < 0:
        target_speed = -BATTLE_MAX_BACKWARD_SPEED * speed_scale
    else:
        target_speed = 0.0

    if movement_dir == 0:
        target_rspeed = BATTLE_PIVOT_ROTATION_SPEED * rotation_dir
    else:
        target_rspeed = BATTLE_MAX_ROTATION_SPEED * rotation_dir
    return target_speed, target_rspeed


def advance_battle_motion(sess: dict, flags: int = None):
    now = time.time()
    last = sess.get('battle_last_motion_time', now)
    dt = max(0.001, min(0.25, now - last))
    sess['battle_last_motion_time'] = now

    x, y, z = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    yaw = float(sess.get('battle_yaw', 0.0))
    if flags is None:
        flags = sess.get('battle_motion_flags', 0)

    target_speed, target_rspeed = battle_motion_targets(flags)
    speed = float(sess.get('battle_speed', 0.0))
    rspeed = float(sess.get('battle_rspeed', 0.0))

    speed_rate = BATTLE_ACCELERATION if abs(target_speed) > abs(speed) \
        else BATTLE_DECELERATION
    rspeed_rate = BATTLE_ROT_ACCELERATION if abs(target_rspeed) > abs(rspeed) \
        else BATTLE_ROT_DECELERATION
    speed = approach(speed, target_speed, speed_rate, dt)
    turn_factor = 1.0
    if abs(target_speed) > 0.01 and abs(speed) > 0.01:
        turn_factor = BATTLE_MIN_TURN_FACTOR
        turn_factor += (1.0 - BATTLE_MIN_TURN_FACTOR) * min(
            1.0, abs(speed) / max(0.01, abs(target_speed)))
    target_rspeed *= turn_factor
    rspeed = approach(rspeed, target_rspeed, rspeed_rate, dt)

    if abs(target_rspeed) < 0.001 and abs(rspeed) < 0.01:
        rspeed = 0.0
    yaw += rspeed * dt
    yaw = (yaw + math.pi) % (2.0 * math.pi) - math.pi
    if abs(speed) > 0.001:
        x += math.sin(yaw) * speed * dt
        z += math.cos(yaw) * speed * dt

    sess['battle_pos'] = (x, y, z)
    sess['battle_yaw'] = yaw
    sess['battle_speed'] = speed
    sess['battle_rspeed'] = rspeed
    sess['battle_target_speed'] = target_speed
    sess['battle_target_rspeed'] = target_rspeed
    return (x, y, z), yaw, speed, rspeed


def send_own_vehicle_position(sock, addr, sess, label: str = '',
                              reliable: bool = False, force: bool = False):
    pos = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    yaw = sess.get('battle_yaw', 0.0)
    speed = sess.get('battle_speed', 0.0)
    rspeed = sess.get('battle_rspeed', 0.0)
    return send_avatar_messages(
        sock, addr, sess,
        build_own_vehicle_motion_update(pos, yaw, speed, rspeed, force=force),
        label,
        reliable=reliable)


def enable_client_vehicle_control(sock, addr, sess, label: str = ''):
    if sess.get('battle_client_control_enabled'):
        return
    sess['battle_client_control_enabled'] = True
    msgs = build_control_entity(PLAYER_VEHICLE_ID, True)
    send_avatar_messages(sock, addr, sess, msgs,
                         label or "controlEntity(Vehicle, on)",
                         reliable=True)


def disable_client_vehicle_control_message(sess):
    sess['battle_client_control_enabled'] = False
    return build_control_entity(PLAYER_VEHICLE_ID, False)


def handle_client_avatar_update(sess: dict, msg_id: int, payload: bytes):
    if msg_id == 2:
        decoded = decode_client_coord_ypr(payload, 0)
        if decoded is None:
            return False
        pos, yaw, _pitch, _roll = decoded
        sess['avatar_pos'] = pos
        sess['avatar_yaw'] = yaw
        return True

    if msg_id == 3:
        if len(payload) < 25:
            return False
        pos, yaw, _pitch, _roll = decode_client_coord_ypr(payload, 8)
        sess['avatar_pos'] = pos
        sess['avatar_yaw'] = yaw
        return True

    if msg_id == 4:
        if len(payload) < 19:
            return False
        ward_id = struct.unpack_from('<I', payload, 0)[0]
        decoded = decode_client_coord_ypr(payload, 4)
        if decoded is None:
            return False
        pos, yaw, _pitch, _roll = decoded
        if ward_id == PLAYER_VEHICLE_ID:
            prev = sess.get('client_vehicle_pos')
            if prev is not None:
                dx = pos[0] - prev[0]
                dy = pos[1] - prev[1]
                dz = pos[2] - prev[2]
                if dx * dx + dy * dy + dz * dz > 0.0001:
                    sess['client_vehicle_last_move_time'] = time.time()
            sess['client_vehicle_pos'] = pos
            sess['client_vehicle_last_update_time'] = time.time()
            if not sess.get('server_vehicle_authoritative', True):
                sess['battle_pos'] = pos
                sess['battle_yaw'] = yaw
            count = sess.get('client_vehicle_update_count', 0) + 1
            sess['client_vehicle_update_count'] = count
            if count % 100 == 1:
                print(f"[<] client Vehicle#{ward_id} pos="
                      f"({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) "
                      f"yaw={yaw:.2f}")
        return True

    if msg_id == 5:
        if len(payload) < 28:
            return False
        ward_id = struct.unpack_from('<I', payload, 0)[0]
        decoded = decode_client_coord_ypr(payload, 12)
        if decoded is None:
            return False
        pos, yaw, _pitch, _roll = decoded
        if ward_id == PLAYER_VEHICLE_ID:
            prev = sess.get('client_vehicle_pos')
            if prev is not None:
                dx = pos[0] - prev[0]
                dy = pos[1] - prev[1]
                dz = pos[2] - prev[2]
                if dx * dx + dy * dy + dz * dz > 0.0001:
                    sess['client_vehicle_last_move_time'] = time.time()
            sess['client_vehicle_pos'] = pos
            sess['client_vehicle_last_update_time'] = time.time()
            if not sess.get('server_vehicle_authoritative', True):
                sess['battle_pos'] = pos
                sess['battle_yaw'] = yaw
        return True

    return False


def ensure_battle_motion_loop(sock, addr, sess):
    if sess.get('battle_motion_loop_started'):
        return
    sess['battle_motion_loop_started'] = True
    generation = sess.get('battle_generation', 0)

    def _tick():
        if generation != sess.get('battle_generation', 0):
            return
        flags = sess.get('battle_motion_flags', 0)
        speed = abs(float(sess.get('battle_speed', 0.0)))
        rspeed = abs(float(sess.get('battle_rspeed', 0.0)))
        if (not sess.get('battle_period_active') or
                (flags == 0 and speed < 0.01 and rspeed < 0.01)):
            sess['battle_motion_loop_started'] = False
            return

        client_last_move = sess.get('client_vehicle_last_move_time', 0.0)
        client_is_moving = (
            not sess.get('server_vehicle_authoritative', True) and
            flags != 0 and
            (time.time() - client_last_move) < 0.30
        )
        if client_is_moving:
            timer = threading.Timer(BATTLE_MOTION_TICK, _tick)
            timer.daemon = True
            timer.start()
            return

        pos, yaw, speed, rspeed = advance_battle_motion(sess, flags)
        count = sess.get('battle_move_tick_count', 0) + 1
        sess['battle_move_tick_count'] = count
        label = ''
        if count % 20 == 1:
            label = (f"ServerVehicle.tick(flags=0x{flags:02x}, "
                     f"pos=({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}), "
                     f"yaw={yaw:.2f})")
        send_avatar_messages(sock, addr, sess,
                             build_battle_motion_tick(pos, yaw),
                             label,
                             reliable=False)
        timer = threading.Timer(BATTLE_MOTION_TICK, _tick)
        timer.daemon = True
        timer.start()

    timer = threading.Timer(BATTLE_MOTION_TICK, _tick)
    timer.daemon = True
    timer.start()


def normalize_vec(vec):
    x, y, z = vec
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 0.0001:
        return (0.0, 0.0, 1.0)
    return (x / length, y / length, z / length)


def build_targeting_for_point(sess: dict, target_pos):
    pos = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    shot_pos = (pos[0], pos[1] + 2.0, pos[2])
    dx = target_pos[0] - shot_pos[0]
    dy = target_pos[1] - shot_pos[1]
    dz = target_pos[2] - shot_pos[2]
    shot_vec = normalize_vec((dx, dy, dz))
    turret_yaw = math.atan2(dx, dz)
    gun_pitch = math.atan2(dy, max(0.001, math.sqrt(dx * dx + dz * dz)))
    sess['battle_turret_yaw'] = turret_yaw
    sess['battle_gun_pitch'] = gun_pitch
    return (
        build_avatar_update_targeting_info(turret_yaw, gun_pitch) +
        build_avatar_update_gun_marker(shot_pos, shot_vec)
    )


def schedule_battle_period(sock, addr, sess):
    if sess.get('battle_period_timer_started'):
        return
    sess['battle_period_timer_started'] = True
    generation = sess.get('battle_generation', 0)

    def _start_battle():
        if generation != sess.get('battle_generation', 0):
            return
        now = current_server_time(sess)
        sess['battle_period_active'] = True
        sess['server_vehicle_authoritative'] = True
        sess['battle_client_control_enabled'] = False
        sess['battle_last_motion_time'] = time.time()
        msgs = build_avatar_update_arena(
            ARENA_UPDATE_PERIOD,
            (ARENA_PERIOD_BATTLE, now + BATTLE_TIMER_SECONDS,
             BATTLE_TIMER_SECONDS, None))
        msgs += build_battle_motion_sync(
            sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA]),
            sess.get('battle_yaw', 0.0),
            0.0, 0.0,
            bind_avatar=True)
        msgs += disable_client_vehicle_control_message(sess)
        send_avatar_messages(sock, addr, sess, msgs,
                             "PERIOD=BATTLE + server vehicle control")

    timer = threading.Timer(PREBATTLE_TIMER_SECONDS, _start_battle)
    timer.daemon = True
    timer.start()


def send_avatar_player(sock, addr, sess):
    """РЁР»Рµ РїРѕРІРЅРёР№ battle-bundle:
       createBasePlayer(Avatar) + createCellPlayer(Avatar, playerVehicleID=200)
       + createEntity(Vehicle 200) + enterAoI(Vehicle 200)."""
    battle_vehicle = sess.get('battle_vehicle') or get_vehicle_by_inventory_id(
        sess.get('battle_vehicle_inv_id', 1))
    veh_compact = (battle_vehicle or {}).get('compactDescr') or get_vehicle_compact_descr()
    arena_type_id = sess.get('battle_arena_type_id') or ARENA_TYPE_KARELIA
    if arena_type_id not in ARENA_GEOMETRY_PATH:
        arena_type_id = ARENA_TYPE_KARELIA
    spawn_pos = pick_spawn_pos(arena_type_id, sess)
    init_battle_state(sess, spawn_pos)
    now = current_server_time(sess)
    msgs = build_avatar_player_bundle(arena_type_id=arena_type_id,
                                      vehicle_compact_descr=veh_compact,
                                      spawn_pos=spawn_pos,
                                      initial_period=ARENA_PERIOD_PREBATTLE,
                                      period_end_time=now + PREBATTLE_TIMER_SECONDS,
                                      period_length=PREBATTLE_TIMER_SECONDS)
    pkt = build_channel_packet(msgs, sess, reliable=True)
    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
    try:
        sock.sendto(pkt, addr)
    except Exception:
        return
    sess['battle_bundle_sent'] = True
    print(f"    [>] battle-bundle: createBasePlayer(Avatar #{AVATAR_ENTITY_ID}) "
          f"+ spaceData(arenaType={arena_type_id}) "
          f"+ createCellPlayer(playerVeh=#{PLAYER_VEHICLE_ID}) + "
          f"enterAoI + createEntity(Vehicle, invID={sess.get('battle_vehicle_inv_id', 1)}, "
        f"spawn={spawn_pos}, health=1000, prebattle={PREBATTLE_TIMER_SECONDS}s)")


def send_avatar_ready_and_prebattle(sock, addr, sess):
    if sess.get('avatar_ready_sent'):
        return
    sess['avatar_ready_sent'] = True
    pos = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    yaw = sess.get('battle_yaw', 0.0)
    initial_target = (pos[0], pos[1] + 2.0, pos[2] + 100.0)
    msgs = b''
    msgs += build_avatar_update_arena(ARENA_UPDATE_AVATAR_READY, PLAYER_VEHICLE_ID)
    msgs += build_battle_motion_sync(pos, yaw, 0.0, 0.0,
                                     bind_avatar=True)
    msgs += build_targeting_for_point(sess, initial_target)
    send_avatar_messages(sock, addr, sess, msgs,
                         "Avatar ready + initial vehicle position/targeting")
    now = current_server_time(sess)
    send_avatar_arena_update(
        sock, addr, sess, ARENA_UPDATE_PERIOD,
        (ARENA_PERIOD_PREBATTLE, now + PREBATTLE_TIMER_SECONDS,
         PREBATTLE_TIMER_SECONDS, None),
        "PERIOD=PREBATTLE")
    schedule_battle_period(sock, addr, sess)


def send_account_event(sock, addr, sess, msg_id: int, label: str,
                       extra: bytes = b''):
    """Р’С–РґРїСЂР°РІР»СЏС” Account entity-method (Р±РµР· args Р°Р±Рѕ Р· extra payload)."""
    em = struct.pack('<I', PLAYER_ENTITY_ID) + extra
    msg = msg_varlen(msg_id, em)
    pkt = build_channel_packet(msg, sess, reliable=True)
    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
    try:
        sock.sendto(pkt, addr)
    except Exception:
        return
    print(f"    [>] {label}  (msgID=0x{msg_id:02x})")


AVATAR_BASE_METHOD_TRACK_POINT_WITH_GUN = 0x81
AVATAR_BASE_METHOD_VEHICLE_MOVE_WITH = 0xc3
AVATAR_BASE_METHOD_VEHICLE_SHOOT = 0xc1
AVATAR_BASE_METHOD_STOP_TRACKING_WITH_GUN = 0xc2
AVATAR_BASE_METHOD_CHANGE_SETTING = 0xc4
AVATAR_BASE_METHOD_TELEPORT = 0xc5
AVATAR_BASE_METHOD_USE_HORN = 0xc6
AVATAR_BASE_METHOD_ON_CLIENT_READY = 0xc7
AVATAR_BASE_METHODS = {
    AVATAR_BASE_METHOD_VEHICLE_MOVE_WITH,
    AVATAR_BASE_METHOD_VEHICLE_SHOOT,
    AVATAR_BASE_METHOD_TRACK_POINT_WITH_GUN,
    AVATAR_BASE_METHOD_STOP_TRACKING_WITH_GUN,
    AVATAR_BASE_METHOD_CHANGE_SETTING,
    AVATAR_BASE_METHOD_TELEPORT,
    AVATAR_BASE_METHOD_USE_HORN,
    AVATAR_BASE_METHOD_ON_CLIENT_READY,
}


def parse_exposed_request_id(payload: bytes):
    if len(payload) < 4:
        return None
    return struct.unpack_from('<I', payload, 0)[0]


def handle_avatar_base_method(sock, addr, sess, msg_id: int, payload: bytes):
    req_id = parse_exposed_request_id(payload)

    if msg_id == AVATAR_BASE_METHOD_ON_CLIENT_READY:
        print(f"    [avatar] onClientReady req={req_id}")
        send_avatar_ready_and_prebattle(sock, addr, sess)
        return True

    if msg_id == AVATAR_BASE_METHOD_VEHICLE_MOVE_WITH:
        if len(payload) >= 5:
            flags = payload[4]
        elif len(payload) >= 1:
            flags = payload[0]
        else:
            return True
        prev_flags = sess.get('battle_motion_flags', 0)
        was_idle = (
            prev_flags == 0 and
            abs(float(sess.get('battle_speed', 0.0))) < 0.01 and
            abs(float(sess.get('battle_rspeed', 0.0))) < 0.01
        )
        sess['battle_motion_flags'] = flags
        if flags != 0 and was_idle:
            sess['battle_last_motion_time'] = time.time()
        move_count = sess.get('battle_move_update_count', 0) + 1
        sess['battle_move_update_count'] = move_count
        if move_count % 20 == 1 or flags == 0:
            mode = "server authoritative" if sess.get(
                'server_vehicle_authoritative', True) else "client controlled"
            move, turn = battle_motion_targets(flags)
            print(f"    [>] Vehicle.input(flags=0x{flags:02x}) "
                  f"[{mode}, target=({move:.1f},{turn:.2f})]")
        if not sess.get('server_vehicle_authoritative', True):
            return True
        if flags != 0:
            ensure_battle_motion_loop(sock, addr, sess)
        elif abs(float(sess.get('battle_speed', 0.0))) > 0.01 or \
                abs(float(sess.get('battle_rspeed', 0.0))) > 0.01:
            ensure_battle_motion_loop(sock, addr, sess)
        return True

    if msg_id == AVATAR_BASE_METHOD_TRACK_POINT_WITH_GUN:
        if len(payload) >= 16 and struct.unpack_from('<I', payload, 0)[0] == PLAYER_VEHICLE_ID:
            target_pos = struct.unpack_from('<fff', payload, 4)
        elif len(payload) >= 12:
            target_pos = struct.unpack_from('<fff', payload, 0)
        else:
            return True
        msgs = build_targeting_for_point(sess, target_pos)
        send_avatar_messages(
            sock, addr, sess, msgs,
            f"Avatar.targeting(point=({target_pos[0]:.1f},"
            f"{target_pos[1]:.1f},{target_pos[2]:.1f}))",
            reliable=False)
        return True

    if msg_id == AVATAR_BASE_METHOD_STOP_TRACKING_WITH_GUN:
        pos = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
        yaw = sess.get('battle_yaw', 0.0)
        target_pos = (pos[0] + math.sin(yaw) * 100.0,
                      pos[1] + 2.0,
                      pos[2] + math.cos(yaw) * 100.0)
        send_avatar_messages(sock, addr, sess,
                             build_targeting_for_point(sess, target_pos),
                             "Avatar.stopTrackingWithGun -> forward marker",
                             reliable=False)
        return True

    if msg_id in (AVATAR_BASE_METHOD_VEHICLE_SHOOT,
                  AVATAR_BASE_METHOD_CHANGE_SETTING,
                  AVATAR_BASE_METHOD_TELEPORT,
                  AVATAR_BASE_METHOD_USE_HORN):
        print(f"    [avatar] method=0x{msg_id:02x} req={req_id} "
              f"payload={payload.hex()}")
        return True

    return False


def handle_account_doCmd(sock, addr, sess, msg_id: int, payload: bytes):
    """Р РѕР·РіР°Р»СѓР¶РµРЅРЅСЏ Р·Р° cmd:
       - cmd=300 (CMD_SYNC_SHOP)        в†’ stream Р· shop dict
       - cmd=700 (CMD_ENQUEUE_FOR_ARENA) в†’ onEnqueued event (no response)
       - cmd=701 (CMD_DEQUEUE)           в†’ onDequeued event (no response)
       - С–РЅС€С–                            в†’ onCmdResponseExt(success, full_sync)"""
    req_id, cmd = parse_doCmd_request(msg_id, payload)
    if req_id is None:
        return
    print(f"    [doCmd] msg=0x{msg_id:02x} reqID={req_id} cmd={cmd}")

    if cmd == CMD_SYNC_DATA:
        if sess.get('sync_data_stream_sent'):
            msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_sync_pickle(0))
            pkt = build_channel_packet(msg, sess, reliable=True)
            pkt = bw_encrypt_packet(pkt, sess['bf_key'])
            try:
                sock.sendto(pkt, addr)
            except Exception:
                return
            print(f"    [>] onCmdResponseExt(req={req_id}, res=0, ext=empty_sync)")
            return
        sess['sync_data_stream_sent'] = True
        send_sync_stream(sock, addr, sess, req_id)
        return

    if cmd == CMD_SYNC_SHOP:
        send_shop_stream(sock, addr, sess, req_id)
        return

    if cmd == CMD_SYNC_DOSSIERS:
        send_dossiers_stream(sock, addr, sess, req_id)
        return

    if cmd == CMD_ENQUEUE_FOR_ARENA:
        args = parse_doCmd_int3(payload) or (1, 0, 0)
        veh_inv_id, arena_type_id, queue_type = args
        vehicle = get_vehicle_by_inventory_id(veh_inv_id)
        if vehicle is None:
            vehicle = get_vehicle_by_inventory_id(1)
            veh_inv_id = 1
        sess['battle_vehicle_inv_id'] = veh_inv_id
        sess['battle_vehicle'] = vehicle
        sess['battle_arena_type_id'] = arena_type_id if arena_type_id else ARENA_TYPE_KARELIA
        sess['battle_queue_type'] = queue_type
        print(f"    [battle] queued invID={veh_inv_id} "
              f"name={vehicle.get('name') if vehicle else 'unknown'} "
              f"arenaType={sess['battle_arena_type_id']} queueType={queue_type}")

        # REQUEST_ID_NO_RESPONSE в†’ cmd response РЅРµ РїРѕС‚СЂС–Р±РµРЅ. РќР°С‚РѕРјС–СЃС‚СЊ С€Р»РµРјРѕ
        # entity event onEnqueued, РїС–СЃР»СЏ СЏРєРѕРіРѕ РєР»С–С”РЅС‚ РїРѕРєР°Р·СѓС” "РЈ С‡РµСЂР·С–...".
        send_account_event(sock, addr, sess,
                           ACCOUNT_ONENQUEUED_MSG_ID, "Account.onEnqueued()")

        # РЎРёРјСѓР»СЏС†С–СЏ "Р·РЅР°Р№РґРµРЅРѕ Р±С–Р№" вЂ” С‡РµСЂРµР· 1.5 СЃ С€Р»РµРјРѕ onArenaCreated,
        # РѕРґСЂР°Р·Сѓ Р·Р° РЅРёРј РїРµСЂРµС…С–Рґ Account в†’ Avatar (resetEntities +
        # createBasePlayer(Avatar)). Avatar.onBecomePlayer СЃС‚РІРѕСЂРёС‚СЊ
        # ClientArena С– Р·Р°РїСѓСЃС‚РёС‚СЊ Р·Р°РІР°РЅС‚Р°Р¶РµРЅРЅСЏ РєР°СЂС‚Рё.
        def _simulate_arena_created():
            try:
                send_account_event(sock, addr, sess,
                                   ACCOUNT_ONARENACREATED_MSG_ID,
                                   "Account.onArenaCreated()  [simulated]")
            except Exception as e:
                print(f"    [!] simulate_arena_created error: {e}")

        def _switch_to_avatar():
            try:
                send_avatar_player(sock, addr, sess)
            except Exception as e:
                print(f"    [!] switch_to_avatar error: {e}")

        threading.Timer(1.5, _simulate_arena_created).start()
        threading.Timer(2.0, _switch_to_avatar).start()
        return

    if cmd == CMD_DEQUEUE:
        send_account_event(sock, addr, sess,
                           ACCOUNT_ONDEQUEUED_MSG_ID, "Account.onDequeued()")
        return

    if cmd == 0 and sess.get('battle_bundle_sent'):
        msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_ext_pickle())
        pkt = build_channel_packet(msg, sess, reliable=True)
        pkt = bw_encrypt_packet(pkt, sess['bf_key'])
        try:
            sock.sendto(pkt, addr)
        except Exception:
            return
        print(f"    [>] onCmdResponseExt(req={req_id}, res=0, ext=empty) "
              f"[Avatar.onClientReady]")
        send_avatar_ready_and_prebattle(sock, addr, sess)
        return

    msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_ext_pickle())
    pkt = build_channel_packet(msg, sess, reliable=True)
    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
    try:
        sock.sendto(pkt, addr)
    except Exception:
        return
    print(f"    [>] onCmdResponseExt(req={req_id}, res=0, ext=empty)")

def send_init_bundle(sock, addr, sess):
    if sess.get('init_sent'):
        return

    token = sess.get('token')
    if not token:
        return

    session_key_int = struct.unpack('<I', token)[0]
    init_msgs = build_init_bundle(session_key_int)
    init_pkt = build_channel_packet(init_msgs, sess, reliable=True)
    init_pkt = bw_encrypt_packet(init_pkt, sess['bf_key'])
    sock.sendto(init_pkt, addr)
    sess['init_sent'] = True
    sess['server_time_zero_wall'] = time.time()
    print(f"[>] Init bundle: authenticate+bandwidth+setGameTime"
          f"+resetEntities+createBasePlayer+showGUI(0x90)")

    if not sess.get('tick_started'):
        start_tick_thread(sock, addr, sess)
        sess['tick_started'] = True

# в”Ђв”Ђ tickSync loop в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

tick_counter = 0

def start_tick_thread(sock, addr, sess=None):
    """Send tickSync (MsgID 0x0D, 1 byte tick counter) every 100 ms."""
    global tick_counter
    def _loop():
        global tick_counter
        while True:
            time.sleep(0.1)
            tick_byte = tick_counter & 0xFF
            tick_counter += 1
            pkt = msg_fixed(0x0D, struct.pack('<B', tick_byte))
            if sess:
                pkt = build_channel_packet(pkt, sess, reliable=False)
                pkt = bw_encrypt_packet(pkt, sess['bf_key'])
            else:
                pkt = make_bundle(pkt)
            try: sock.sendto(pkt, addr)
            except: break
    t = threading.Thread(target=_loop, daemon=True)
    t.start()

# в”Ђв”Ђ LoginApp handler в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def handle_loginapp(sock):
    try:
        data, addr = sock.recvfrom(4096)
    except ConnectionResetError:
        return
    print(f"\n[+] LoginApp: {len(data)} Р±Р°Р№С‚ РІС–Рґ {addr}")
    reply_id = data[5:9]

    dec = find_rsa_block(data)
    if not dec:
        print("[-] RSA decrypt failed"); return

    username, password, bf_key = parse_logon_params(dec)
    if not username: return

    token = os.urandom(4)
    active_sessions[token] = {'bf_key': bf_key, 'addr': None}
    print(f"[+] Р›РѕРіС–РЅ: '{username}' | Token: {token.hex()}")

    # LoginReplyRecord: Mercury::Address(ip 4B + port 2B + salt 2B) + sessionKey(4B) = 12 B
    record  = socket.inet_aton('127.0.0.1')
    record += struct.pack('>H', BASEAPP_PORT)
    record += b'\x00\x00'            # salt (Mercury::Address.salt)
    record += token
    enc = bw_bf_encrypt(record, bf_key)

    resp_payload = reply_id + b'\x01' + enc   # status=LOGGED_ON(0x01)
    resp = b'\x00\x00\xff' + struct.pack('<I', len(resp_payload)) + resp_payload
    sock.sendto(resp, addr)
    print(f"[>] LOGGED_ON РІС–РґРїСЂР°РІР»РµРЅРѕ, РєР»С–С”РЅС‚ С–РґРµ РЅР° BaseApp:{BASEAPP_PORT}")

# в”Ђв”Ђ BaseApp handler в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

baseapp_clients = {}   # addr в†’ session info/state

def handle_baseapp(sock):
    try:
        data, addr = sock.recvfrom(4096)
    except ConnectionResetError:
        return
    if len(data) < 4: return

    # РџС–СЃР»СЏ СѓСЃРїС–С€РЅРѕРіРѕ login РєР»С–С”РЅС‚ РјРѕР¶Рµ РїРµСЂРµР№С‚Рё РЅР° Р·Р°С€РёС„СЂРѕРІР°РЅС– РїР°РєРµС‚Рё.
    sess_for_addr = baseapp_clients.get(addr)
    decrypted = False
    if sess_for_addr:
        dec = bw_decrypt_packet(data, sess_for_addr['bf_key'])
        if dec:
            data = dec
            decrypted = True
            if len(data) < 4:
                return

    body = data
    flags = struct.unpack('<H', data[:2])[0]
    in_seq = None

    if decrypted:
        body, flags, in_seq, _ = split_packet_body(data)
        if len(body) < 3:
            return

        # РџСЂРѕСЃС‚Рѕ РѕРЅРѕРІР»СЋС”РјРѕ in_seq state. ACK РїС–РіРіС–Р±РµРєР°С”РјРѕ РІ РЅР°СЃС‚СѓРїРЅРёР№
        # РІРёС…С–РґРЅРёР№ РїР°РєРµС‚ (init bundle Р°Р±Рѕ tickSync) вЂ” РѕРєСЂРµРјРёР№ ACK-РїР°РєРµС‚
        # Р·Р±РёРІР°С” РІР»Р°СЃРЅСѓ РЅСѓРјРµСЂР°С†С–СЋ out_seq.
        if sess_for_addr and (flags & PACKET_FLAG_ON_CHANNEL) and (in_seq is not None):
            update_in_seq_state(sess_for_addr, in_seq)

    msg_id = body[2]

    # в”Ђв”Ђ baseAppLogin (MsgID 0x00) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    if (not decrypted) and body[:2] == b'\x01\x00' and msg_id == 0x00 and len(body) >= 15:
        reply_id       = body[5:9]
        token_received = body[11:15]
        print(f"\n[+] BaseApp: baseAppLogin РІС–Рґ {addr} | Token: {token_received.hex()}")

        sess = active_sessions.get(token_received)
        if not sess:
            # РЎРїСЂРѕР±СѓС”РјРѕ Р·РЅР°Р№С‚Рё Р·Р° Р±СѓРґСЊ-СЏРєРёРј РєР»СЋС‡РµРј (РЅР° РІРёРїР°РґРѕРє СЂС–Р·РЅРёС… Р·СЃСѓРІС–РІ)
            for k, v in active_sessions.items():
                if token_received in (k, k[::-1]):
                    sess = v; break

        if not sess:
            print(f"[-] РќРµРІС–РґРѕРјРёР№ Token! Р”Р°РјРї: {data.hex()}")
            return

        sess['addr'] = addr
        sess['token'] = token_received
        # РљРѕР¶РµРЅ РЅРѕРІРёР№ baseAppLogin в†’ РЅРѕРІРёР№ channel, СЃРєРёРґР°С”РјРѕ СѓСЃС– counters,
        # С–РЅР°РєС€Рµ РїРѕРІС‚РѕСЂРЅРµ РїС–РґРєР»СЋС‡РµРЅРЅСЏ РїС–РґРµ Р· seq=N+1, Р° РєР»С–С”РЅС‚ С‡РµРєР°С” 0.
        sess['init_sent'] = False
        sess['tick_started'] = False
        sess['in_seq_at'] = 0
        sess['in_seq_buffered'] = set()
        sess['out_channel_seq'] = 0
        sess['out_nub_seq'] = 0
        baseapp_clients[addr] = sess

        # 1) Р’С–РґРїРѕРІС–РґСЊ РЅР° baseAppLogin: РїСЂРѕСЃС‚Рѕ echo token СЏРє SessionKey
        reply = make_reply(reply_id, token_received)
        reply = bw_encrypt_packet(reply, sess['bf_key'])
        sock.sendto(reply, addr)
        print(f"[>] baseAppLogin Reply РІС–РґРїСЂР°РІР»РµРЅРѕ")

        # 2) Init РІС–РґРїСЂР°РІРёРјРѕ РїС–СЃР»СЏ РїРµСЂС€РѕРіРѕ enableEntities/authenticate РІС–Рґ РєР»С–С”РЅС‚Р°.

    # в”Ђв”Ђ С–РЅС€С– РїРѕРІС–РґРѕРјР»РµРЅРЅСЏ РІС–Рґ РєР»С–С”РЅС‚Р° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    else:
        if decrypted and sess_for_addr:
            messages = list(iter_baseapp_ext_messages(body))

            for m, p, _ in messages:
                if m in (0x02, 0x03, 0x04, 0x05):
                    handle_client_avatar_update(sess_for_addr, m, p)

            # Spam filter: client sends authenticate + avatar/vehicle movement
            # updates almost every frame. Print only occasional samples.
            movement_msg_ids = (0x01, 0x02, 0x03, 0x04, 0x05)
            avatar_update_only = all(m in movement_msg_ids for m, _, _ in messages) \
                and any(m in (0x02, 0x03, 0x04, 0x05) for m, _, _ in messages)
            if avatar_update_only:
                cnt = sess_for_addr.get('avatar_update_count', 0) + 1
                sess_for_addr['avatar_update_count'] = cnt
                if cnt % 100 == 1:
                    summary = ", ".join(f"0x{m:02x}({len(p)}B)"
                                        for m, p, _ in messages)
                    print(f"[<] movementUpdate x{cnt}: {summary}")
            else:
                summary = ", ".join(f"0x{m:02x}({len(p)}B)" for m, p, _ in messages)
                print(f"[<] BaseAppExt: {summary or 'none'} | "
                      f"flags=0x{flags:04x} seq={in_seq} body={body.hex()}")

            if any(m in (0x01, 0x0A) for m, _, _ in messages) and not sess_for_addr.get('init_sent'):
                send_init_bundle(sock, addr, sess_for_addr)
                return

            # Р‘СѓРґСЊ-СЏРєРёР№ РЅР°СЃС‚СѓРїРЅРёР№ РїР°РєРµС‚ РїС–СЃР»СЏ init вЂ“ РґР°РјРїРёРјРѕ + РІС–РґРїРѕРІС–РґР°С”РјРѕ
            # РЅР° exposed Account base-methods (doCmdStr/Int3/Int4/Int2Str/IntArr).
            for m, p, _ in messages:
                if m in movement_msg_ids:
                    continue
                print(f"    [post-init] msg=0x{m:02x} payload={p.hex()}")
                if sess_for_addr.get('battle_bundle_sent') and m in AVATAR_BASE_METHODS:
                    if handle_avatar_base_method(sock, addr, sess_for_addr, m, p):
                        continue
                if m in ACCOUNT_DOCMD_MSG_IDS:
                    handle_account_doCmd(sock, addr, sess_for_addr, m, p)
            return

        print(f"[!!!] BaseApp РїР°РєРµС‚ MsgID=0x{msg_id:02x} ({len(body)}B): {body.hex()}")

# в”Ђв”Ђ Main loop в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def main():
    login_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    login_sock.bind(('0.0.0.0', LOGIN_PORT))

    base_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    base_sock.bind(('0.0.0.0', BASEAPP_PORT))

    print(f"[*] WoT 0.6.5 Emulator | LoginApp:{LOGIN_PORT} | BaseApp:{BASEAPP_PORT}")
    print("[*] Р—Р°РїСѓСЃРєР°Р№ РіСЂСѓ С– С‚РёСЃРЅРё Connect!\n")

    while True:
        readable, _, _ = select.select([login_sock, base_sock], [], [], 1.0)
        for s in readable:
            if s is login_sock:
                try:
                    handle_loginapp(s)
                except ConnectionResetError:
                    continue
            elif s is base_sock:
                try:
                    handle_baseapp(s)
                except ConnectionResetError:
                    continue

if __name__ == '__main__':
    main()

